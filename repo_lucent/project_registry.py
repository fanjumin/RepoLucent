# -*- coding: utf-8 -*-
"""项目注册表：~/.repolucent/projects.json（内嵌成员，自包含）。

项目 = 多个仓库/路径的统计聚合单位（如 verorun-core + verorun-desktop）；
成员条目 {name, path, profile, scopes} 直接内嵌，不引用全局 repos.json ——
项目成员是"要统计的路径"，不必先注册成可切换仓库，增删互不影响。

- scopes：相对成员根的子目录名列表（如 ["plugins"]），用于仓库内子范围统计；
  写入时逐一校验必须是真实存在的直接子目录，防手滑。
- 与 packs/gitflow 的"仓库组"（~/.verorun/repos.yaml，批量 git 操作）是两回事，
  互不复用、互不影响。
- 纯本地开发辅助数据，不入 Git；任何损坏降级为空注册表，不让 CLI/服务崩。
- 测试可用环境变量 REPO_LUCENT_HOME 重定向根目录（默认 Path.home()）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff.-]{1,64}$")
_SCOPE_RE = re.compile(r"^[^\\/:*?\"<>|\s]{1,96}$")   # 单段相对目录名，禁分隔符与盘符


def home_dir() -> Path:
    """注册表根目录：REPO_LUCENT_HOME 优先（测试重定向），否则用户主目录。"""
    return Path(os.environ.get("REPO_LUCENT_HOME") or Path.home())


def projects_path() -> Path:
    return home_dir() / ".repolucent" / "projects.json"


def load() -> dict:
    p = projects_path()
    if not p.is_file():
        return {"version": 1, "projects": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 损坏降级为空表
        return {"version": 1, "projects": []}
    if not isinstance(d, dict) or not isinstance(d.get("projects"), list):
        return {"version": 1, "projects": []}
    return d


def save(reg: dict) -> None:
    p = projects_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_projects() -> list[dict]:
    return [x for x in load().get("projects", [])
            if isinstance(x, dict) and x.get("name")]


def resolve_project(name: str) -> dict | None:
    for x in list_projects():
        if x.get("name") == name:
            return x
    return None


def add_project(name: str) -> dict:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"项目名不合法（1-64 位字母/数字/汉字/./-/_）：{name!r}")
    reg = load()
    if resolve_project(name):
        raise ValueError(f"项目名已存在：{name}")
    ent = {"name": name, "repos": [],
           "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    reg["projects"].append(ent)
    save(reg)
    return ent


def remove_project(name: str) -> bool:
    reg = load()
    before = len(reg["projects"])
    reg["projects"] = [x for x in reg["projects"] if x.get("name") != name]
    if len(reg["projects"]) == before:
        return False
    save(reg)
    return True


def resolve_member(project: dict, name: str) -> dict | None:
    for m in project.get("repos") or []:
        if isinstance(m, dict) and m.get("name") == name:
            return m
    return None


def _validate_scopes(path: Path, scopes: list[str]) -> list[str]:
    """scope 逐一校验：单段相对目录名 + 真实存在的直接子目录。返回规范化列表。"""
    out: list[str] = []
    for sc in scopes or []:
        sc = str(sc).strip().rstrip("\\/").strip()
        if not sc:
            continue
        if not _SCOPE_RE.match(sc) or sc.startswith("."):
            raise ValueError(f"子范围名不合法（单段相对目录名）：{sc!r}")
        if not (path / sc).is_dir():
            raise ValueError(f"子范围不是成员路径下的真实目录：{path / sc}")
        if sc not in out:
            out.append(sc)
    return out


def add_member(project: str, name: str, path: str,
               profile: str | None = None, scopes: list[str] | None = None) -> dict:
    """向项目添加成员。项目不存在时先创建（便于一次性建项目+成员）。"""
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"成员名不合法（1-64 位字母/数字/汉字/./-/_）：{name!r}")
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"成员路径不存在或不是目录：{path}")
    p = p.resolve()
    scope_list = _validate_scopes(p, list(scopes or []))
    reg = load()
    proj = resolve_project(project)
    if proj is None:
        proj = add_project(project)          # 内部已 save；继续在最新 reg 上改
        reg = load()
    else:
        proj = next(x for x in reg["projects"] if x.get("name") == project)
    if resolve_member(proj, name):
        raise ValueError(f"项目内成员名已存在：{name}")
    ent = {"name": name, "path": str(p),
           "profile": (profile or None), "scopes": scope_list,
           "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    proj["repos"].append(ent)
    save(reg)
    return ent


def remove_member(project: str, name: str) -> bool:
    reg = load()
    proj = next((x for x in reg["projects"] if x.get("name") == project), None)
    if proj is None:
        return False
    before = len(proj.get("repos") or [])
    proj["repos"] = [m for m in proj.get("repos") or [] if m.get("name") != name]
    if len(proj["repos"]) == before:
        return False
    save(reg)
    return True
