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
    # v2.1.1 口径修正（三收窄，556 条虚高 → 每前缀一条的真偏离）：
    #   ① 公开域豁免：url_prefix 不在 /admin 域 → 跳过（插件公开路由按设计
    #      使用专属公开前缀，管理域惯例不适用，此前把 /mall、/api/* 全误伤）；
    #   ② URL 连字符归一：/admin/site-builder ↔ site_builder 视为同一命名
    #      （kebab-case URL 是常规风格选择，不是违规）；
    #   ③ 蓝图级去重：按 (identifier, 前缀) 聚合报一条，此前逐路由重复计数。
    plugins = (data.get("plugins") or {}).get("items") or []
    reported4: set[tuple[str, str]] = set()
    for p in plugins:
        ident = p.get("identifier", "")
        allowed = {f"/admin/{ident}", f"/admin/{p.get('dir', ident)}"}
        for r in (p.get("routes") or []):
            prefix = (r.get("url_prefix") or "").strip().strip("\"'")
            if not prefix.startswith("/admin"):
                continue                            # ① 公开域 / 空前缀 / 动态值豁免
            norm = "/" + "/".join(seg.replace("-", "_")
                                  for seg in prefix.strip("/").split("/"))
            if any(norm.rstrip("/") == a or norm.startswith(a + "/")
                   for a in allowed):
                continue                            # ② 归一后同源（含本域子路径）
            dkey = (ident, norm)
            if dkey in reported4:
                continue                            # ③ 每前缀只报一条
            reported4.add(dkey)
            findings.append(Finding(
                "ARCH004", SEVERITY_WARNING, "architecture", ident,
                r.get("file"), None,
                f"管理域前缀 url_prefix={prefix or '(空)'} 未遵循 /admin/<identifier>",
                f"期望 {sorted(allowed)[0]}（同前缀路由已聚合）"))
    return findings
