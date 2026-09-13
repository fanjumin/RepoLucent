# -*- coding: utf-8 -*-
"""把既有 ci_analyzer 的发现包装为统一 Finding —— 零改 ci，只调用。"""
from __future__ import annotations

from ... import ci_analyzer
from ..model import Finding, CI_SEV_MAP, DIM_ARCH, COV_RULE_ONLY


def run(repo_root) -> list[Finding]:
    data = ci_analyzer.analyze_ci_config(repo_root, platform="auto")
    if "error" in data:            # 无 CI 配置等：视为本提供方不适用，非缺陷
        return []
    findings: list[Finding] = []
    for f in data.get("findings", []):
        findings.append(Finding(
            rule_id=f.get("rule_id", "CI???"), dimension=DIM_ARCH,
            severity=CI_SEV_MAP.get(f.get("severity", "info"), "info"),
            title=f.get("message", "CI 配置问题"), provider="ci",
            coverage=COV_RULE_ONLY,
            evidence=[{k: v for k, v in
                       {"file": f.get("file"), "line": f.get("line"),
                        "snippet": f.get("message", "")}.items() if v}],
            confidence="high", fix=f.get("suggestion", ""),
        ))
    return findings


def dims_covered() -> dict:
    return {DIM_ARCH: True}
