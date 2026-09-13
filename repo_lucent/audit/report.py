# -*- coding: utf-8 -*-
"""审计报告渲染：md（人读，裁决置顶）+ json（机读，可 diff）。"""
from __future__ import annotations

import json

_SEV_ICON = {"blocking": "⛔", "major": "❗", "minor": "⚠️", "info": "ℹ️"}
_DEC = {"releasable": "✅ 放行", "not_releasable": "⛔ 不放行"}
# CI 工作流注解级别映射（GitHub/Gitee Actions 通用 :: 命令）
_SEV_LEVEL = {"blocking": "error", "major": "warning", "minor": "warning", "info": "notice"}


def render_annotations(report) -> str:
    """输出 GitHub/Gitee Actions 可识别的 ::error/::warning/::notice 注解，逐条打到 PR/构建。"""
    out = []
    for f in report.findings:
        level = _SEV_LEVEL.get(f.severity, "notice")
        loc = ""
        if f.evidence and f.evidence[0].get("file"):
            e = f.evidence[0]
            loc = f" file={e['file']}" + (f",line={e['line']}" if e.get("line") else "")
        msg = f"[{f.rule_id}/{f.dimension}] {f.title}".replace("\n", " ")
        out.append(f"::{level}{loc}::{msg}")
    v = report.verdict
    if v.decision == "not_releasable":
        out.append(f"::error::代码审计裁决：不放行（阻断 {v.blocking}"
                   + (f"，证据不足维度 {'、'.join(v.evidence_insufficient)}" if v.evidence_insufficient else "") + "）")
    else:
        out.append("::notice::代码审计裁决：放行")
    return "\n".join(out) + "\n"


def render_json(report, deterministic: bool = False) -> str:
    d = report.to_dict()
    if deterministic:
        # 预留：阶段 3 会带 repo 绝对路径/时间戳，这里统一剥离以保证可逐字节 diff
        d["repo"] = d.get("repo", "").split("/")[-1]
    return json.dumps(d, ensure_ascii=False, sort_keys=True, indent=2)


def render_md(report) -> str:
    v = report.verdict
    L: list[str] = ["# 代码审计报告", ""]
    L.append(f"- 仓库：`{report.repo}`")
    L.append(f"- 维度：{'、'.join(report.dims)}")
    L.append(f"- **裁决：{_DEC.get(v.decision, v.decision)}** ｜ 阻断命中 {v.blocking}"
             + (f" ｜ 证据不足维度：{'、'.join(v.evidence_insufficient)}" if v.evidence_insufficient else ""))
    for r in v.reasons:
        L.append(f"  - {r}")
    L.append("")
    # 覆盖度矩阵
    L.append("## 覆盖度矩阵（专业性的关键：区分『审过且干净』与『没审』）")
    L.append("")
    L.append("| 维度 | 规则通道 | 语义通道 |")
    L.append("|---|---|---|")
    for d, c in report.coverage.items():
        ro = "✅ 已审" if c.rule_only.ran else f"❌ 未审（{c.rule_only.reason}）"
        sm = "✅ 已审" if c.semantic.ran else "— 未跑（默认仅提示项）"
        L.append(f"| {d} | {ro} / 命中 {c.rule_only.findings} | {sm} |")
    L.append("")
    # 摘要
    s = report.summary()
    L.append(f"## 发现总览：{s['total']} 条")
    if s["by_severity"]:
        L.append("- 按级别：" + "、".join(f"{k} {v}" for k, v in s["by_severity"].items()))
    L.append("")
    # 明细
    order = {"blocking": 0, "major": 1, "minor": 2, "info": 3}
    for f in sorted(report.findings, key=lambda x: order.get(x.severity, 9)):
        icon = _SEV_ICON.get(f.severity, "")
        loc = ""
        if f.evidence:
            e = f.evidence[0]
            loc = "（`" + str(e.get("file", ""))
            if e.get("line"):
                loc += ":" + str(e["line"])
            loc += "`）"
        tag = " `语义/咨询`" if f.provider == "semantic" else ""
        L.append(f"- {icon} **[{f.rule_id}/{f.severity}]**{tag} {f.title} {loc}")
        if f.evidence and f.evidence[0].get("snippet"):
            L.append(f"  - 证据：`{f.evidence[0]['snippet'][:120]}`")
        if f.fix:
            L.append(f"  - 建议：{f.fix}")
    if not report.findings:
        L.append("（规则通道未发现命中）")
    L.append("")
    L.append("> 注意：`coverage=semantic` 的规则默认不参与放行判定；某维度若无确定性通道且语义层未跑，"
             "裁决按『证据不足』处理，不得视为通过。")
    return "\n".join(L) + "\n"
