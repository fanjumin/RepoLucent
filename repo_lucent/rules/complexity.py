# -*- coding: utf-8 -*-
"""CMP001-002：复杂度规则。

CMP001 用 overview.top_files（全仓单文件代码行）；
CMP002 用插件函数体跨度（基于插件源码 AST）。
"""
from __future__ import annotations

from ._types import Finding, SEVERITY_INFO, AnalysisContext, scan_ast


def run_complexity(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    cfg = ctx.cfg
    max_file = getattr(cfg, "max_file_lines", 2000)
    max_func = getattr(cfg, "max_func_lines", 120)

    # CMP001 单文件代码行超过阈值
    top_files = ((ctx.data.get("overview") or {}).get("top_files") or [])
    for e in top_files:
        code = e.get("code") or 0
        if code > max_file:
            findings.append(Finding(
                "CMP001", SEVERITY_INFO, "complexity", e.get("file", "?"),
                None, None,
                f"单文件 {code} 代码行 > {max_file}", f"code_lines={code}"))

    # CMP002 单函数体行数超过阈值
    for rel, fpath in ctx.plugin_py:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, lineno, end in scan_ast(text)["functions"]:
            span = (end - lineno + 1) if end and lineno else 0
            if span > max_func:
                findings.append(Finding(
                    "CMP002", SEVERITY_INFO, "complexity", rel, None, lineno,
                    f"函数 {name} 体 {span} 行 > {max_func}",
                    f"{name}() @L{lineno}-{end}"))
    return findings
