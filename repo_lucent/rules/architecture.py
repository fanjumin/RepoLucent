# -*- coding: utf-8 -*-
"""ARCH001-004：架构规则。

ARCH001/002/004 基于 data 既有字段；ARCH003 基于插件源码 AST（私有连接池）。
"""
from __future__ import annotations

from ._types import (Finding, SEVERITY_ERROR, SEVERITY_WARNING,
                     AnalysisContext, scan_ast)


def _find_cycles(graph: dict) -> list:
    """检测有向图全部简单环（含自环），返回规范化后的环路径列表。DFS 三色标记。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict = {}
    path: list = []
    cycles: list = []
    seen: set = set()

    def norm(c):
        nodes = c[:-1]                      # 末元素与首元素相同
        i = nodes.index(min(nodes))
        return tuple(nodes[i:] + nodes[:i])

    def dfs(u):
        color[u] = GRAY
        path.append(u)
        for v in graph.get(u, []):
            if color.get(v, WHITE) == GRAY:    # 回边 → 发现环
                cyc = path[path.index(v):] + [v]
                k = norm(cyc)
                if k not in seen:
                    seen.add(k)
                    cycles.append(cyc)
            elif color.get(v, WHITE) == WHITE:
                dfs(v)
        path.pop()
        color[u] = BLACK

    for n in sorted(graph):
        if color.get(n, WHITE) == WHITE:
            dfs(n)
    return cycles


def run_architecture(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    data = ctx.data
    inter = data.get("interactions") or {}

    # ARCH001 核心模块直接 import 业务插件
    obs = (inter.get("boundary_observations") or {}).get("violations") or []
    for v in obs:
        findings.append(Finding(
            "ARCH001", SEVERITY_ERROR, "architecture",
            v.get("module", "?"), v.get("file"), None,
            f"核心模块 {v.get('module')} 直接 import 业务插件 {v.get('imports')}",
            f"import {v.get('imports')}"))

    # ARCH002 插件间循环依赖
    edges = inter.get("plugin_to_plugin") or []
    graph = {e["from"]: list(e["to"]) for e in edges}
    for cyc in _find_cycles(graph):
        findings.append(Finding(
            "ARCH002", SEVERITY_ERROR, "architecture",
            " → ".join(cyc), None, None,
            f"插件循环依赖：{' → '.join(cyc)}", "depends_on/实际 import 形成环"))

    # ARCH003 插件私有连接池（直连数据库）
    for rel, fpath in ctx.plugin_py:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, lineno, _ in scan_ast(text)["calls"]:
            if name == "psycopg2.connect" or name == "create_engine":
                findings.append(Finding(
                    "ARCH003", SEVERITY_WARNING, "architecture", rel, None, lineno,
                    "插件私有连接池（直连数据库，违反共享连接治理）",
                    f"{name}() @L{lineno}"))

    # ARCH004 路由前缀未遵循 /admin/<identifier>
    plugins = (data.get("plugins") or {}).get("items") or []
    for p in plugins:
        ident = p.get("identifier", "")
        allowed = {f"/admin/{ident}", f"/admin/{p.get('dir', ident)}"}
        for r in (p.get("routes") or []):
            prefix = (r.get("url_prefix") or "").strip().strip("\"'")
            if not prefix.startswith("/"):
                continue
            if prefix.rstrip("/") in allowed:
                continue
            findings.append(Finding(
                "ARCH004", SEVERITY_WARNING, "architecture", ident,
                r.get("file"), None,
                f"{r.get('endpoint', '?')} 的 url_prefix={prefix or '(空)'} 未遵循 /admin/<identifier>",
                f"期望 {sorted(allowed)[0]}"))
    return findings
