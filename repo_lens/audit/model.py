# -*- coding: utf-8 -*-
"""审计内核的数据契约：Finding / Dimension / Severity / Coverage / Verdict / AuditReport。

设计：统一既有 gate（GateResult）与 ci（rule_id/severity finding）以及源码级扫描结论
为同一 Finding 模型；severity 采用四级（blocking/major/minor/info），并给出对旧三级的映射。
"""
from __future__ import annotations

from dataclasses import dataclass, field

AUDIT_SCHEMA_VERSION = "1.0"

# ---- 受控枚举（用字符串常量，JSON 友好）----
SEV_INFO, SEV_MINOR, SEV_MAJOR, SEV_BLOCKING = "info", "minor", "major", "blocking"
_SEV_RANK = {SEV_INFO: 0, SEV_MINOR: 1, SEV_MAJOR: 2, SEV_BLOCKING: 3}
# 旧 CI severity → 新四级
CI_SEV_MAP = {"error": SEV_BLOCKING, "warning": SEV_MINOR, "info": SEV_INFO}

DIM_SECURITY = "security"
DIM_ARCH = "architecture"
DIM_QUALITY = "quality"
DIM_AI = "ai_business"
DIMENSIONS = (DIM_SECURITY, DIM_ARCH, DIM_QUALITY, DIM_AI)

COV_RULE_ONLY = "rule_only"
COV_SEMANTIC = "semantic"


def sev_rank(s: str) -> int:
    return _SEV_RANK.get(s, 0)


@dataclass
class Finding:
    rule_id: str
    dimension: str
    severity: str
    title: str
    provider: str                       # gate | ci | scanner | semantic
    coverage: str = COV_RULE_ONLY       # rule_only | semantic
    evidence: list = field(default_factory=list)   # [{file,line?,snippet,why?}]
    confidence: str = "high"            # high | medium
    fix: str = ""
    references: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id, "dimension": self.dimension,
            "severity": self.severity, "title": self.title,
            "provider": self.provider, "coverage": self.coverage,
            "confidence": self.confidence, "evidence": self.evidence,
            "fix": self.fix, "references": self.references,
        }


@dataclass
class Channel:
    ran: bool = False
    findings: int = 0
    reason: str = ""


@dataclass
class DimCoverage:
    rule_only: Channel = field(default_factory=Channel)
    semantic: Channel = field(default_factory=Channel)

    def to_dict(self) -> dict:
        return {"rule_only": self.rule_only.__dict__,
                "semantic": self.semantic.__dict__}


@dataclass
class Verdict:
    decision: str = "releasable"        # releasable | not_releasable
    blocking: int = 0                   # 达阈值的阻断命中数
    evidence_insufficient: list = field(default_factory=list)  # 证据不足（无确定性通道）的维度
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "blocking": self.blocking,
                "evidence_insufficient": self.evidence_insufficient,
                "reasons": self.reasons}


@dataclass
class AuditReport:
    repo: str
    dims: list
    findings: list = field(default_factory=list)     # list[Finding]
    coverage: dict = field(default_factory=dict)      # dim -> DimCoverage
    verdict: Verdict = field(default_factory=Verdict)

    def summary(self) -> dict:
        by_sev, by_dim = {}, {}
        for f in self.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            by_dim[f.dimension] = by_dim.get(f.dimension, 0) + 1
        return {"total": len(self.findings), "by_severity": by_sev, "by_dimension": by_dim}

    def to_dict(self) -> dict:
        return {
            "audit_schema": AUDIT_SCHEMA_VERSION,
            "repo": self.repo, "dimensions": self.dims,
            "coverage_matrix": {d: c.to_dict() for d, c in self.coverage.items()},
            "findings": [f.to_dict() for f in self.findings],
            "verdict": self.verdict.to_dict(),
            "summary": self.summary(),
        }
