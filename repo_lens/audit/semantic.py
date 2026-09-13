# -*- coding: utf-8 -*-
"""语义层（阶段 4）：编排—判定分离。工具不接 LLM，只做两件事：

1) build_prompt：导出"语义审计任务 + AI 上下文切片 + 严格输出 schema"，交给外部 AI Agent 判定；
2) load_results：校验并回灌 Agent 产出的 semantic_findings（coverage=semantic，provider=semantic）。

红线：语义发现默认是**咨询项**，不单独支撑放行；仅当审计时显式允许（semantic_can_block）才纳入
阻断判定。语义通道只会让结论更严，不会把"规则通道未过"洗成"通过"。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..report_ai import render_ai_context
from .model import Finding, DIM_AI, DIM_SECURITY, COV_SEMANTIC

# 适用 semantic 的规则（确定性通道做不到、需跨文件/语义理解）
SEMANTIC_RULES: dict[str, dict] = {
    "AIB002": {"dimension": DIM_AI, "severity": "major",
               "title": "配置多源不同步（后台写入源 vs 前端读取源不一致）",
               "inspect": "核对：后台设置写入的存储/表/键，与前端/运行时实际读取的源是否同一。"
                          "若写 A 读 B、或存在两份互不触发的配置，判为缺陷并定位写点与读点。"},
    "AIB004": {"dimension": DIM_AI, "severity": "major",
               "title": "规则/关键词未落到生效通道（UI 配置但运行时不读）",
               "inspect": "核对：界面上可配的规则（如转人工关键词、开关），是否真正被运行时读取生效；"
                          "是否存在'纯装饰配置'或双通道只喂了一条。"},
    "SEC004": {"dimension": DIM_SECURITY, "severity": "major",
               "title": "敏感端点缺少鉴权/越权校验",
               "inspect": "对每个对外写/读端点，判断是否需要鉴权且是否实际施加（装饰器/中间件/内部校验）；"
                          "结合路径语义与参数识别越权。"},
}


def _schema_hint() -> str:
    return json.dumps({
        "findings": [{
            "rule_id": "AIB002", "dimension": "ai_business", "severity": "major|blocking|minor|info",
            "title": "一句话结论", "file": "相对仓库路径", "line": 17,
            "why": "为什么判为缺陷（引用关键证据）", "confidence": "high|medium",
            "fix": "修复建议"
        }]
    }, ensure_ascii=False, indent=2)


def build_prompt(cfg, data, dims) -> str:
    """生成语义审计任务书（含 AI 上下文切片），供外部 AI Agent 阅读判定。"""
    rules = [(rid, r) for rid, r in SEMANTIC_RULES.items() if r["dimension"] in dims]
    L: list[str] = ["# 语义审计任务书（RepoLens 导出）", ""]
    L.append("> 本任务由确定性引擎无法覆盖的规则产生。请作为 AI 审查者阅读下方 AI 上下文，")
    L.append("> 逐条判定下列语义规则，并**只返回** schema 规定的 JSON（不要多余文本）。")
    L.append("> 每条结论须给出 file/line 证据；不确定就不报（宁漏报不误伤），confidence 如实填。")
    L.append("")
    L.append("## 待判定规则")
    for rid, r in rules:
        L.append(f"- **{rid}** [{r['dimension']}/{r['severity']}] {r['title']}")
        L.append(f"  审查要点：{r['inspect']}")
    L.append("")
    L.append("## 输出 schema（严格照此返回）")
    L.append("```json")
    L.append(_schema_hint())
    L.append("```")
    L.append("")
    L.append("## 仓库 AI 上下文（判定依据）")
    L.append("")
    L.append(render_ai_context(data, getattr(cfg, "max_plugins_in_ai_context", 60)))
    return "\n".join(L) + "\n"


def _validate_items(items: list) -> tuple[list[Finding], list[str]]:
    findings, rejects = [], []
    for i, f in enumerate(items):
        rid = (f or {}).get("rule_id")
        if rid not in SEMANTIC_RULES:
            rejects.append(f"#{i}: 未知语义规则 {rid!r}（不在 semantic 目录，已丢弃）")
            continue
        if not f.get("file"):
            rejects.append(f"#{i} {rid}: 缺 file 证据，已丢弃")
            continue
        spec = SEMANTIC_RULES[rid]
        ev = {"file": str(f["file"]).replace("\\", "/")}
        if f.get("line"):
            ev["line"] = int(f["line"])
        ev["snippet"] = f.get("why", "")
        findings.append(Finding(
            rule_id=rid, dimension=spec["dimension"],
            severity=f.get("severity", spec["severity"]),
            title=f.get("title", spec["title"]), provider="semantic",
            coverage=COV_SEMANTIC,
            confidence=f.get("confidence", "medium"),
            evidence=[ev], fix=f.get("fix", "")))
    return findings, rejects


def load_results(path: str | Path) -> tuple[list[Finding], list[str]]:
    """读入 Agent 回填的语义结果文件 → Finding(coverage=semantic)。"""
    return load_items(json.loads(Path(path).read_text(encoding="utf-8")))


def load_items(raw) -> tuple[list[Finding], list[str]]:
    """接受 {findings:[...]} 或数组（文件或 HTTP body 均可）。"""
    items = raw.get("findings", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("semantic results 须为 {findings:[...]} 或数组")
    return _validate_items(items)
