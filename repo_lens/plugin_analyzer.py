# -*- coding: utf-8 -*-
"""业务插件分析器：镜像 plugin_manager/discovery.py 的发现规则，
解析每个插件的 plugin.json 清单（含校验）、BasePlugin 子类、Flask 路由与文档。

发现规则（与系统运行时一致）：
  1. 必须是 plugins/<name>/ 子目录
  2. 必须包含 __init__.py
  3. 必须包含 plugin.json（有效的 JSON）
  4. 忽略 _ 和 . 开头的目录
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config
from .config import RepoConfig
from .fs_scan import iter_repo_files, read_text_safe
from .py_ast import parse_python_file

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]+$")


def discover_plugin_dirs(cfg: RepoConfig) -> tuple[list[Path], list[dict]]:
    """返回 (合法插件目录列表, 被拒绝的目录及原因)。"""
    root = cfg.repo_root / config.PLUGINS_DIR
    valid, rejected = [], []
    if not root.is_dir():
        return valid, rejected
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith(("_", ".")):
            continue  # 规则 4：_base/_templates 等框架资源单独处理
        init_ok = (p / "__init__.py").exists()          # 规则 2
        manifest_ok = (p / config.MANIFEST_NAME).exists()  # 规则 3
        if init_ok and manifest_ok:
            valid.append(p)
        else:
            reasons = []
            if not init_ok:
                reasons.append("缺少 __init__.py")
            if not manifest_ok:
                reasons.append(f"缺少 {config.MANIFEST_NAME}")
            rejected.append({"dir": p.name, "reasons": reasons})
    return valid, rejected


def _load_manifest(path: Path) -> tuple[dict | None, list[str]]:
    text = read_text_safe(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [f"plugin.json JSON 解析失败: {e}"]
    if not isinstance(data, dict):
        return None, ["plugin.json 顶层必须是 JSON 对象"]
    return data, []


def _validate_manifest(m: dict, schema_required: list[str]) -> list[str]:
    errors: list[str] = []
    for field in schema_required:
        v = m.get(field)
        if v is None or v == "" or v == []:
            errors.append(f"缺少必填字段: {field}")
    ident = m.get("identifier", "")
    if ident and not _IDENTIFIER_RE.match(ident):
        errors.append(f"identifier 不符合 ^[a-z0-9_]+$: {ident!r}")
    ver = str(m.get("version", ""))
    if ver and not _SEMVER_RE.match(ver):
        errors.append(f"version 不符合语义化版本 X.Y.Z: {ver!r}")
    mav = str(m.get("min_app_version", ""))
    if m.get("min_app_version") and not _SEMVER_RE.match(mav):
        errors.append(f"min_app_version 不符合 X.Y.Z: {mav!r}")
    return errors


def _load_schema_required(cfg: RepoConfig) -> tuple[list[str], dict, str | None]:
    """从 docs/plugin-manifest.schema.json 动态读取必填字段与枚举。"""
    schema_path = cfg.repo_root / "docs" / "plugin-manifest.schema.json"
    if not schema_path.exists():
        return list(config.MANIFEST_REQUIRED_FALLBACK), {}, None
    data, errs = _load_manifest(schema_path)
    if data is None:
        return list(config.MANIFEST_REQUIRED_FALLBACK), {}, None
    required = data.get("required") or list(config.MANIFEST_REQUIRED_FALLBACK)
    enums = {}
    for prop, spec in (data.get("properties") or {}).items():
        if isinstance(spec, dict) and "enum" in spec:
            enums[prop] = spec["enum"]
    return required, enums, "docs/plugin-manifest.schema.json"


def analyze_plugins(cfg: RepoConfig, parse_cache: dict) -> dict:
    """分析全部业务插件。

    档位 A 组件短路：profile.component.dir 为空字符串（无组件概念，如
    generic-python 预设）时，跳过插件分析，返回空结果（不抛异常），
    下游 gate / interactions / report 各章节自然为空。
    """
    root = cfg.repo_root
    if not config.PLUGINS_DIR:
        return {
            "count": 0,
            "rejected_dirs": [],
            "manifest_required": [],
            "manifest_schema_file": None,
            "manifest_enums": {},
            "items": [],
            "framework": {"base_helpers": [], "templates": []},
        }
    required, enums, schema_file = _load_schema_required(cfg)
    dirs, rejected = discover_plugin_dirs(cfg)

    items: list[dict] = []
    for pdir in dirs:
        rel_base = f"{config.PLUGINS_DIR}/{pdir.name}"
        manifest, m_errors = _load_manifest(pdir / config.MANIFEST_NAME)
        if manifest is None:
            manifest = {}
            m_errors = m_errors or ["plugin.json 无法解析"]
        else:
            m_errors = m_errors + _validate_manifest(manifest, required)

        py_files = [rel for rel, _ in iter_repo_files(cfg)
                    if rel.parts[0] == config.PLUGINS_DIR and len(rel.parts) > 1
                    and rel.parts[1] == pdir.name and rel.suffix == ".py"]

        loc_total = loc_code = 0
        plugin_classes, blueprints, routes = [], [], []
        entry_doc = None
        for rel in py_files:
            entry = parse_cache.get(str(rel)) or parse_python_file(root / rel, str(rel))
            parse_cache[str(rel)] = entry
            loc_total += entry["loc_total"]
            loc_code += entry["loc_code"]
            if rel.name == "__init__.py" and rel.parts[-1] == "__init__.py" \
                    and len(rel.parts) == 3:
                entry_doc = entry["docstring"]
            for c in entry["classes"]:
                if c["inherits_base_plugin"]:
                    plugin_classes.append({"name": c["name"], "file": str(rel)})
            blueprints += [{**b, "file": str(rel)} for b in entry["blueprints"]]
            routes += [{**r, "file": str(rel)} for r in entry["routes"]]

        i18n_dir = pdir / "i18n"
        locales = sorted(f.stem for f in i18n_dir.glob("*.yml")) if i18n_dir.is_dir() else []
        locales += sorted(f.stem for f in i18n_dir.glob("*.yaml")) if i18n_dir.is_dir() else []

        docs = sorted({rel.name for rel, _ in iter_repo_files(cfg)
                       if rel.parts[0] == config.PLUGINS_DIR and len(rel.parts) == 3
                       and rel.parts[1] == pdir.name
                       and rel.suffix.lower() in (".md", ".txt")})

        items.append({
            "identifier": manifest.get("identifier", pdir.name),
            "dir": pdir.name,
            "name": manifest.get("name", ""),
            "version": str(manifest.get("version", "")),
            "category": manifest.get("category", ""),
            "agent_role": manifest.get("agent_role", ""),
            "capabilities": manifest.get("capabilities", []),
            "description": (manifest.get("description") or "")[:160],
            "author": manifest.get("author", ""),
            "min_app_version": str(manifest.get("min_app_version", "")),
            "price_type": manifest.get("price_type", "free"),
            "permissions": manifest.get("permissions", []),
            "depends_on": manifest.get("depends_on", {}) or {},
            "dependencies": manifest.get("dependencies", {}) or {},
            "hooks_keys": sorted((manifest.get("hooks") or {}).keys()),
            "path": rel_base,
            "manifest_valid": not m_errors,
            "manifest_errors": m_errors,
            "py_files": len(py_files),
            "loc": loc_total,
            "loc_code": loc_code,
            "entry_docstring": entry_doc,
            "plugin_classes": plugin_classes,
            "blueprints": blueprints,
            "routes": routes[:cfg.max_routes_per_plugin],
            "route_count": len(routes),
            "route_truncated": len(routes) > cfg.max_routes_per_plugin,
            "i18n_locales": sorted(set(locales)),
            "has_templates": (pdir / "templates").is_dir(),
            "has_static": (pdir / "static").is_dir(),
            "has_tests": (pdir / "tests").is_dir() or any("test" in r.parts[-1].lower() for r in py_files),
            "docs": docs,
        })

    # 框架资源：_base 辅助库与官方模板
    framework = {"base_helpers": [], "templates": []}
    base_dir = root / config.PLUGINS_DIR / "_base"
    if base_dir.is_dir():
        for py in sorted(base_dir.glob("*.py")):
            if py.name == "__init__.py":
                continue
            # 缓存键必须与预热（cli.py）同构：str(Path(...))。
            # 历史版本用 f"{PLUGINS_DIR}/_base/{py.name}"（POSIX 斜杠），
            # 与预热的 str(Path(...)) 在 Windows 上不等，导致重复解析且缓存永不命中。
            rel = str(Path(config.PLUGINS_DIR) / "_base" / py.name)
            entry = parse_cache.get(rel) or parse_python_file(py, rel)
            parse_cache[rel] = entry
            framework["base_helpers"].append({
                "module": f"plugins._base.{py.stem}",
                "docstring": entry["docstring"],
            })
    tpl_dir = root / config.PLUGINS_DIR / "_templates"
    if tpl_dir.is_dir():
        for td in sorted(tpl_dir.iterdir()):
            if not td.is_dir():
                continue
            mj = td / config.MANIFEST_NAME
            meta, _ = _load_manifest(mj) if mj.exists() else ({}, [])
            framework["templates"].append({
                "dir": f"{config.PLUGINS_DIR}/_templates/{td.name}",
                "name": meta.get("name", td.name),
                "version": meta.get("version", ""),
                "description": (meta.get("description") or "")[:120],
            })

    return {
        "count": len(items),
        "rejected_dirs": rejected,
        "manifest_required": required,
        "manifest_schema_file": schema_file,
        "manifest_enums": enums,
        "items": sorted(items, key=lambda x: x["identifier"]),
        "framework": framework,
    }
