# -*- coding: utf-8 -*-
"""SPEC001-005：基于插件 manifest / 仓库规范的数据派生规则。

纯 data 衍生，无源码扫描；对缺失 plugins 的安全仓库返回空列表。
"""
from __future__ import annotations

import re

from ._types import Finding, SEVERITY_ERROR, SEVERITY_WARNING

_CORE_ROLES = {
    "chat", "assistant", "analyzer", "scheduler", "notifier",
    "connector", "tool", "retriever", "agent", "workflow",
}
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def run_spec(ctx) -> list[Finding]:
    findings: list[Finding] = []
    plugins = (ctx.data.get("plugins") or {}).get("items") or []
    for p in plugins:
        ident = p.get("identifier", "?")
        # SPEC001 agent_role 不在核心角色集合
        role = (p.get("agent_role") or "").strip().lower()
        if role and role not in _CORE_ROLES:
            findings.append(Finding(
                "SPEC001", SEVERITY_ERROR, "spec", ident, None, None,
                f"agent_role={role!r} 不在核心角色集合",
                f"agent_role={p.get('agent_role')!r}"))
        # SPEC002 capabilities 为空
        if not (p.get("capabilities") or []):
            findings.append(Finding(
                "SPEC002", SEVERITY_ERROR, "spec", ident, None, None,
                "capabilities 为空", "capabilities=[]"))
        # SPEC003 缺 i18n 目录
        if not (p.get("i18n_locales") or []):
            findings.append(Finding(
                "SPEC003", SEVERITY_WARNING, "spec", ident, None, None,
                "插件缺 i18n/ 国际化目录", f"i18n_locales={p.get('i18n_locales')}"))
        # SPEC004 缺 README/文档
        docs = p.get("docs")
        readme = docs.get("readme") if isinstance(docs, dict) else docs
        if not readme:
            findings.append(Finding(
                "SPEC004", SEVERITY_WARNING, "spec", ident, None, None,
                "插件缺 README/文档", f"docs={docs}"))
        # SPEC005 version / min_app_version 非语义化
        ver = str(p.get("version") or "")
        if ver and not _SEMVER_RE.match(ver):
            findings.append(Finding(
                "SPEC005", SEVERITY_ERROR, "spec", ident, None, None,
                f"version={ver!r} 不符合 X.Y.Z 语义化版本", f"version={ver!r}"))
        mav = str(p.get("min_app_version") or "")
        if mav and not _SEMVER_RE.match(mav):
            findings.append(Finding(
                "SPEC005", SEVERITY_ERROR, "spec", ident, None, None,
                f"min_app_version={mav!r} 不符合 X.Y.Z 语义化版本",
                f"min_app_version={mav!r}"))
    return findings
