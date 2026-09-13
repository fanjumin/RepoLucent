# -*- coding: utf-8 -*-
"""SEC001-004：源码安全规则。依赖插件 .py 文本与 AST 扫描。"""
from __future__ import annotations

import re

from ._types import (Finding, SEVERITY_ERROR, SEVERITY_WARNING,
                     AnalysisContext, scan_ast)

_SECRET_RE = re.compile(
    r"""(?i)(api[_-]?key|secret|token|access[_-]?key|passwd|password)\s*[:=]\s*
        (["'])                       # 引号开始
        (?!\{|\$\{|<\|%\{)           # 排除占位符 ${} / <%{ / {
        [^"']{6,}                    # 至少 6 位字面量
        \2""", re.VERBOSE)
_SQL_RE = re.compile(
    r"""(?i)(f["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE)\b[^"']*["'])
        |(["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE)\b[^"']*["']\s*[%\+])""",
    re.VERBOSE)


def _read(fpath) -> str:
    try:
        return fpath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def run_security(ctx: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    for rel, fpath in ctx.plugin_py:
        text = _read(fpath)
        if not text:
            continue
        # SEC001 硬编码密钥/凭证
        for m in _SECRET_RE.finditer(text):
            findings.append(Finding(
                "SEC001", SEVERITY_ERROR, "security", rel, None, None,
                "源码含疑似硬编码密钥/凭证", m.group(0).strip()[:120]))
        # SEC004 疑似 SQL 字符串拼接（注入风险）
        for m in _SQL_RE.finditer(text):
            findings.append(Finding(
                "SEC004", SEVERITY_WARNING, "security", rel, None, None,
                "疑似 SQL 字符串拼接（注入风险）", m.group(0).strip()[:120]))
        # SEC002 / SEC003 用 AST
        ast_res = scan_ast(text)
        for name, lineno, kws in ast_res["calls"]:
            if name in ("eval", "exec"):
                findings.append(Finding(
                    "SEC002", SEVERITY_WARNING, "security", rel, None, lineno,
                    "使用了 eval()/exec()（代码执行风险）", f"{name}() @L{lineno}"))
            if name.startswith("subprocess.") and kws.get("shell") is True:
                findings.append(Finding(
                    "SEC003", SEVERITY_WARNING, "security", rel, None, lineno,
                    "subprocess 以 shell=True 调用（命令注入风险）",
                    f"{name}(shell=True) @L{lineno}"))
    return findings
