# -*- coding: utf-8 -*-
"""SPEC001-005：基于插件 manifest / 仓库规范的数据派生规则。

纯 data 衍生，无源码扫描；对缺失 plugins 的安全仓库返回空列表。

v2.1.1 口径修正（SPEC001）：核心角色集合**不再内核硬编码**——原内置
{chat, assistant, …} 与 VeroRun 实际角色表（agent_matrix/roles/*.yaml）
完全脱节，40/40 插件全量误报。现读 profile.agent_roles（from_glob +
name_strip），未声明或角色源解析为空 → SPEC001 整体跳过，与
gates.route_prefix_pattern=None 即跳过的既有语义一致：宁可不判，不臆判。
"""
from __future__ import annotations

import re
from pathlib import Path

from ._types import Finding, SEVERITY_ERROR, SEVERITY_WARNING

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _profile_roles(cfg) -> set[str] | None:
    """按 profile.agent_roles 从仓内权威源读角色集合；不可判 → None（跳过）。"""
    from ..settings import profile_get
    sec = profile_get("agent_roles", None)
    if not isinstance(sec, dict):
        return None
    glob_pat = str(sec.get("from_glob") or "")
    root = getattr(cfg, "repo_root", None)
    if not glob_pat or root is None:
        return None
    strip = str(sec.get("name_strip") or "")
    rx = re.compile(strip) if strip else None
    roles: set[str] = set()
    try:
        for p in sorted(Path(root).glob(glob_pat)):
            if not p.is_file():
                continue
            nm = p.stem
            if rx:
                m = rx.match(nm)
                if m and m.lastindex:
                    nm = m.group(1)
            roles.add(nm.strip().lower())
    except OSError:
        return None
    return roles or None


def run_spec(ctx) -> list[Finding]:
    findings: list[Finding] = []
    plugins = (ctx.data.get("plugins") or {}).get("items") or []
    roles = _profile_roles(ctx.cfg)
    for p in plugins:
        ident = p.get("identifier", "?")
        # SPEC001 agent_role 不在核心角色集合（角色源不可判时跳过，见模块 docstring）
        role = (p.get("agent_role") or "").strip().lower()
        if roles is not None and role and role not in roles:
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
