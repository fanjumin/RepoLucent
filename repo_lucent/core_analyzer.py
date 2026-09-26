# -*- coding: utf-8 -*-
"""系统核心分析器：识别核心模块、提取关键类/函数/Flask 应用与插件框架契约。

核心范围 = 顶层 Python 代码目录（排除 plugins/ 与排除目录）+ 仓库根目录入口脚本。
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .config import RepoConfig
from .fs_scan import iter_repo_files, read_text_safe
from .py_ast import parse_python_file

#: 参与"模块"识别与符号归集的源文件后缀（Python + C/C++/Arduino 两前端）。
SOURCE_SUFFIXES = config.PARSEABLE_EXTS


def _parse_source(fpath, rel: str) -> dict:
    """按后缀派发解析（缓存命中失败时的兜底路径，须与预热口径一致）。"""
    suffix = str(rel).rsplit(".", 1)[-1].lower() if "." in str(rel) else ""
    if "." + suffix in config.CXX_EXTS:
        from .cxx_ast import parse_cxx_file
        return parse_cxx_file(fpath, str(rel))
    return parse_python_file(fpath, str(rel))


def _plugin_core_dir() -> str:
    """插件框架核心目录名（profile.plugin_system.core_dir）；未声明返回空串。"""
    return str((config.PLUGIN_SYSTEM or {}).get("core_dir") or "")


def _pm_key(name: str) -> str:
    """插件框架目录下文件的 parse_cache 键。

    必须与预热（cli.py）及其他分析器严格同构：统一 str(Path(...))。
    历史版本这里硬编码 "plugin_manager/x.py"（POSIX 斜杠），而预热用
    str(Path(...))（Windows 为反斜杠），两者不匹配 → 这些文件每次运行都
    重新解析，且 AST 缓存永不命中。输出字段仍用 POSIX 风格，保持不变。
    """
    core_dir = _plugin_core_dir()
    return str(Path(core_dir) / name) if core_dir else name


def _discover_core_dirs(cfg: RepoConfig) -> list[str]:
    """核心目录 = 知识库收录目录 + 自动识别（含 >=1 个 .py 的顶层目录）。"""
    root = cfg.repo_root
    auto: list[str] = []
    for p in sorted(root.iterdir()):
        # 档位 A：component.dir 为空（无组件概念）时不排除任何目录；
        # 非空时排除组件目录（VeroRun 语义下即 plugins/）。
        if not p.is_dir() or (config.PLUGINS_DIR and p.name == config.PLUGINS_DIR) \
                or cfg.is_excluded_dir(p.name):
            continue
        if p.name in config.AUTO_CORE_EXCLUDE:
            continue
        has_src = any(f.suffix.lower() in SOURCE_SUFFIXES
                      for f in p.rglob("*") if f.is_file())
        if has_src:
            auto.append(p.name)
    known = [k for k in config.KNOWN_CORE_MODULES if (root / k).is_dir()]
    return sorted(set(known) | set(auto))


def analyze_core(cfg: RepoConfig, parse_cache: dict) -> dict:
    """分析系统核心。parse_cache 复用 AST 解析结果（键=相对路径字符串）。"""
    root = cfg.repo_root
    modules = []

    for name in _discover_core_dirs(cfg):
        dir_path = root / name
        py_files = [rel for rel, _ in iter_repo_files(cfg)
                    if rel.parts[0] == name and rel.suffix.lower() in SOURCE_SUFFIXES]

        classes, funcs, blueprints, routes = [], [], [], []
        import_roots: dict[str, int] = {}
        loc_total = loc_code = 0
        pkg_doc = None

        init_rel = Path(name) / "__init__.py"
        init_entry = parse_cache.get(str(init_rel))
        if init_entry:
            pkg_doc = init_entry.get("docstring")

        for rel in py_files:
            entry = parse_cache.get(str(rel)) or _parse_source(root / rel, str(rel))
            parse_cache[str(rel)] = entry
            loc_total += entry["loc_total"]
            loc_code += entry["loc_code"]
            for c in entry["classes"]:
                classes.append({**c, "file": str(rel)})
            for f in entry["functions"]:
                if f["public"]:
                    funcs.append({**f, "file": str(rel)})
            blueprints += [{**b, "file": str(rel)} for b in entry["blueprints"]]
            routes += [{**r, "file": str(rel)} for r in entry["routes"]]
            for m in entry["imports"]:
                rootmod = m.split(".")[0]
                import_roots[rootmod] = import_roots.get(rootmod, 0) + 1

        # 优先展示"有意义的类"：继承 BasePlugin / 有 docstring / 名字含 Plugin
        def class_weight(c):
            return (0 if c.get("inherits_base_plugin") else 1,
                    0 if c.get("docstring") else 1,
                    0 if c["name"].startswith("_") else 1,
                    c["name"].lower())
        classes.sort(key=class_weight)
        funcs.sort(key=lambda f: (0 if f["docstring"] else 1, f["name"].lower()))

        modules.append({
            "name": name,
            "description": config.KNOWN_CORE_MODULES.get(name) or (pkg_doc or "（自动识别的源码模块）"),
            "known": name in config.KNOWN_CORE_MODULES,
            "py_files": len(py_files),
            "loc": loc_total,
            "loc_code": loc_code,
            "package_docstring": pkg_doc,
            "classes": [
                {"name": c["name"], "bases": c["bases"], "docstring": c["docstring"],
                 "file": c["file"],
                 "methods": [
                     {"name": m["name"], "signature": m["signature"],
                      "docstring": m["docstring"], "abstract": m["abstract"]}
                     for m in c["methods"] if not m["name"].startswith("_")
                 ][:cfg.max_methods_per_class]}
                for c in classes[:cfg.max_classes_per_module]
            ],
            "class_total": len(classes),
            "functions": [
                {"name": f["name"], "signature": f["signature"], "docstring": f["docstring"],
                 "file": f["file"]}
                for f in funcs[:cfg.max_funcs_per_module]
            ],
            "function_total": len(funcs),
            "blueprints": blueprints,
            "routes": routes,
            "route_count": len(routes),
            "import_roots": dict(sorted(import_roots.items(), key=lambda kv: -kv[1])[:10]),
        })

    # 仓库根目录入口脚本
    entry_files = []
    for rel, _ in iter_repo_files(cfg):
        if len(rel.parts) == 1 and rel.suffix == ".py":
            entry = parse_cache.get(str(rel)) or _parse_source(root / rel, str(rel))
            parse_cache[str(rel)] = entry
            entry_files.append({
                "file": str(rel),
                "docstring": entry["docstring"],
                "loc": entry["loc_total"],
                # v2.1.0 端点全景：根级入口脚本（如 VeroRun auth_server.py）的
                # 路由也是端点事实；此前仅目录型核心模块收集路由，根级 app.route
                # 整体缺失（"统计不完整"的最后一个来源）。
                "routes": [{**r, "file": str(rel)} for r in entry["routes"]],
                "route_count": len(entry["routes"]),
            })
    entry_files.sort(key=lambda e: -e["loc"])

    return {
        "modules": modules,
        "module_count": len(modules),
        "entry_files": entry_files,
        "plugin_system": _analyze_plugin_system(cfg, parse_cache),
    }


def _analyze_plugin_system(cfg: RepoConfig, parse_cache: dict) -> dict:
    """聚焦插件框架契约：base_class 接口、Manager 公共 API、发现规则、钩子/事件。

    口径全部来自 profile.plugin_system。未启用插件体系（或未声明 core_dir）时
    返回**同键集的空段**——产物 schema 不随 profile 变化，"是否启用"由配置层
    （config.plugin_system_enabled()）承载。
    """
    empty: dict = {"base_plugin": None, "manager_api": None,
                   "discovery_rules": [], "support_modules": [], "hooks_events": {}}
    ps = config.PLUGIN_SYSTEM or {}
    if not config.plugin_system_enabled():
        return empty
    core_dir = str(ps.get("core_dir") or "")
    if not core_dir:
        return empty

    files = ps.get("files") or {}
    base_name = str(files.get("base") or "base.py")
    manager_name = str(files.get("manager") or "manager.py")
    discovery_name = str(files.get("discovery") or "discovery.py")
    base_class = str(ps.get("base_class") or "BasePlugin")
    manager_pat = str(ps.get("manager_class_pattern") or "Manager")

    root = cfg.repo_root
    pm_dir = root / core_dir
    out: dict = {"base_plugin": None, "manager_api": None,
                 "discovery_rules": [], "support_modules": [], "hooks_events": {}}

    base_file = pm_dir / base_name
    if base_file.exists():
        k = _pm_key(base_name)
        entry = parse_cache.get(k) or parse_python_file(base_file, k)
        parse_cache[k] = entry
        for c in entry["classes"]:
            if c["name"] == base_class:
                out["base_plugin"] = {
                    "file": f"{core_dir}/{base_name}",
                    "docstring": c["docstring"],
                    "methods": [
                        {"name": m["name"], "signature": m["signature"],
                         "docstring": m["docstring"], "abstract": m["abstract"]}
                        for m in c["methods"] if not m["name"].startswith("_")
                    ],
                }
                break

    manager_file = pm_dir / manager_name
    if manager_file.exists():
        k = _pm_key(manager_name)
        entry = parse_cache.get(k) or parse_python_file(manager_file, k)
        parse_cache[k] = entry
        for c in entry["classes"]:
            if manager_pat in c["name"]:
                out["manager_api"] = {
                    "class": c["name"],
                    "file": f"{core_dir}/{manager_name}",
                    "methods": [
                        {"name": m["name"], "signature": m["signature"], "docstring": m["docstring"]}
                        for m in c["methods"] if not m["name"].startswith("_")
                    ],
                }
                break

    discovery_file = pm_dir / discovery_name
    if discovery_file.exists():
        k = _pm_key(discovery_name)
        entry = parse_cache.get(k) or parse_python_file(discovery_file, k)
        parse_cache[k] = entry
        out["discovery_rules"] = (entry.get("docstring_full") or entry.get("docstring") or "").splitlines()

    # 支撑模块一览（base / manager / discovery 之外的其余 .py）
    skip = {base_name, manager_name, discovery_name}
    if pm_dir.is_dir():
        for py in sorted(pm_dir.glob("*.py")):
            if py.name in skip:
                continue
            rel = f"{core_dir}/{py.name}"                # 输出字段：保持 POSIX 风格
            k = _pm_key(py.name)                         # 缓存键：与预热同构
            entry = parse_cache.get(k) or parse_python_file(py, k)
            parse_cache[k] = entry
            out["support_modules"].append({
                "module": f"{core_dir}.{py.stem}",
                "docstring": entry["docstring"],
                "classes": len(entry["classes"]),
                "functions": len(entry["functions"]),
            })

    # Hooks 与事件名（尽力提取常量字符串）
    for target, label in ((str(files.get("hooks") or "hooks.py"), "hooks"),
                          (str(files.get("event_bus") or "event_bus.py"), "events")):
        f = pm_dir / target
        if not f.exists():
            continue
        rel = f"{core_dir}/{target}"                     # 输出字段：保持 POSIX 风格
        k = _pm_key(target)                              # 缓存键：与预热同构
        entry = parse_cache.get(k) or parse_python_file(f, k)
        parse_cache[k] = entry
        names: list[str] = []
        for c in entry["classes"]:
            names.append(c["name"])
        for fn in entry["functions"]:
            if fn["public"]:
                names.append(fn["name"])
        out["hooks_events"][label] = names[:40]
    return out
