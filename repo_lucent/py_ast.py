# -*- coding: utf-8 -*-
"""Python AST 解析：类 / 函数签名 / Flask Blueprint 路由 / 导入关系。

只依赖标准库 ast；解析失败的文件不中断整体流程，而是记录 syntax_ok=False。

v1.6.0（阶段一 1-C）：类 / 函数 / 路由三项事实补 `lineno` 字段（定义行号）。
符号倒排索引（symbol_index.py）依赖该字段输出「符号 → file:line」定位，
从而在不引入任何二次解析的前提下支撑 repo.search。字段为 MINOR 新增，
消费方忽略未知字段即可；AST 缓存 entry 结构因此变化，CACHE_VERSION 同步递增。

v1.7.0（阶段二 2-A）：新增进程池并行解析（`parse_files_parallel` / `_parse_one`）。
Windows spawn 三条铁律全部满足：worker 为顶层函数；只接收 (绝对路径, 相对路径)
字符串元组，绝不传递不可 pickle 对象（文件内容由 worker 自行读取，主进程零 pickle
负载）；入口 `repolucent.py` / `repo_lucent/__main__.py` 均具备
`if __name__ == "__main__"` 守卫。并行侧任何异常都自动串行兜底——优化可以失效，
正确性不可以。
"""
from __future__ import annotations

import ast
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .cache import cache_key
from .fs_scan import MAX_TEXT_BYTES, count_lines, decode_text


def _up(node) -> str:
    """ast.unparse 的容错封装，超长截断。"""
    try:
        s = ast.unparse(node)
    except Exception:
        s = "<unparse-failed>"
    return s if len(s) <= 80 else s[:77] + "..."


def _doc_first(node) -> str | None:
    """取节点 docstring 首行（去掉多余空白）。"""
    ds = ast.get_docstring(node)
    if not ds:
        return None
    line = ds.strip().splitlines()[0].strip()
    return line[:200]


def _format_signature(node) -> str:
    """把函数/方法签名渲染为可读字符串（含默认值与注解）。"""
    try:
        a = node.args
        parts: list[str] = []

        def fmt(arg, default):
            s = arg.arg
            if getattr(arg, "annotation", None) is not None:
                s += ": " + _up(arg.annotation)
            if default is not None:
                s += (" = " if getattr(arg, "annotation", None) else "=") + _up(default)
            return s

        posonly = list(getattr(a, "posonlyargs", []) or [])
        args = list(a.args)
        allpos = posonly + args
        defaults = list(a.defaults or [])
        pad = [None] * (len(allpos) - len(defaults)) + defaults
        for arg, d in zip(allpos, pad):
            parts.append(fmt(arg, d))
        if posonly:
            parts.insert(len(posonly), "/")
        if a.vararg:
            parts.append("*" + a.vararg.arg)
        elif a.kwonlyargs:
            parts.append("*")
        for arg, d in zip(a.kwonlyargs, a.kw_defaults or []):
            if d is not None:
                parts.append(fmt(arg, d))
            else:
                s = arg.arg
                if getattr(arg, "annotation", None) is not None:
                    s += ": " + _up(arg.annotation)
                parts.append(s)
        if a.kwarg:
            parts.append("**" + a.kwarg.arg)
        ret = " -> " + _up(node.returns) if node.returns is not None else ""
        return "(" + ", ".join(parts) + ")" + ret
    except Exception:
        return "(...)"


def _decorator_names(node) -> list[str]:
    out = []
    for dec in node.decorator_list:
        out.append(_up(dec))
    return out


def _is_abstract(node) -> bool:
    for dec in node.decorator_list:
        name = dec.id if isinstance(dec, ast.Name) else (
            dec.attr if isinstance(dec, ast.Attribute) else "")
        if name in ("abstractmethod", "abstractproperty"):
            return True
    return False


def _extract_routes(tree: ast.Module) -> tuple[list[dict], list[dict]]:
    """提取 Flask Blueprint 定义与 @<var>.route(...) 路由声明。"""
    blueprints: list[dict] = []
    routes: list[dict] = []

    for node in ast.walk(tree):
        # x = Blueprint("name", __name__, url_prefix=...)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            val = node.value
            if isinstance(val, ast.Call):
                fn = val.func
                is_bp = (isinstance(fn, ast.Name) and fn.id == "Blueprint") or \
                        (isinstance(fn, ast.Attribute) and fn.attr == "Blueprint")
                if is_bp:
                    bp = {"var": node.targets[0].id, "name": "", "url_prefix": ""}
                    if val.args:
                        bp["name"] = _up(val.args[0])
                    for kw in val.keywords:
                        if kw.arg == "url_prefix":
                            bp["url_prefix"] = _up(kw.value)
                    blueprints.append(bp)

    def bp_prefix(var: str) -> str:
        for b in blueprints:
            if b["var"] == var:
                return b["url_prefix"]
        return ""

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr != "route":
                continue
            if not isinstance(dec.func.value, ast.Name):
                continue
            owner = dec.func.value.id
            rule = _up(dec.args[0]) if dec.args else ""
            methods = ["GET"]
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [ast.literal_eval(e) if isinstance(e, ast.Constant) else _up(e)
                               for e in kw.value.elts]
            routes.append({
                "bp": owner,
                "url_prefix": bp_prefix(owner),
                "rule": rule,
                "methods": sorted(set(str(m).upper() for m in methods)),
                "endpoint": node.name,
                # v1.6.0：装饰器所修饰函数（即路由处理函数）的定义行号
                "lineno": getattr(node, "lineno", 0),
            })
    return blueprints, routes


def _collect_imports(tree: ast.Module) -> list[str]:
    """收集模块级导入的 dotted 模块名（from X import 与 import X.Y）。"""
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:  # 相对导入
                mods.add(node.module)
            elif node.module:
                mods.add(node.module)
    return sorted(mods)


def parse_python_file(fpath: Path, rel: str, raw: bytes | None = None) -> dict:
    """解析单个 Python 文件，返回结构化摘要。

    `raw` 可传入已读取的原始字节，供并行 worker 复用（避免重复读盘）；
    为 None 时自行读取。两条路径的解码与截断口径完全一致（同一 MAX_TEXT_BYTES
    与 decode_text），因此串行/并行产出的 entry 逐字节等价。
    """
    if raw is None:
        try:
            raw = fpath.read_bytes()[:MAX_TEXT_BYTES]
        except OSError:
            raw = b""
    text = decode_text(raw)
    # 与 scan_overview 共用 count_lines()，确保 LOC 口径一致
    # （scan_overview 在命中 parse_cache 时直接复用这两个值）。
    loc_total, loc_code = count_lines(text)
    result = {
        "path": rel,
        "syntax_ok": True,
        "error": None,
        "loc_total": loc_total,
        "loc_code": loc_code,
        "docstring": None,
        "docstring_full": None,
        "imports": [],
        "classes": [],
        "functions": [],
        "blueprints": [],
        "routes": [],
    }
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError as e:
        result["syntax_ok"] = False
        result["error"] = f"SyntaxError: {e.msg} (line {e.lineno})"
        return result
    except (ValueError, RecursionError) as e:
        result["syntax_ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    result["docstring"] = _doc_first(tree)
    result["docstring_full"] = ast.get_docstring(tree)
    result["imports"] = _collect_imports(tree)
    result["blueprints"], result["routes"] = _extract_routes(tree)

    for node in tree.body:  # 只取顶层，保持接口清单聚焦
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append({
                "name": node.name,
                "signature": "def " + node.name + _format_signature(node),
                "docstring": _doc_first(node),
                "decorators": _decorator_names(node),
                "public": not node.name.startswith("_"),
                "lineno": getattr(node, "lineno", 0),          # v1.6.0 符号定位
            })
        elif isinstance(node, ast.ClassDef):
            methods = []
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": m.name,
                        "signature": m.name + _format_signature(m),
                        "docstring": _doc_first(m),
                        "abstract": _is_abstract(m),
                        "decorators": _decorator_names(m),
                        "lineno": getattr(m, "lineno", 0),
                    })
            bases = [_up(b).rsplit(".", 1)[-1] for b in node.bases]
            result["classes"].append({
                "name": node.name,
                "bases": bases,
                "docstring": _doc_first(node),
                "methods": methods,
                "abstract_methods": [m["name"] for m in methods if m["abstract"]],
                "inherits_base_plugin": any(b.endswith("BasePlugin") for b in bases),
                "lineno": getattr(node, "lineno", 0),          # v1.6.0 符号定位
            })
    return result


# ------------------------------------------------- 并行解析（阶段二 2-A） ----

#: 低于该文件数不做并行——进程启动与 pickle 开销大于收益（方案 2-A 约定）。
PARALLEL_MIN_FILES = 200

#: 并行自动档的进程数上限。2 核机器不会被 4 进程打满，且避免 spawn 代价失控。
PARALLEL_AUTO_CAP = 4


def resolve_workers(workers: int | None) -> int:
    """把 settings / CLI 的 workers 语义解析为实际进程数。

    - `None` / `0` → auto：`min(cpu_count or 2, 4)`
    - `1` / 负数   → 串行（1 是兼容开关，也是并行不可用时的兜底路径）
    - `n > 1`      → 显式进程数

    非法值一律按「最安全解释」处理为串行，绝不因配置写错而抛异常。
    """
    if workers is None or workers == 0:
        return max(1, min(os.cpu_count() or 2, PARALLEL_AUTO_CAP))
    try:
        n = int(workers)
    except (TypeError, ValueError):
        return 1
    return 1 if n < 1 else n


def _failed_entry(rel: str, err: str) -> dict:
    """worker 内兜底：返回与 parse_python_file 失败态**同构**的 entry。

    形状与正常返回完全一致，下游（缓存写入 / 各分析器 / 符号索引）无需分支处理。
    与既有容错语义一致：syntax_ok=False + error 文本，不抛异常、不中断整体流程。
    """
    return {
        "path": rel,
        "syntax_ok": False,
        "error": err,
        "loc_total": 0,
        "loc_code": 0,
        "docstring": None,
        "docstring_full": None,
        "imports": [],
        "classes": [],
        "functions": [],
        "blueprints": [],
        "routes": [],
    }


def _parse_one(item: tuple[str, str]) -> tuple[str, dict, str | None]:
    """进程池 worker（**必须顶层**，Windows spawn 下需按限定名 pickle）。

    只接收 (绝对路径, 相对路径) 两个字符串，文件内容由 worker 自行读取——
    主进程零 pickle 负载，也绝不向子进程传递任何不可序列化对象。

    返回 `(相对路径, entry, 内容哈希)`。读取失败时哈希为 None，
    调用方据此按 miss 落缓存（下次运行仍会重试该文件）。
    """
    abs_path, rel = item
    try:
        raw = Path(abs_path).read_bytes()[:MAX_TEXT_BYTES]
    except OSError as e:
        return rel, _failed_entry(rel, f"OSError: {e}"), None
    try:
        return rel, parse_python_file(Path(abs_path), rel, raw=raw), cache_key(raw)
    except Exception as e:      # noqa: BLE001 —— worker 是进程边界，必须兜住一切异常
        return rel, _failed_entry(rel, f"{type(e).__name__}: {e}"), cache_key(raw)


def parse_files_parallel(items, workers: int | None = None,
                         min_files: int = PARALLEL_MIN_FILES
                         ) -> list[tuple[str, dict, str | None]]:
    """批量解析 Python 文件：小批量或 `workers == 1` 走串行，否则走进程池。

    `items`：可迭代的 `(绝对路径, 相对路径)` 字符串元组序列。
    返回值顺序与传入顺序**严格一致**（`ex.map` 保序），因此调用方可以
    依赖它与 `iter_repo_files()` 的遍历顺序对齐。

    任何并行侧异常（受限环境禁用子进程、资源不足、pickle 失败等）都回退串行，
    保证「优化可以失效，正确性不可以」。
    """
    batch = list(items)
    if not batch:
        return []
    w = resolve_workers(workers)
    if w == 1 or len(batch) < min_files:
        return [_parse_one(it) for it in batch]
    try:
        with ProcessPoolExecutor(max_workers=w) as ex:
            return list(ex.map(_parse_one, batch, chunksize=16))
    except Exception:           # noqa: BLE001 —— 并行不可用时静默降级为串行
        return [_parse_one(it) for it in batch]
