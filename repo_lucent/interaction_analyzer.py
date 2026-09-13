# -*- coding: utf-8 -*-
"""交互关系分析器：核心 ↔ 插件的导入依赖矩阵、插件间依赖、边界违规检查。

边界原则（VeroRun 架构约定）：
- 插件 → 核心：通过 plugin_manager 注入的运行时引用（self.manager/self.app）、
  plugins._base 辅助库、shared/i18n 公共设施——这是合法方向。
- 核心 → 业务插件：除 plugin_manager 的动态加载机制外，核心模块不应直接
  import 具体业务插件；出现直接导入即作为"边界观察项"输出，供架构评审关注。
"""
from __future__ import annotations

import re
from collections import Counter

from . import config
from .config import RepoConfig
from .py_ast import parse_python_file

_PLUGIN_MOD_RE = re.compile(r"^plugins\.([a-z0-9_]+)")


def analyze_interactions(cfg: RepoConfig, parse_cache: dict, core: dict, plugins: dict) -> dict:
    root = cfg.repo_root
    plugin_ids = {p["dir"] for p in plugins["items"]}

    core_import_counter: Counter[str] = Counter()
    base_helper_counter: Counter[str] = Counter()
    plugin_to_plugin: dict[str, set] = {}
    core_to_plugin: list[dict] = []   # 边界观察项
    base_class_users = 0
    class_users = 0

    for p in plugins["items"]:
        pid = p["dir"]
        if p["plugin_classes"]:
            class_users += 1
        deps = set()
        for key in ("depends_on", "dependencies"):
            raw = p.get(key) or {}
            if isinstance(raw, dict):
                deps |= set(raw.keys())
            elif isinstance(raw, list):
                deps |= set(raw)

        for rel, fpath in _iter_plugin_files(cfg, pid):
            entry = parse_cache.get(str(rel)) or parse_python_file(fpath, str(rel))
            parse_cache[str(rel)] = entry
            for m in entry["imports"]:
                root_mod = m.split(".")[0]
                if root_mod in config.KNOWN_CORE_MODULES or root_mod in (
                        "plugin_manager", "orchestrator", "agent_matrix", "shared",
                        "i18n", "providers", "admin", "main_site"):
                    core_import_counter[root_mod] += 1
                if m.startswith("plugins._base."):
                    base_helper_counter[m.split(".")[2]] += 1
                pm = _PLUGIN_MOD_RE.match(m)
                if pm and pm.group(1) != pid and pm.group(1) in plugin_ids:
                    deps.add(pm.group(1))
        if deps:
            plugin_to_plugin[pid] = deps

    base_class_users = class_users  # 有 BasePlugin 子类的插件数

    # 核心 → 插件 直接导入观察项（plugin_manager 的动态加载不算直接导入）
    plugin_manager_files = set()
    for rel, _ in _iter_module_files(cfg, "plugin_manager"):
        plugin_manager_files.add(str(rel))
    for mod in core["modules"]:
        if mod["name"] == "plugin_manager":
            continue
        for rel, fpath in _iter_module_files(cfg, mod["name"]):
            entry = parse_cache.get(str(rel)) or parse_python_file(fpath, str(rel))
            parse_cache[str(rel)] = entry
            for m in entry["imports"]:
                pm = _PLUGIN_MOD_RE.match(m)
                if pm and pm.group(1) in plugin_ids:
                    core_to_plugin.append({"file": str(rel), "imports": m,
                                           "module": mod["name"]})

    plugin_dep_edges = [
        {"from": f, "to": sorted(t)} for f, t in sorted(plugin_to_plugin.items())
    ]

    return {
        "plugins_import_core": [
            {"module": k, "import_count": v}
            for k, v in core_import_counter.most_common(15)
        ],
        "plugins_use_base_helpers": [
            {"helper": k, "import_count": v}
            for k, v in base_helper_counter.most_common(10)
        ],
        "plugin_to_plugin": plugin_dep_edges,
        "base_plugin_inheritance": {
            "plugins_with_base_class": base_class_users,
            "plugins_total": plugins["count"],
        },
        "boundary_observations": {
            "rule": "核心模块不应直接 import 业务插件（plugin_manager 动态加载除外）",
            "violations": core_to_plugin,
            "checked_files": sum(m["py_files"] for m in core["modules"]),
        },
    }


def _iter_plugin_files(cfg: RepoConfig, pid: str):
    for rel, fpath in _walk(cfg):
        if rel.parts[0] == config.PLUGINS_DIR and len(rel.parts) > 2 and rel.parts[1] == pid \
                and rel.suffix == ".py":
            yield rel, fpath


def _iter_module_files(cfg: RepoConfig, mod: str):
    for rel, fpath in _walk(cfg):
        if rel.parts[0] == mod and rel.suffix == ".py":
            yield rel, fpath


def _walk(cfg: RepoConfig):
    # 延迟导入避免与 fs_scan 循环依赖
    from .fs_scan import iter_repo_files
    yield from iter_repo_files(cfg)
