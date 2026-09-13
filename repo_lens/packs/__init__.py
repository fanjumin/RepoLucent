# -*- coding: utf-8 -*-
"""pack 分层：把「写能力」从只读内核剥离为可按场景分发的包。

设计动机（对齐 SonarQube 插件 / Semgrep rules pack 的分层分发思想）：
- 内核（``repo_lens/*.py`` + ``registry.core.json``）只保留**通用只读分析**能力；
- 各场景的**写能力**收敛到 ``repo_lens/packs/<name>/`` 这两个明确目录，
  安全审计面（可执行脚本 / 可写远程的子命令）由"整个包"收敛到"两个目录"；
- generic-python 场景可只启用 gitflow，分发体量更小。

一个 pack 即一个目录 ``repo_lens/packs/<name>/``，约定文件：
    registry.json    贡献给脚本资产库的脚本条目（可选）
    cli_commands.py  贡献给 CLI 的子命令（可选，实现 ``build(sub, ctx) -> handlers``）
    *.py             该 pack 的实现模块

启用策略（settings.json 的 ``packs.enabled``）::

    null（缺省）    → 按 profile 默认（见 DEFAULT_PACKS_BY_PROFILE）
    []              → 纯只读内核：任何 pack 的脚本与子命令都不可用
    ["gitflow"]     → 仅启用列出的 pack

**兼容保证**：内置 profile（verorun）默认启用全部 pack，故既有用户行为零变化
——这是本改造的硬约束（19 套 verify 必须全绿）。

另注：pack 的模块**始终可导入**（导入不产生任何写副作用），"未启用"只作用于
「CLI 子命令是否注册」与「脚本条目是否可见」两个入口，门控语义清晰且不脆。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_PACKS_DIR = Path(__file__).resolve().parent

#: 可用 pack：目录名 → 元数据。新增 pack 只需在此登记并建同名目录。
PACKS: dict[str, dict] = {
    "gitflow": {
        "title": "Git 工作流",
        "desc": "智能推送 / 拉取 / 多远程同步 / 仓库组批量操作（写远程能力）",
        "commands": ("push", "pull", "sync", "group", "batch"),
    },
    "verorun": {
        "title": "VeroRun 业务探针",
        "desc": "数据存储只读探针 / SSH 只读巡检（对接真实业务环境）",
        "commands": (),
    },
}

#: 内置 profile → 默认启用的 pack。
DEFAULT_PACKS_BY_PROFILE: dict[str, list[str]] = {
    "verorun": ["gitflow", "verorun"],
    "generic-python": ["gitflow"],
}

#: 未登记 profile 的回落：只给通用工程能力，不含业务探针。
_FALLBACK_PACKS = ["gitflow"]


def _exists(name: str) -> bool:
    return name in PACKS and (_PACKS_DIR / name).is_dir()


def available_packs() -> list[str]:
    """已登记且目录存在的 pack（按登记顺序）。"""
    return [n for n in PACKS if _exists(n)]


def current_profile_name() -> str:
    from ..settings import get_profile
    return str(get_profile().get("name") or "verorun")


def enabled_packs(profile_name: str | None = None) -> list[str]:
    """解析生效的 pack 列表：``settings.packs.enabled`` 优先，其次按 profile 默认。

    显式 ``enabled: []`` 表示"纯只读内核"，返回空列表。未知 pack 名被忽略
    （配置笔误不应导致运行时报错）。
    """
    from ..settings import get_setting
    cfg = get_setting("packs", None)
    if isinstance(cfg, dict) and cfg.get("enabled") is not None:
        v = cfg["enabled"]
        if not isinstance(v, list):
            return []
        return [n for n in (str(x) for x in v) if _exists(n)]
    prof = current_profile_name() if profile_name is None else profile_name
    return [n for n in DEFAULT_PACKS_BY_PROFILE.get(prof, _FALLBACK_PACKS) if _exists(n)]


def is_enabled(name: str) -> bool:
    return name in enabled_packs()


def disabled_command_hints() -> dict[str, str]:
    """命令名 → 所属 pack。供 CLI 对"命令存在但 pack 未启用"给出可操作提示。"""
    return {c: p for p, meta in PACKS.items() for c in meta.get("commands", ())}


def registry_entries(enabled: list[str] | None = None) -> list[dict]:
    """聚合各启用 pack 贡献的脚本条目（附 ``pack`` 归属字段）。"""
    names = enabled_packs() if enabled is None else enabled
    out: list[dict] = []
    for name in names:
        try:
            d = json.loads((_PACKS_DIR / name / "registry.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in d.get("scripts", []) or []:
            if isinstance(s, dict) and s.get("id"):
                e = dict(s)
                e.setdefault("pack", name)
                out.append(e)
    return out


def register_cli(sub, ctx: dict) -> dict:
    """把**启用** pack 的 CLI 子命令注册进 argparse，返回 ``{命令名: handler}``。

    未启用的 pack 不注册其命令；``cli.run()`` 会对其报"pack disabled"并提示启用方法。
    单个 pack 的加载/注册失败降级为「跳过 + stderr 警告」，不让整个 CLI 不可用。
    """
    handlers: dict = {}
    for name in enabled_packs():
        if not (_PACKS_DIR / name / "cli_commands.py").is_file():
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{name}.cli_commands")
            build = getattr(mod, "build", None)
            if callable(build):
                handlers.update(build(sub, ctx) or {})
        except Exception as e:  # noqa: BLE001 —— pack 损坏不应让 CLI 整体不可用
            print(f"[repolens] 警告：pack `{name}` 的 CLI 命令注册失败，已跳过："
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return handlers
