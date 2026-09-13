# -*- coding: utf-8 -*-
"""指定目标深度分析：对单个核心模块 / 单个业务插件做聚焦解析。

用于开发者想快速吃透某一块（或某插件被改动前自查）时，输出该目标的：
逐文件摘要、类/方法、函数、完整路由、导入依赖、manifest 校验细节、依赖关系。
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .config import RepoConfig
from .fs_scan import iter_repo_files
from .py_ast import parse_python_file


def analyze_target(cfg: RepoConfig, target_type: str, name: str,
                   parse_cache: dict, plugins: dict) -> dict:
    if target_type == "plugin":
        return _analyze_plugin(cfg, name, parse_cache, plugins)
    return _analyze_module(cfg, name, parse_cache, plugins)


def _analyze_module(cfg: RepoConfig, name: str, parse_cache: dict, plugins: dict) -> dict:
    root = cfg.repo_root
    mod_dir = root / name
    files: list[dict] = []
    for rel, fpath in _module_py_files(cfg, name):
        entry = parse_cache.get(str(rel)) or parse_python_file(fpath, str(rel))
        parse_cache[str(rel)] = entry
        files.append(_summarize_file(entry))

    imports: dict[str, int] = {}
    routes = []
    classes, funcs = [], []
    for f in files:
        entry = parse_cache.get(f["file"])
        for m in entry.get("imports", []):
            imports[m.split(".")[0]] = imports.get(m.split(".")[0], 0) + 1
        for r in entry.get("routes", []):
            routes.append({**r, "file": f["file"]})
        for c in entry.get("classes", []):
            classes.append({**c, "file": f["file"]})
        for fn in entry.get("functions", []):
            if fn["public"]:
                funcs.append({**fn, "file": f["file"]})

    # 哪些插件依赖此核心模块
    depended_by = []
    for p in plugins["items"]:
        for e in plugins["items"] and _plugin_core_refs(cfg, p, parse_cache):
            if e[0].split(".")[0] == name:
                depended_by.append({"plugin": p["identifier"], "refs": e[1]})

    return {
        "target_type": "module",
        "name": name,
        "path": str(mod_dir.relative_to(root)) if mod_dir.exists() else name,
        "locations": _loc_sum(files),
        "file_count": len(files),
        "files": [{"file": f["file"], "loc": f["loc_total"], "loc_code": f["loc_code"],
                   "classes": len(f["classes"]), "functions": len(f["functions"]),
                   "routes": len(f["routes"])} for f in files],
        "classes": classes,
        "functions": funcs,
        "routes": routes,
        "routes_count": len(routes),
        "imports_aggregated": dict(sorted(imports.items(), key=lambda kv: -kv[1])[:15]),
        "depended_by_plugins": depended_by,
    }


def _analyze_plugin(cfg: RepoConfig, name: str, parse_cache: dict, plugins: dict) -> dict:
    root = cfg.repo_root
    pdir = root / config.PLUGINS_DIR / name
    item = next((p for p in plugins["items"] if p["dir"] == name), None)
    files: list[dict] = []
    for rel, fpath in _plugin_py_files(cfg, name):
        entry = parse_cache.get(str(rel)) or parse_python_file(fpath, str(rel))
        parse_cache[str(rel)] = entry
        files.append(_summarize_file(entry))

    imports, routes, classes, funcs = {}, [], [], []
    plugin_classes = []
    for f in files:
        entry = parse_cache.get(f["file"])
        for m in entry.get("imports", []):
            imports[m.split(".")[0]] = imports.get(m.split(".")[0], 0) + 1
        for r in entry.get("routes", []):
            routes.append({**r, "file": f["file"]})
        for c in entry.get("classes", []):
            classes.append({**c, "file": f["file"]})
            if c.get("inherits_base_plugin"):
                plugin_classes.append({**c, "file": f["file"]})
        for fn in entry.get("functions", []):
            if fn["public"]:
                funcs.append({**fn, "file": f["file"]})

    meta = {
        "identifier": item["identifier"] if item else name,
        "name": item["name"] if item else "",
        "version": item["version"] if item else "",
        "agent_role": item["agent_role"] if item else "",
        "category": item["category"] if item else "",
        "description": item["description"] if item else "",
        "manifest_valid": item["manifest_valid"] if item else None,
        "manifest_errors": item["manifest_errors"] if item else [],
        "depends_on": item["depends_on"] if item else {},
        "capabilities": item["capabilities"] if item else [],
    }

    return {
        "target_type": "plugin",
        "name": name,
        "path": f"{config.PLUGINS_DIR}/{name}",
        "manifest": meta,
        "locations": _loc_sum(files),
        "file_count": len(files),
        "files": [{"file": f["file"], "loc": f["loc_total"], "loc_code": f["loc_code"],
                   "classes": len(f["classes"]), "functions": len(f["functions"]),
                   "routes": len(f["routes"])} for f in files],
        "classes": classes,
        "plugin_classes": plugin_classes,
        "functions": funcs,
        "routes": routes,
        "routes_count": len(routes),
        "imports_aggregated": dict(sorted(imports.items(), key=lambda kv: -kv[1])[:15]),
    }


# ------------------------------------------------------------------ 辅助 ----

def _summarize_file(entry: dict) -> dict:
    return {
        "file": entry["path"],
        "loc_total": entry["loc_total"],
        "loc_code": entry["loc_code"],
        "docstring": entry["docstring"],
        "classes": entry["classes"],
        "functions": entry["functions"],
        "routes": entry["routes"],
        "blueprints": entry["blueprints"],
        "imports": entry["imports"],
    }


def _loc_sum(files: list[dict]) -> dict:
    return {
        "loc_total": sum(f["loc_total"] for f in files),
        "loc_code": sum(f["loc_code"] for f in files),
    }


def _module_py_files(cfg: RepoConfig, name: str):
    for rel, fpath in iter_repo_files(cfg):
        if rel.parts[0] == name and rel.suffix == ".py":
            yield rel, fpath


def _plugin_py_files(cfg: RepoConfig, pid: str):
    for rel, fpath in iter_repo_files(cfg):
        if rel.parts[0] == config.PLUGINS_DIR and len(rel.parts) > 2 and rel.parts[1] == pid \
                and rel.suffix == ".py":
            yield rel, fpath


def _plugin_core_refs(cfg: RepoConfig, p: dict, parse_cache: dict):
    entries = []
    for rel, fpath in _plugin_py_files(cfg, p["dir"]):
        entry = parse_cache.get(str(rel)) or parse_python_file(fpath, str(rel))
        parse_cache[str(rel)] = entry
        for m in entry.get("imports", []):
            entries.append((m, str(rel)))
    return entries
