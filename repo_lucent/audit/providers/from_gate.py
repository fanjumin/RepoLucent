# -*- coding: utf-8 -*-
"""把既有 gate.GateResult 包装为统一 Finding —— 零改 gate，只调用。

gate 的 5 个检查项映射到架构/质量维度规则；passed 的检查项不产 Finding（但计入覆盖度：
该维度"规则通道已审"）。这直接兑现"复用既有审计骨架、不重复实现"。
"""
from __future__ import annotations

from ... import gate
from ..model import (Finding, SEV_BLOCKING, SEV_MAJOR, SEV_MINOR, DIM_ARCH, DIM_QUALITY,
                     COV_RULE_ONLY)

# gate 名 → (rule_id, dimension, severity, title, provider)
GATE_RULE_MAP = {
    "manifest-invalid":   ("ARC001", DIM_ARCH, SEV_BLOCKING, "插件 manifest 校验失败"),
    "boundary-violation": ("ARC002", DIM_ARCH, SEV_BLOCKING, "核心模块跨层直接 import 业务插件"),
    "plugin-cycle":       ("ARC003", DIM_ARCH, SEV_MAJOR,    "插件间存在循环依赖"),
    "route-unprefixed":   ("ARC004", DIM_ARCH, SEV_MAJOR,    "插件路由未遵循 /admin/<identifier> 前缀"),
    "file-too-large":     ("QLT001", DIM_QUALITY, SEV_MINOR, "单文件代码行超过阈值"),
}

_GATE_NAMES = tuple(GATE_RULE_MAP.keys())


def run(data: dict, cfg, max_file_lines: int | None = None) -> list[Finding]:
    """对已生成的 data 跑全部架构/质量门禁，转成 Finding 列表。"""
    kwargs = {} if max_file_lines is None else {"max_file_lines": max_file_lines}
    results = gate.evaluate(list(_GATE_NAMES), data, cfg, **kwargs)
    findings: list[Finding] = []
    for r in results:
        rule_id, dim, sev, title = GATE_RULE_MAP[r.name]
        if r.passed:
            continue
        for hit in r.hits:
            ev = {"snippet": hit.get("detail", "")}
            for k in ("file", "plugin"):
                if hit.get(k):
                    ev[k if k == "file" else "file"] = hit[k]
            findings.append(Finding(
                rule_id=rule_id, dimension=dim, severity=sev, title=title,
                provider="gate", coverage=COV_RULE_ONLY,
                evidence=[{k: v for k, v in ev.items() if v}],
                confidence="high", fix=r.threshold or "",
                references=[f"gate:{r.name}"],
            ))
    return findings


def dims_covered() -> dict:
    """本提供方使哪些维度的"规则通道"视为已运行（有确定性检查）。"""
    return {DIM_ARCH: True, DIM_QUALITY: True}
