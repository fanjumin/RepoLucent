# -*- coding: utf-8 -*-
"""开发规范提取器：manifest 必填项、插件标准章节、AGENTS.md 规则索引、重点文档清单。

原则：只摘取真实存在于仓库文档中的信息（标题/字段/枚举），不做主观发挥；
完整规范以文档原文为准，本工具输出"索引 + 摘要"用于快速定位。
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from . import config
from .config import RepoConfig
from .fs_scan import read_text_safe


def extract_standards(cfg: RepoConfig, plugin_result: dict) -> dict:
    root = cfg.repo_root
    out: dict = {
        "manifest_required": plugin_result["manifest_required"],
        "manifest_enums": plugin_result["manifest_enums"],
        "manifest_schema_file": plugin_result["manifest_schema_file"],
        "plugin_standard": None,
        "agents_md": None,
        "key_docs": [],
        "plugin_templates": plugin_result["framework"]["templates"],
        "base_helpers": plugin_result["framework"]["base_helpers"],
    }

    # ---- 插件标准文档：版本与章节索引 ----
    # 档位 A：glob 模式来自 profile.standard_doc_pattern（默认 docs/plugin-standard-v*.md）；
    # 空字符串 = 不提取规范文档（非 VeroRun 仓库的 generic-python 预设即如此）。
    std_pattern = config.PLUGIN_STANDARD_RE
    matches = sorted(glob.glob(str(root / std_pattern))) if std_pattern else []
    if matches:
        std_path = Path(matches[-1])  # 取最新版本
        text = read_text_safe(std_path)
        title = ""
        sections: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("## "):
                sections.append(s[3:].strip()[:120])
            elif not title and s.startswith("# "):
                title = s[2:].strip()[:160]
        out["plugin_standard"] = {
            "file": str(std_path.relative_to(root)),
            "title": title,
            "sections": sections[:40],
        }

    # ---- AGENTS.md 顶层规则索引 ----
    agents = root / "AGENTS.md"
    if agents.exists():
        text = read_text_safe(agents)
        headings = [ln.strip().lstrip("#").strip()[:120]
                    for ln in text.splitlines() if ln.strip().startswith("###")]
        out["agents_md"] = {
            "file": "AGENTS.md",
            "headings": headings[:30],
            "note": "AGENTS.md 为项目最高优先级约束文件，开发前必须完整阅读原文。",
        }

    # ---- 重点文档索引 ----
    for rel in config.KEY_DOCS:
        f = root / rel
        if not f.exists():
            continue
        text = read_text_safe(f)
        title = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s[2:].strip()[:120]
                break
        out["key_docs"].append({
            "file": rel,
            "title": title or f.name,
            "bytes": f.stat().st_size,
        })
    return out
