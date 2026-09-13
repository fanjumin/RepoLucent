# -*- coding: utf-8 -*-
"""审计报告版本化：把一次审计的"结论指纹"存基线、并与基线对比（复用 snapshot.py 的 history 约定）。

指纹只含确定性、可 diff 的字段（裁决/各级别与维度计数/命中规则集/覆盖度），不含时间戳，
因此同结论两次跑逐字节一致。与脚本库基线隔离在不同子目录：out/history/audit/。
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import snapshot as S


def fingerprint(report_dict: dict) -> dict:
    v = report_dict.get("verdict", {})
    s = report_dict.get("summary", {})
    cov = {d: {"rule_only": c["rule_only"]["ran"], "rule_only_hits": c["rule_only"]["findings"]}
           for d, c in report_dict.get("coverage_matrix", {}).items()}
    rules = sorted({f"{f['dimension']}/{f['rule_id']}" for f in report_dict.get("findings", [])})
    return {
        "kind": "audit_report",
        "dims": report_dict.get("dimensions", []),
        "decision": v.get("decision"),
        "blocking": v.get("blocking"),
        "by_severity": s.get("by_severity", {}),
        "by_dimension": s.get("by_dimension", {}),
        "coverage": cov,
        "hit_rules": rules,
    }


def _audit_hist(out_dir: Path) -> Path:
    return S.history_dir(out_dir) / "audit"


def save(out_dir: Path, name: str, report_dict: dict) -> Path:
    d = _audit_hist(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (S._safe_name(name) + ".json")
    p.write_text(S._dumps(fingerprint(report_dict)), encoding="utf-8", newline="\n")
    return p


def load(out_dir: Path, name: str) -> dict | None:
    d = _audit_hist(out_dir)
    p = d / (S._safe_name(name) + ".json")
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_baselines(out_dir: Path) -> list[str]:
    d = _audit_hist(out_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def diff(base: dict, cur: dict) -> dict:
    b_rules, c_rules = set(base["hit_rules"]), set(cur["hit_rules"])
    changed_sev = {k: (base["by_severity"].get(k, 0), cur["by_severity"].get(k, 0))
                   for k in set(base["by_severity"]) | set(cur["by_severity"])
                   if base["by_severity"].get(k, 0) != cur["by_severity"].get(k, 0)}
    return {
        "decision": {"baseline": base["decision"], "current": cur["decision"],
                     "changed": base["decision"] != cur["decision"]},
        "blocking": {"baseline": base.get("blocking"), "current": cur.get("blocking")},
        "rules_added": sorted(c_rules - b_rules),
        "rules_removed": sorted(b_rules - c_rules),
        "by_severity_changed": changed_sev,
    }


def render_diff_md(base_name: str, d: dict) -> str:
    L = [f"## 审计结论对比（基线 `{base_name}` → 本次）", ""]
    dec = d["decision"]
    icon = "✅" if dec["current"] == "releasable" else "⛔"
    L.append(f"- 裁决：{dec['baseline']} → {icon} {dec['current']}"
             + ("（变化）" if dec["changed"] else "（不变）"))
    L.append(f"- 阻断命中：{d['blocking']['baseline']} → {d['blocking']['current']}")
    L.append(f"- 新增命中规则：{', '.join(d['rules_added']) or '—'}")
    L.append(f"- 消失命中规则：{', '.join(d['rules_removed']) or '—'}")
    if d["by_severity_changed"]:
        L.append("- 各级别增减：" + "、".join(
            f"{k} {v[0]}→{v[1]}" for k, v in d["by_severity_changed"].items()))
    return "\n".join(L) + "\n"
