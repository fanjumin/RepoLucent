# -*- coding: utf-8 -*-
"""函数级依赖图：从 py_ast 的 import_facts + calls 事实派生「谁调用谁」的边。

定位：Sourcegraph/Sourcetrail 式依赖图的**本地最小版**。对标诚实声明——这是
**静态启发式解析，不是精确指针分析**：解析不出的（跨多层属性、动态 getattr、
类型推断）一律**丢弃边**，宁可欠连不误连，保证图可信、可解释。

产出两类视图，UI 各自取用（避免一次画几千节点）：
  - nodes/edges：符号级（函数/方法/类），id = "<file>::<symbol>"，weight=调用次数；
    受 MAX_SYMBOL_EDGES 上限截断为高频边。
  - module_edges：按归属(owner) rollup 的聚合边（核心↔业务↔边缘），恒全量、小、稳。

零新增解析：只吃 parse_cache 既有 entry。路径统一 POSIX、符号/边全排序，产物逐字节确定。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import config

GRAPH_SCHEMA = 1

#: 符号级边上限（按权重截断），防大仓图数据/前端布局失控。
MAX_SYMBOL_EDGES = 1500


def _posix(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def _module_of(rel: str) -> str:
    """文件相对路径 → 其可导入 dotted 模块名。"""
    p = _posix(rel)
    if p.endswith(".py"):
        p = p[:-3]
    parts = [s for s in p.split("/") if s]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _owner_of(rel: str, plugins_dir: str) -> str:
    parts = [p for p in _posix(rel).split("/") if p]
    if not parts:
        return "<root>"
    if len(parts) > 1 and plugins_dir and parts[0] == plugins_dir:
        return parts[1]
    return parts[0] if len(parts) > 1 else "<root>"


def _bindings_for(rel: str, entry: dict) -> dict[str, str]:
    """重建「本文件里的名字 → 它指向的绝对 dotted 源」。

    - `import a.b`          → {'a': 'a'}（绑定的是顶层段）
    - `import a.b as z`     → {'z': 'a.b'}
    - `from x.y import m`   → {'m': 'x.y.m'}
    - `from x.y import m as n`→ {'n': 'x.y.m'}
    - 相对 `from .z import k` / `from . import k`：用本文件所属包补绝对前缀。
    """
    pkg = _module_of(rel)
    pkg_parts = pkg.split(".") if pkg else []
    out: dict[str, str] = {}
    for f in (entry.get("import_facts") or []):
        kind = f.get("kind")
        module = f.get("module")
        level = int(f.get("level") or 0)
        name = f.get("name")
        alias = f.get("alias")
        if kind == "import":
            full = module or ""
            bind = alias or (full.split(".")[0] if full else "")
            target = alias and full or bind
        else:  # from
            if level and not module:
                base_parts = pkg_parts[: max(0, len(pkg_parts) - (level - 1))]
            elif level and module:
                base_parts = pkg_parts[: max(0, len(pkg_parts) - (level - 1))]
                base_parts = base_parts + module.split(".")
            else:
                base_parts = module.split(".") if module else []
            full = ".".join(base_parts + ([name] if name else []))
            bind = alias or name or ""
            target = full
        if bind:
            out[bind] = target
    return out


def build_graph(parse_cache: dict, cfg=None) -> dict:
    """由 parse_cache（文件 rel → py_ast entry）构建依赖图。

    返回 {schema, granularity:"symbol", stats, nodes, edges, module_edges}。
    """
    plugins_dir = getattr(config, "PLUGINS_DIR", "") or ""
    facts = { _posix(e.get("path") or k): e
              for k, e in (parse_cache.items() if isinstance(parse_cache, dict)
                           else ((e["path"], e) for e in parse_cache)) }

    # 全局符号定义：bare name → files；"Class.method" → files；模块 → file
    sym_files: dict[str, list[str]] = {}
    mod_file: dict[str, str] = {}
    loc_of: dict[str, int] = {}
    kind_of: dict[tuple[str, str], str] = {}   # (file, symbol) → kind
    for rel, e in facts.items():
        mod_file.setdefault(_module_of(rel), rel)
        loc_of[rel] = int(e.get("loc_code") or 0)
        for c in (e.get("classes") or []):
            nm = c.get("name")
            if nm:
                sym_files.setdefault(nm, []).append(rel)
                kind_of[(rel, nm)] = "class"
            for m in (c.get("methods") or []):
                mn = m.get("name")
                if mn:
                    key = f"{nm}.{mn}"
                    sym_files.setdefault(key, []).append(rel)
                    kind_of[(rel, key)] = "method"
        for fn in (e.get("functions") or []):
            nm = fn.get("name")
            if nm:
                sym_files.setdefault(nm, []).append(rel)
                kind_of[(rel, nm)] = "function"

    def resolve(rel: str, target: str, bind: dict[str, str]) -> str | None:
        """把一个调用目标解析成仓内定义它的文件；解析不出返回 None（丢弃边）。"""
        segs = target.split(".")
        root, last = segs[0], segs[-1]
        dotted = ".".join(segs[-2:]) if len(segs) >= 2 else last

        def defined(f: str) -> bool:
            return (f, last) in kind_of or (f, dotted) in kind_of

        # 规则A：root 是本文件 import 绑定 → 按绑定源逐级回退定位定义模块
        if root in bind:
            head = bind[root].split(".")
            cands = [".".join(head[:i]) for i in range(len(head), 0, -1)]
            for cand in cands:
                f = mod_file.get(cand)
                if f and defined(f):
                    return f
            dests = (set(sym_files.get(last, [])) | set(sym_files.get(dotted, []))) - {rel}
            if len(dests) == 1:
                return next(iter(dests))
            return None
        # 规则B：同文件内的局部调用
        if defined(rel):
            return rel
        # 规则C：全局唯一裸名调用（歧义则丢弃，防误连）
        if len(segs) == 1:
            dests = set(sym_files.get(last, []))
            if len(dests) == 1:
                return next(iter(dests))
        return None

    edge_w: Counter = Counter()
    fe_w: Counter = Counter()          # 文件→文件 聚合边（rollup）
    oe_w: Counter = Counter()          # 归属(owner)→归属 聚合边（插件/包级 coarse 视图）
    node_use: Counter = Counter()
    for rel, e in facts.items():
        bind = _bindings_for(rel, e)
        owner_src = _owner_of(rel, plugins_dir)
        for site in (e.get("calls") or []):
            caller = site.get("caller") or "@module"
            target = site.get("target") or ""
            if target.startswith("<expr>") or not target:
                continue
            dst = resolve(rel, target, bind)
            if not dst:
                continue
            src_id = f"{rel}::{caller}"
            dst_id = f"{dst}::{target.split('.')[-1]}"
            if src_id == dst_id:        # 丢自环噪声
                continue
            edge_w[(src_id, dst_id, rel, dst)] += 1
            owner_dst = _owner_of(dst, plugins_dir)
            if rel != dst:
                fe_w[(rel, dst)] += 1
            if owner_src != owner_dst:
                oe_w[(owner_src, owner_dst)] += 1
            node_use[src_id] += 1
            node_use[dst_id] += 1

    # ---- C/C++/Arduino：以 #include 作为"依赖边"（嵌入式工程的真实架构信号）----
    files_list = list(facts)
    by_base: dict[str, list[str]] = {}
    for p in files_list:
        by_base.setdefault(p.split("/")[-1], []).append(p)

    def _resolve_include(target: str) -> str | None:
        t = str(target or "").replace("\\", "/").strip().lstrip("./")
        if not t:
            return None
        if t in facts:
            return t
        suffix = "/".join(["", t])            # "/"+t
        cands = [p for p in files_list if p.endswith(suffix)]
        if len(set(cands)) == 1:
            return cands[0]
        base = t.split("/")[-1]
        bb = by_base.get(base) or []
        if len(set(bb)) == 1:
            return bb[0]
        return None

    file_inc: dict[str, list[dict]] = {}
    for rel, e in facts.items():
        incs = e.get("includes")
        if not incs:
            continue
        owner_src = _owner_of(rel, plugins_dir)
        for inc in incs:
            if inc.get("system"):
                continue                      # <...> 视为系统/框架/三方，不连
            dst = _resolve_include(inc.get("target"))
            file_inc.setdefault(rel, []).append(
                {"target": inc.get("target"), "resolved": dst})
            if not dst or dst == rel:
                continue
            fe_w[(rel, dst)] += 1
            owner_dst = _owner_of(dst, plugins_dir)
            if owner_src != owner_dst:
                oe_w[(owner_src, owner_dst)] += 1

    # 符号级边：按权重截断到 MAX_SYMBOL_EDGES
    top_edges = sorted(edge_w.items(), key=lambda kv: (-kv[1], kv[0][:2]))[:MAX_SYMBOL_EDGES]
    nodes: dict[str, dict] = {}
    for (src_id, dst_id, sfile, dfile), _ in edge_w.items():
        for nid, nfile in ((src_id, sfile), (dst_id, dfile)):
            if nid in nodes:
                continue
            sym = nid.split("::", 1)[1]
            nodes[nid] = {"id": nid, "file": nfile, "symbol": sym,
                          "owner": _owner_of(nfile, plugins_dir),
                          "kind": kind_of.get((nfile, sym.split(".")[-1])) or
                                  kind_of.get((nfile, sym)) or "symbol",
                          "calls": node_use.get(nid, 0)}
    edges = [{"from": s, "to": d, "weight": w} for (s, d, _, _), w in top_edges]

    file_edges = [{"from": a, "to": b, "weight": w}
                  for (a, b), w in sorted(fe_w.items(), key=lambda kv: (-kv[1], kv[0]))]
    owner_edges = [{"from": a, "to": b, "weight": w}
                   for (a, b), w in sorted(oe_w.items(), key=lambda kv: (-kv[1], kv[0]))]
    indeg: Counter = Counter()
    outdeg: Counter = Counter()
    for (a, b), _w in fe_w.items():
        outdeg[a] += 1
        indeg[b] += 1
    files = [{"path": rel, "owner": _owner_of(rel, plugins_dir),
              "loc": loc_of.get(rel, 0), "in_deg": indeg.get(rel, 0),
              "out_deg": outdeg.get(rel, 0)}
             for rel in sorted(facts)]
    include_cycles = _find_file_cycles(fe_w)
    owners = sorted({n["owner"] for n in nodes.values()} |
                    {e["from"] for e in owner_edges} | {e["to"] for e in owner_edges})
    return {
        "schema": GRAPH_SCHEMA,
        "granularity": "symbol",
        "stats": {"files": len(facts), "nodes": len(nodes),
                  "edges_total": len(edge_w), "edges_emitted": len(edges),
                  "file_edges": len(file_edges), "owner_edges": len(owner_edges),
                  "include_cycles": len(include_cycles),
                  "truncated": len(edge_w) > MAX_SYMBOL_EDGES},
        "owners": owners,
        "files": files,
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": sorted(edges, key=lambda x: (-x["weight"], x["from"], x["to"])),
        "file_edges": file_edges,
        "owner_edges": owner_edges,
        "include_cycles": include_cycles,
        "file_includes": {k: file_inc[k] for k in sorted(file_inc)},
    }


def _find_file_cycles(fe_w: "Counter") -> list[list[str]]:
    """在文件→文件的 include 有向图上找全部简单环（排除自环）。

    DFS 三色标记；同 gate._find_cycles 口径：环旋转到最小节点去重。
    """
    adj: dict[str, set[str]] = {}
    for (a, b) in fe_w:
        if a == b:
            continue
        adj.setdefault(a, set()).add(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    path: list[str] = []
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    nodes = sorted(set(adj) | {b for vs in adj.values() for b in vs})

    def norm(cyc: list[str]) -> tuple[str, ...]:
        body = cyc[:-1]
        i = body.index(min(body))
        return tuple(body[i:] + body[:i])

    def dfs(u: str) -> None:
        color[u] = GRAY
        path.append(u)
        for v in sorted(adj.get(u, ())):
            if color.get(v, WHITE) == GRAY:
                cyc = path[path.index(v):] + [v]
                key = norm(cyc)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cyc)
            elif color.get(v, WHITE) == WHITE:
                dfs(v)
        path.pop()
        color[u] = BLACK

    for n in nodes:
        if color.get(n, WHITE) == WHITE:
            dfs(n)
    return sorted(cycles, key=lambda c: (len(c), c))


def summary(graph: dict) -> dict:
    """图摘要（可选嵌进产物，不含全量）。"""
    return {"schema": GRAPH_SCHEMA, "granularity": graph.get("granularity"),
            "stats": graph.get("stats", {})}
