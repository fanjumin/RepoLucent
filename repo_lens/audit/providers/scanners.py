# -*- coding: utf-8 -*-
"""源码级确定性扫描（阶段 2）：手写 SEC001/SEC005 + 数据驱动 rulepack（regex_line + ast）。

复用 fs_scan 已枚举的分析文件与 CODE_EXTS（不重造遍历、共享排除规则）。
支持仓库外置规则（rulepack.load_repo_rules）与 CLI 传入的 extra_rules。全确定性 → coverage=rule_only。
"""
from __future__ import annotations

import ast
import re

from ... import fs_scan
from ...config import CODE_EXTS
from ..model import (Finding, SEV_BLOCKING, SEV_MAJOR, DIM_SECURITY, COV_RULE_ONLY)
from . import rulepack

# ---- 手写：SEC001 硬编码凭据 / SEC005 SQL 注入（含占位/参数化排除，不适合纯单正则）----
_SECRET_RE = re.compile(
    r"""(?ix)\b(password|passwd|pwd|secret|token|api_?key|access_?key|access_?token)\b
    \s*=\s*(['\"])([^'\"]{8,})\2""")
_PLACEHOLDER = re.compile(r"(?i)(os\.environ|getenv|<.*>|xxx+|\*{3,}|placeholder|example|dummy|changeme)")
_SQLI = re.compile(r"\.execute\(\s*f['\"]|\.execute\([^)]*%[^)]*\)|\.execute\([^)]*\+")


def _sec001_005(relp: str, lines: list[str]) -> list[tuple]:
    hits = []
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("#"):
            continue
        for m in _SECRET_RE.finditer(line):
            if not (_PLACEHOLDER.search(line) or _PLACEHOLDER.search(m.group(3))):
                hits.append(("SEC001", DIM_SECURITY, SEV_MAJOR, "源码硬编码明文凭据", i, s,
                             "改用环境变量/密钥管理；勿把真实凭据写进代码或提交进版本库。",
                             ["OWASP:A05", "CWE-798"], "medium"))
        if _SQLI.search(line):
            hits.append(("SEC005", DIM_SECURITY, SEV_BLOCKING, "疑似 SQL 注入：动态拼接进 execute()",
                         i, s, "使用参数化查询 execute(sql, params)，禁止把输入拼进 SQL 字符串。",
                         ["OWASP:A03", "CWE-89"], "medium"))
    return hits


def _mk_finding(spec_id, spec) -> Finding:
    """把 rulepack 命中转 Finding。spec 为命中 {line,snippet}。"""
    return Finding(
        rule_id=spec_id["rule_id"], dimension=spec_id["dimension"], severity=spec_id["severity"],
        title=spec_id["title"], provider="scanner", coverage=COV_RULE_ONLY,
        evidence=[{"file": spec["file"], "line": spec["line"], "snippet": spec["snippet"]}],
        confidence=spec_id.get("confidence", "high"), fix=spec_id.get("fix", ""),
        references=spec_id.get("references", []))


def run(cfg, extra_rules: list[dict] | None = None) -> list[Finding]:
    rules = list(rulepack.BUILTIN_RULES) + rulepack.load_repo_rules(cfg.repo_root) + list(extra_rules or [])
    line_rules = [r for r in rules if r.get("matcher", {}).get("type") == "regex_line"]
    ast_rules = [r for r in rules if r.get("matcher", {}).get("type") == "ast"]

    findings: list[Finding] = []
    for rel, fpath in fs_scan.iter_repo_files(cfg):
        if fpath.suffix.lower() not in CODE_EXTS:
            continue
        try:
            text = fs_scan.read_text_safe(fpath)
        except Exception:  # noqa: BLE001 - 读不到的文件跳过，不影响整体审计
            continue
        relp = str(rel).replace("\\", "/")
        lines = text.splitlines()

        # 手写规则
        for rid, dim, sev, title, ln, snip, fix, refs, conf in _sec001_005(relp, lines):
            findings.append(Finding(
                rule_id=rid, dimension=dim, severity=sev, title=title, provider="scanner",
                coverage=COV_RULE_ONLY, evidence=[{"file": relp, "line": ln, "snippet": snip[:120]}],
                confidence=conf, fix=fix, references=refs))

        # 规则包：regex_line
        for rule in line_rules:
            for hit in rulepack.run_line_rule(rule, lines, text):
                findings.append(_mk_finding({**rule, "confidence": rule.get("confidence", "medium")},
                                            {"file": relp, **hit}))

        # 规则包：ast（每文件解析一次）
        if ast_rules:
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                for rule in ast_rules:
                    for hit in rulepack.run_ast_rule(rule, tree, text):
                        findings.append(_mk_finding({**rule, "confidence": rule.get("confidence", "high")},
                                                    {"file": relp, **hit}))
    return findings


def dims_covered() -> dict:
    # security（SEC*）、quality（QLT*）、ai_business（AIB001 确定性部分）
    return {DIM_SECURITY: True, "quality": True, "ai_business": True}
