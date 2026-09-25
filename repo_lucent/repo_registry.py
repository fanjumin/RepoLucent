# -*- coding: utf-8 -*-
"""多仓库注册表：~/.repolucent/repos.json。

条目形态：{"name": str, "path": str, "profile": str|null, "added_at": str}
- profile：预设名（<tool>/profiles/<name>.json 或 ~/.repolucent/profiles/<name>.json）；
  **强烈建议填**：改造后已无"内置默认口径"，null/空 的注册项在分析时会被
  强制显式声明拦下（退出码 2）——只有在调用方另行用 --profile /
  REPO_LUCENT_PROFILE / settings.json 声明口径时，null 才有意义。
- 纯本地开发辅助数据，不入 Git；任何损坏都降级为空注册表，不让 CLI/服务崩。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff.-]{1,64}$")


def registry_path() -> Path:
    return Path.home() / ".repolucent" / "repos.json"


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
    """可用 profile 预设名：<tool>/profiles/*.json + ~/.repolucent/profiles/*.json。

    不再硬编码 "verorun"——它现在由随包发布的 profiles/verorun.json 提供，
    与其余预设同源（口径唯一来源）。
    """
    from .settings import available_profiles
    return available_profiles()


def add_repo(name: str, path: str, profile: str | None = None,
             *, force: bool = False) -> dict:
    """注册仓库（写入 ~/.repolucent/repos.json）。

    注册期校验（FIX P0-1）：路径不满足所绑 profile 的 repo_signature 时**照常注册**，
    但在返回的 entry 上附带 `warning` 字段说明"按该 profile 分析会失败 + 怎么修"。
    刻意不阻断——注册表是纯本地开发辅助数据，其既有设计约定是「降级不阻断」
    （见模块 docstring），且 scaffold 阶段先注册后补 signature 文件是正常流程。
    告警只在注册时刻可见（不落盘，保持 entry schema 不变）；force=True 跳过检查。

    修复前的实际后果是"注册 100% 成功、审计 100% 失败"且失败提示把用户引向
    已经用过的 --repo，形成死循环；现在用户在注册那一刻就拿到可执行的修法。
    """
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"仓库名不合法（1-64 位字母/数字/汉字/./-/_）：{name!r}")
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"仓库路径不存在或不是目录：{path}")
    p = p.resolve()
    warning: str | None = None
    if not force:
        # 函数内导入：避免 config ↔ repo_registry 的模块级循环依赖。
        from .config import signature_for_profile, signature_matches
        sig = signature_for_profile(profile)
        if not signature_matches(sig, p):
            need: list[str] = [f"目录: {', '.join(sig['dirs'])}"] if sig["dirs"] else []
            need += [f"文件: {', '.join(sig['files'])}"] if sig["files"] else []
            warning = (
                f"路径不匹配 profile 签名，按当前 profile 分析会失败：\n"
                f"    路径    : {p}\n"
                f"    profile : {profile or '(未声明)'}\n"
                f"    要求    : {'；'.join(need) or '（该 profile 接受任意目录）'}\n"
                "    修法    : ① 换 --profile（如 generic-python 要求 pyproject.toml）；"
                "② 为该技术栈新增 profiles/<name>.json；③ 确需保留可加 --force 静默。"
            )
    reg = load_registry()
    if resolve(name):
        raise ValueError(f"仓库名已存在：{name}")
    entry = {"name": name, "path": str(p),
             "profile": (profile or None),
             "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    reg["repos"].append(entry)
    save_registry(reg)
    return {**entry, "warning": warning} if warning else entry


def remove_repo(name: str) -> bool:
    reg = load_registry()
    before = len(reg["repos"])
    reg["repos"] = [r for r in reg["repos"] if r.get("name") != name]
    if len(reg["repos"]) == before:
        return False
    save_registry(reg)
    return True
