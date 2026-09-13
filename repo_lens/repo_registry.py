# -*- coding: utf-8 -*-
"""多仓库注册表：~/.repolens/repos.json。

条目形态：{"name": str, "path": str, "profile": str|null, "added_at": str}
- profile：预设名（<tool>/profiles/<name>.json 或 ~/.repolens/profiles/<name>.json）；
  null/空 = 内置 verorun 口径。
- 纯本地开发辅助数据，不入 Git；任何损坏都降级为空注册表，不让 CLI/服务崩。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff.-]{1,64}$")


def registry_path() -> Path:
    return Path.home() / ".repolens" / "repos.json"


def load_registry() -> dict:
    p = registry_path()
    if not p.is_file():
        return {"version": 1, "repos": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "repos": []}
    if not isinstance(d, dict) or not isinstance(d.get("repos"), list):
        return {"version": 1, "repos": []}
    return d


def save_registry(reg: dict) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_repos() -> list[dict]:
    return [r for r in load_registry().get("repos", []) if isinstance(r, dict) and r.get("name")]


def resolve(name: str) -> dict | None:
    for r in list_repos():
        if r.get("name") == name:
            return r
    return None


def list_profiles() -> list[str]:
    """可用 profile 预设名：内置 verorun + <tool>/profiles/*.json + 用户级。"""
    from .settings import _TOOL_ROOT
    names = {"verorun"}
    for base in (_TOOL_ROOT / "profiles", Path.home() / ".repolens" / "profiles"):
        if base.is_dir():
            for f in sorted(base.glob("*.json")):
                names.add(f.stem)
    return sorted(names)


def add_repo(name: str, path: str, profile: str | None = None) -> dict:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"仓库名不合法（1-64 位字母/数字/汉字/./-/_）：{name!r}")
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"仓库路径不存在或不是目录：{path}")
    reg = load_registry()
    if resolve(name):
        raise ValueError(f"仓库名已存在：{name}")
    entry = {"name": name, "path": str(p.resolve()),
             "profile": (profile or None),
             "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    reg["repos"].append(entry)
    save_registry(reg)
    return entry


def remove_repo(name: str) -> bool:
    reg = load_registry()
    before = len(reg["repos"])
    reg["repos"] = [r for r in reg["repos"] if r.get("name") != name]
    if len(reg["repos"]) == before:
        return False
    save_registry(reg)
    return True
