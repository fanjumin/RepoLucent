# -*- coding: utf-8 -*-
"""C / C++ / Arduino 词法前端（纯标准库、零依赖、离线）。

定位：给 RepoLucent 加一套 **C 家族**（``.h .hpp .hh .hxx .c .cpp .cc .cxx .ino .pde``）
的解析前端，产出与 :mod:`repo_lucent.py_ast` **同构的"每文件事实"字典**，从而复用
既有的 模块识别 / 符号倒排索引 / 依赖图 / 报告 / serve —— Python 分析是"前端 A"，
本模块是"前端 B"，下游一视同仁。

诚实边界（务必知悉）：
- 这是**词法/正则级**启发式，不是 clang 级 AST：不做预处理展开、模板实例化、
  跨 TU 类型解析。故 ``#include`` 依赖、类/结构体/函数**声明**提取可靠；
  宏内/条件编译内的定义可能漏；函数体"调用图"不做（C++ 调用解析噪声大），
  C 家族的依赖图以 **include 边**为真实信号（嵌入式工程本就以头文件依赖论架构）。
- 与 Python 前端一样只吐**事实**，不产出建议。

设计约束：
- 输出 entry 的键是 py_ast entry 的超集（额外 ``includes``/``defines``/``lang``），
  缺失字段用同构默认值，使 symbol_index / dep_graph / core_analyzer 无需分支即可消费。
- 逐文件、无跨文件 IO、无第三方 import；结果确定（列表按出现序，不做隐式排序，
  与 py_ast 一致交由上层排序）。
"""
from __future__ import annotations

import re

from .fs_scan import count_lines, decode_text

#: 本前端负责的源文件扩展名（含 Arduino/Processing）。与 config.CXX_EXTS 单一来源保持一致由 config 定义，
#: 这里独立持有以免循环导入（config 只放常量集合）。
CXX_EXTS = frozenset({
    ".h", ".hpp", ".hh", ".hxx", ".inl",
    ".c", ".cpp", ".cc", ".cxx", ".ino", ".pde",
})

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*([<"])([^">]+)[">]', re.M)
_DEFINE_RE = re.compile(r'^\s*#\s*define\s+\w+', re.M)
# class / struct：捕获名字与可选基类（到 '{' 或换行前）
_CLASS_RE = re.compile(
    r'^\s*(?:template\s*<[^>]*>\s*)?(class|struct)\s+([A-Za-z_]\w*)'
    r'(?:\s*:\s*([^{;]+))?\s*[{;]?', re.M)
# 函数定义启发式：某行起始（顶格或浅缩进）以 返回类型 + 名字(args) 收尾于 '{' 或 'const{'
# 支持类外定义 `Ret Type::name(args)`。刻意保守，宁漏不误。
_FUNC_RE = re.compile(
    r'^(?!\s*[A-Za-z_~]\w*\s*::\s*\w*\s*\()([A-Za-z_][\w:<>,\s\*&]*?)\b'
    r'([A-Za-z_~]\w*(?:::[A-Za-z_~]\w*)*)\s*\(([^;{)]*)\)\s*(?:const\s*)?(?:override\s*)?\{',
    re.M)
# 前置声明（无函数体）不计为"定义"，避免把 header 里的原型当实现。

_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT_RE = re.compile(r'//[^\n]*')


def _strip_comments(text: str) -> str:
    """把块/行注释替换成等量空格，保持行列号不变（供函数签名/统计用）。"""
    def _blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    text = _BLOCK_COMMENT_RE.sub(_blank, text)
    # 行注释：保留换行，仅去掉 // 到行尾
    text = _LINE_COMMENT_RE.sub(lambda m: '', text)
    return text


def _first_block_line(text: str):
    """文件顶部块注释或首个 // 注释的首行，作为 file docstring。"""
    m = re.match(r'\s*/\*\*?(.*?)\*/', text, re.S)
    if m:
        body = [ln.strip(' *\t') for ln in m.group(1).splitlines()]
        body = [ln for ln in body if ln]
        return (body[0][:200] if body else None)
    m = re.search(r'^\s*//\s*(.+)$', text, re.M)
    return m.group(1).strip()[:200] if m else None


def _parse_class(name: str, kind: str, bases_raw, lineno: int, body: str):
    bases = []
    if bases_raw:
        for b in bases_raw.split(','):
            b = re.sub(r'\b(public|protected|private|virtual)\b', '', b).strip()
            b = b.split('<')[0].strip()          # 去模板参数
            if b:
                bases.append(b.split('::')[-1])   # 取末段名
    methods = []
    for f in _FUNC_RE.finditer(body):
        mname = f.group(2).split('::')[-1]
        if mname == name:                         # 构造/析构名同名也留（去掉~）
            pass
        methods.append({"name": mname, "signature": f"{mname}({f.group(3).strip()})",
                        "docstring": None, "abstract": '= 0' in f.group(0),
                        "decorators": [], "lineno": f.group(0).count('\n') + lineno})
    return {"name": name, "kind": kind, "bases": bases, "methods": methods,
            "abstract_methods": [m["name"] for m in methods if m["abstract"]],
            "inherits_base_plugin": False, "docstring": None, "lineno": lineno,
            "_body_spans": None}


def parse_cxx_text(rel: str, text: str) -> dict:
    """把 C 家族源码文本解析成 py_ast 兼容事实字典（不读盘，供 worker 复用）。"""
    loc_total, loc_code = count_lines(text)
    clean = _strip_comments(text)
    result = {
        "path": rel, "lang": "c",
        "syntax_ok": True, "error": None,
        "loc_total": loc_total, "loc_code": loc_code,
        "docstring": _first_block_line(text), "docstring_full": None,
        "imports": [], "import_facts": [], "calls": [],
        "classes": [], "functions": [], "blueprints": [], "routes": [],
        "includes": [], "defines": len(_DEFINE_RE.findall(text)),
    }

    # includes（区分系统 <> 与本地 ""）
    for m in _INCLUDE_RE.finditer(text):
        sep, target = m.group(1), m.group(2).strip()
        result["includes"].append({"target": target, "system": sep == "<"})
        if not result["includes"][-1]["system"]:
            # 以头文件名（去扩展名、去目录）当"模块名"，供 core import_roots 聚合
            stem = target.replace('\\', '/').split('/')[-1]
            stem = re.sub(r'\.(h|hpp|hh|hxx|inl)$', '', stem, flags=re.I)
            if stem:
                result["imports"].append(stem)

    # class / struct：记录起点，body 取到下一个 class 之前的粗略范围
    class_matches = list(_CLASS_RE.finditer(clean))
    for i, m in enumerate(class_matches):
        start = m.start()
        lineno = clean.count('\n', 0, start) + 1
        body_end = class_matches[i + 1].start() if i + 1 < len(class_matches) else len(clean)
        c = _parse_class(m.group(2), m.group(1), m.group(3), lineno, clean[start:body_end])
        result["classes"].append({k: v for k, v in c.items() if k != "_body_spans"})

    # 顶层函数定义（非类内）：在全量 clean 上匹配，排除落在 class body 内的
    class_spans = []
    for m in class_matches:
        body_end = len(clean)
        # 简单起见：把 class 关键字之后到文件尾的 '{' 配对粗略划区间不必精确，
        # 这里以"顶格函数（无行首缩进）"过滤，减少把类内方法重复计为自由函数
        _ = body_end
    for m in _FUNC_RE.finditer(clean):
        # 只认"顶格或极浅缩进"的定义，模拟命名空间作用域函数；避免类内成员重复计
        line_start = clean.rfind('\n', 0, m.start()) + 1
        indent = m.start() - line_start
        if indent > 2:
            continue
        name = m.group(2).split('::')[-1]
        lineno = clean.count('\n', 0, m.start()) + 1
        if name in ("if", "for", "while", "switch", "return", "catch"):
            continue
        result["functions"].append({
            "name": name, "signature": m.group(0).split('{')[0].strip()[:160],
            "docstring": None, "decorators": [],
            "public": not name.startswith("_"), "lineno": lineno,
        })
    return result


def parse_cxx_file(fpath, rel: str, raw=None) -> dict:
    """解析单个 C 家族文件（worker 入口，与 py_ast.parse_python_file 同构）。"""
    from pathlib import Path
    if raw is None:
        try:
            raw = Path(fpath).read_bytes()
        except OSError:
            raw = b""
    text = decode_text(raw)
    try:
        return parse_cxx_text(rel, text)
    except Exception as e:      # noqa: BLE001 —— 单文件解析失败不中断整体
        result = parse_cxx_text.__wrapped__(rel, "") if hasattr(parse_cxx_text, "__wrapped__") else {
            "path": rel, "lang": "c", "syntax_ok": False, "error": f"{type(e).__name__}: {e}",
            "loc_total": 0, "loc_code": 0, "docstring": None, "docstring_full": None,
            "imports": [], "import_facts": [], "calls": [], "classes": [],
            "functions": [], "blueprints": [], "routes": [], "includes": [], "defines": 0}
        return result


def is_cxx(rel_path) -> bool:
    return str(rel_path).lower().endswith(tuple(CXX_EXTS))
