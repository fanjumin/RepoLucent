# -*- coding: utf-8 -*-
"""AST 符号倒排索引：把 py_ast 已产出的逐文件事实编译成「符号 → 位置」索引。

定位对标（升级方案 1-C）：
- Aider repomap（tree-sitter 符号图，无 embedding 即覆盖大部分「定位类」需求）；
- Sourcegraph SCIP 精确符号图的**本地化最小版**。

硬约束与设计取舍：
- **零新增解析成本**：输入全部来自 parse_cache（py_ast.parse_python_file 的产物），
  本模块不做任何文件 IO、不 import ast、不重新解析源码。
- **只输出事实**：索引条目仅含 file / line / kind / owner / symbol，
  不产出任何建议（约束 3「事实与判断分离」）。
- **路径归一**：仓库内相对路径统一转 POSIX 斜杠。分析器产出的 file 字段沿用
  操作系统原生分隔符（Windows 反斜杠），若直接进索引会让产物随平台漂移、无法 diff；
  索引层归一后，产物确定性不依赖运行平台。
- **owner 派生**：`plugins/<id>/... → <id>`，其余取顶层目录名，作为「归属」事实，
  便于 Agent 收敛检索范围。派生逻辑只读路径，不依赖任何附加元数据。
- 主 JSON 只放轻量摘要段（count + index_file 指针），完整索引落独立文件，
  避免把已 700KB 级的事实源继续撑大。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ARTIFACT_SYMBOLS, config

#: 索引结构版本号。改动条目结构时必须递增。
SYMBOL_INDEX_SCHEMA = 1

#: 索引文件顶层段名
_SECTION = "symbols"


def _posix(path: str) -> str:
    """仓库相对路径 → POSIX 斜杠形式（跨平台确定性产物的前提）。"""
    return str(path or "").replace("\\", "/").lstrip("./")


def _owner_of(rel_posix: str, plugins_dir: str) -> str:
    """从相对路径派生归属：插件目录取插件 identifier，其余取顶层目录。"""
    parts = [p for p in rel_posix.split("/") if p]
    if not parts:
        return "<root>"
    if len(parts) > 1 and plugins_dir and parts[0] == plugins_dir:
        # plugins/<id>/x.py → <id>；plugins/_base/x.py → _base（框架资源同样作为归属）
        return parts[1]
    return parts[0] if len(parts) > 1 else "<root>"


def _route_symbol(rule) -> str:
    """把路由 rule 规范成可检索的符号名。

    py_ast 的 rule 由 `ast.unparse` 产出，字符串常量会带引号（`'/admin/demo/ping'`）。
    直接拿它当检索键时，用户按 `/admin/demo/ping` 检索只能走子串回退；索引层去掉
    成对引号后，URL 即符号名，精确命中可用。（其余产品仍保留带引号的 rule 原文，
    本索引是新增产物，不改变既有字段语义。）
    """
    s = str(rule or "").strip()
    for q in ("'", '"'):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            return s[1:-1]
    return s


def _add(index: dict, name: str, rel_posix: str, lineno, kind: str, owner: str) -> None:
    if not name:
        return
    try:
        line = int(lineno or 0)
    except (TypeError, ValueError):
        line = 0
    index.setdefault(str(name), []).append({
        "file": rel_posix,
        "line": line,
        "kind": kind,
        "owner": owner,
    })


def build_symbol_index(file_facts, cfg=None) -> dict:
    """把逐文件解析事实编译成倒排索引。

    file_facts 可为：
      - parse_cache（dict，键=相对路径，值=parse_python_file 的产物），或
      - 上述产物的可迭代序列（每次迭代取 entry["path"] 作相对路径）。

    返回 {"schema": 1, "count": N, "symbols": {name: [条目...]}}，
    其中 symbols 按符号名排序，保证产物逐字节确定。
    """
    facts = file_facts.values() if isinstance(file_facts, dict) else file_facts
    plugins_dir = getattr(config, "PLUGINS_DIR", "") or ""

    index: dict[str, list[dict]] = {}
    for f in facts:
        if not isinstance(f, dict):
            continue
        rel = _posix(f.get("path") or "")
        if not rel:
            continue
        owner = _owner_of(rel, plugins_dir)
        for c in (f.get("classes") or []):
            if isinstance(c, dict):
                _add(index, c.get("name"), rel, c.get("lineno"), "class", owner)
        for fn in (f.get("functions") or []):
            if isinstance(fn, dict):
                _add(index, fn.get("name"), rel, fn.get("lineno"), "function", owner)
        for rt in (f.get("routes") or []):
            if isinstance(rt, dict):
                # 路由本身即"可检索符号"：以 rule 为符号名，便于按 URL 定位处理函数
                _add(index, _route_symbol(rt.get("rule")), rel, rt.get("lineno"),
                     "route", owner)

    # 同符号内的条目排序，保证确定性（不同符号间由 dict(sorted(...)) 保证）
    for entries in index.values():
        entries.sort(key=lambda e: (e["file"], e["line"], e["kind"]))

    symbols = {k: index[k] for k in sorted(index)}
    return {"schema": SYMBOL_INDEX_SCHEMA, "count": len(symbols), _SECTION: symbols}


# --------------------------------------------------------------- 检索 ----

#: 检索结果上限（超出置 truncated=true，提示调用方收窄关键词）
DEFAULT_LIMIT = 50


def search_symbols(index: dict, symbol: str, limit: int = DEFAULT_LIMIT) -> dict:
    """检索符号：精确命中优先 → 大小写不敏感子串回退。

    返回只含事实字段：symbol（命中的符号名）/ file / line / kind / owner。
    同一符号多次定义时按 file、line 排序；结果超限时截断并置 truncated。
    """
    syms = (index or {}).get(_SECTION) or {}
    q = str(symbol or "").strip()
    try:
        cap = int(limit) if limit else DEFAULT_LIMIT
    except (TypeError, ValueError):
        cap = DEFAULT_LIMIT
    cap = max(1, min(cap, 500))

    if not q:
        return {"symbol": q, "count": 0, "truncated": False, "hits": []}

    hits: list[dict] = []
    mode = "exact"
    for ent in syms.get(q) or []:
        hits.append({"symbol": q, **ent})
    if not hits:
        mode = "substring"
        ql = q.lower()
        for name in sorted(syms):                 # 排序保证跨平台确定性
            if ql in name.lower():
                for ent in syms[name]:
                    hits.append({"symbol": name, **ent})

    hits.sort(key=lambda h: (h["file"], h["line"], h["kind"], h["symbol"]))
    total = len(hits)
    return {
        "symbol": q,
        "match": mode,
        "count": total,
        "truncated": total > cap,
        "hits": hits[:cap],
    }


def summary(index: dict, filename: str = ARTIFACT_SYMBOLS) -> dict:
    """主 JSON 的 symbols 摘要段（轻量指针，不含全量索引）。"""
    return {
        "schema": int((index or {}).get("schema", SYMBOL_INDEX_SCHEMA)),
        "count": int((index or {}).get("count", 0)),
        "index_file": filename,
    }


def index_path(out_dir: Path, filename: str = ARTIFACT_SYMBOLS) -> Path:
    return Path(out_dir) / filename


def write_symbol_index(out_dir: Path, index: dict,
                       filename: str = ARTIFACT_SYMBOLS) -> Path:
    """把完整索引写入独立产物文件，返回写入路径。"""
    p = index_path(out_dir, filename)
    p.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def index_from_file(path: Path) -> dict:
    """读取已落盘的索引产物；不存在/损坏返回空索引（调用方据此降级）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SYMBOL_INDEX_SCHEMA, "count": 0, _SECTION: {}}
    return data if isinstance(data, dict) else {
        "schema": SYMBOL_INDEX_SCHEMA, "count": 0, _SECTION: {}}


def render_table(result: dict) -> str:
    """把检索结果渲染为对齐文本表（人看）；无命中返回提示行。"""
    hits = result.get("hits") or []
    if not hits:
        return f"（未命中符号：{result.get('symbol', '')}）"
    header = ["symbol", "kind", "file", "line", "owner"]
    body = [[str(h.get("symbol", "")), str(h.get("kind", "")),
             str(h.get("file", "")), str(h.get("line", "")), str(h.get("owner", ""))]
            for h in hits]
    widths = [max(len(header[i]), *(len(r[i]) for r in body)) for i in range(len(header))]
    sep = "-+-".join("-" * w for w in widths)

    def fmt(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    lines = [fmt(header), sep] + [fmt(r) for r in body]
    if result.get("truncated"):
        lines.append(f"… 结果已截断（命中 {result.get('count')} 条，"
                     f"请收窄关键词或提高 --limit）")
    return "\n".join(lines)
