# -*- coding: utf-8 -*-
"""审计编排引擎：把各 provider 的 Finding 归并 → 覆盖度矩阵 → 放行裁决。

确定性优先：阶段 1 无语义层，semantic 通道一律 ran=false；被请求但无确定性通道且语义未跑的
维度，判"证据不足"→ 不放行（不把"没命中"当"通过"）。
"""
from __future__ import annotations

from .model import (AuditReport, DimCoverage, Channel, Verdict,
                    DIM_ARCH, DIM_QUALITY, DIM_SECURITY, DIM_AI,
                    COV_RULE_ONLY, COV_SEMANTIC, sev_rank)
from .providers import from_gate, from_ci, scanners

DEFAULT_DIMS = (DIM_SECURITY, DIM_ARCH, DIM_QUALITY, DIM_AI)   # 阶段2起 ai_business 有确定性 AIB001；semantic-only 规则仍只登记不判定


def _provider_findings(dims, cfg, data, repo_root, extra_rules=None):
    """按请求维度调用相应 provider，返回 (findings, rule_only_dims_set)。

    scanners 一次调用即产 security/quality/ai_business 三维结论，故只要请求了其中任一维度就
    跑一次（去重）。from_gate 产 architecture+quality；from_ci 产 architecture。
    """
    findings: list[Finding] = []
    rule_ran: set[str] = set()

    if (DIM_ARCH in dims) or (DIM_QUALITY in dims):
        gf = from_gate.run(data, cfg)
        findings += gf
        rule_ran |= {f.dimension for f in gf}
        # 即便无命中，也标记被请求且有确定性检查的维度已"规则通道审过"
        if DIM_ARCH in dims:
            rule_ran.add(DIM_ARCH)
        if DIM_QUALITY in dims:
            rule_ran.add(DIM_QUALITY)

    if DIM_ARCH in dims:
        cf = from_ci.run(repo_root)
        findings += cf
        rule_ran.add(DIM_ARCH)

    if (DIM_SECURITY in dims) or (DIM_QUALITY in dims) or (DIM_AI in dims):
        sf = scanners.run(cfg, extra_rules)      # 一次扫，覆盖三维
        findings += [f for f in sf if f.dimension in dims]
        rule_ran |= {f.dimension for f in sf if f.dimension in dims}
        # 被请求且扫描器已执行的维度视为规则通道已审
        for d in (DIM_SECURITY, DIM_QUALITY, DIM_AI):
            if d in dims and d in scanners.dims_covered():
                rule_ran.add(d)

    return [f for f in findings if f.dimension in dims], rule_ran


def run_audit(cfg, data, repo_root, dims=None, fail_on_severity="blocking",
              use_semantic: bool = False, extra_rules=None,
              semantic_findings=None, semantic_can_block: bool = False) -> AuditReport:
    dims = list(dims) if dims else list(DEFAULT_DIMS)
    findings, rule_ran = _provider_findings(dims, cfg, data, repo_root, extra_rules)

    sem = [f for f in (semantic_findings or []) if f.dimension in dims]
    findings = findings + sem

    # 覆盖度矩阵
    coverage: dict[str, DimCoverage] = {}
    for d in dims:
        dc = DimCoverage()
        dc.rule_only = Channel(ran=(d in rule_ran),
                               findings=sum(1 for f in findings
                                            if f.dimension == d and f.coverage == COV_RULE_ONLY),
                               reason="" if d in rule_ran else "该维度无确定性规则通道")
        sem_ran_here = any(f.dimension == d for f in sem)
        if sem_ran_here:
            dc.semantic = Channel(ran=True,
                                  findings=sum(1 for f in sem if f.dimension == d),
                                  reason="")
        elif use_semantic:
            dc.semantic = Channel(ran=False, reason="--with-semantic 已开，但未回灌结果")
        else:
            dc.semantic = Channel(ran=False, reason="未启用语义层")
        coverage[d] = dc

    # 裁决
    thr = sev_rank(fail_on_severity)
    blocking = [f for f in findings
                if sev_rank(f.severity) >= thr
                and (f.coverage == COV_RULE_ONLY or semantic_can_block)]
    insufficient = [d for d in dims
                    if not coverage[d].rule_only.ran and not coverage[d].semantic.ran]
    reasons = []
    if blocking:
        reasons.append(f"达阈值({fail_on_severity})命中 {len(blocking)} 条")
    if sem and not semantic_can_block:
        reasons.append(f"含 {len(sem)} 条语义发现为咨询项，默认不影响放行（加 --semantic-can-block 纳入判定）")
    if insufficient:
        reasons.append(f"维度证据不足（两通道均未跑）：{', '.join(insufficient)}——按审计红线不得判为通过")
    decision = "not_releasable" if (blocking or insufficient) else "releasable"
    verdict = Verdict(decision=decision, blocking=len(blocking),
                      evidence_insufficient=insufficient,
                      reasons=reasons or ["未见达阈值问题，且各请求维度均有确定性覆盖"])

    return AuditReport(repo=str(repo_root), dims=dims, findings=findings,
                       coverage=coverage, verdict=verdict)
