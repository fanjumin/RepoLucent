# -*- coding: utf-8 -*-
"""pack 分层：把「写能力」从只读内核剥离为可按场景分发的包。

设计动机（对齐 SonarQube 插件 / Semgrep rules pack 的分层分发思想）：
- 内核（``repo_lucent/*.py`` + ``registry.core.json``）只保留**通用只读分析**能力；
- 各场景的**写能力**收敛到 ``repo_lucent/packs/<name>/`` 这两个明确目录，
  安全审计面（可执行脚本 / 可写远程的子命令）由"整个包"收敛到"两个目录"；
- generic-python 场景可只启用 gitflow，分发体量更小。

一个 pack 即一个目录 ``repo_lucent/packs/<name>/``，约定文件：
    registry.json    贡献给脚本资产库的脚本条目（可选）
    cli_commands.py  贡献给 CLI 的子命令（可选，实现 ``build(sub, ctx) -> handlers``）
    *.py             该 pack 的实现模块

启用策略（优先级从高到低）::

    settings.json 的 ``packs.enabled``   用户级显式配置，最高优先级
      null（缺省）  → 落到下一级
      []            → 纯只读内核：任何 pack 的脚本与子命令都不可用
      ["gitflow"]   → 仅启用列出的 pack
    profile 的 ``packs.enabled``         分析口径自带（如 verorun = ["gitflow","verorun"]）
    代码兜底 ``_FALLBACK_PACKS``            profile 未声明时生效 = 纯只读内核

**去硬编码**：改造前这里有 ``DEFAULT_PACKS_BY_PROFILE = {"verorun": [...],
"generic-python": [...]}``，把「某分析口径该启用哪些 pack」写死在代码里；
现改为由各 ``profiles/<name>.json`` 的 ``packs.enabled`` 自述。
``verorun.json`` 显式声明 ``["gitflow","verorun"]``，故 VeroRun 侧行为零变化
（这是本改造的硬约束，19 套 verify 必须全绿）。

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

#: profile 未声明 ``packs.enabled`` 时的最终兜底：纯只读内核，不默认启用任何 pack。
#: （改造前此处为 ``["gitflow"]``，那等于"未声明口径即送通用写能力"的隐式授权；
#: 现要求 profile 自述启用集，未声明即不启用——与"强制显式声明"口径一致。）
_FALLBACK_PACKS: list[str] = []


def _exists(name: str) -> bool:
    return name in PACKS and (_PACKS_DIR / name).is_dir()


def available_packs() -> list[str]:
    """已登记且目录存在的 pack（按登记顺序）。"""
    return [n for n in PACKS if _exists(n)]


def current_profile_name() -> str:
    """当前生效的 profile 名；未声明返回空串（不再以 "verorun" 兜底）。"""
    from ..settings import get_profile
    return str(get_profile().get("name") or "")


def _declared_packs(profile_name: str | None = None) -> list[str] | None:
    """读 profile 自述的启用集；profile 未声明 ``packs.enabled`` 时返回 None。

    未指定 ``profile_name`` 时必须走 ``require_profile()``——它才是"当前生效口径"
    的单一事实源（覆盖 --profile / REPO_LUCENT_PROFILE / settings.json 三条通道）。
    只读 ``get_profile()`` 会漏掉环境变量通道，使 CLI（声明在 env）与库调用
    （声明在 settings）对"启用哪些 pack"给出不同答案。
    """
    from ..settings import get_profile, resolve_profile_source
    if profile_name is None:
        try:
            from ..settings import require_profile
            _, prof = require_profile()
        except Exception:  # noqa: BLE001 —— 未声明口径时按"无声明"处理（上层会报错）
            prof = get_profile()
    else:
        prof = resolve_profile_source(profile_name) or {}
    packs = prof.get("packs") if isinstance(prof, dict) else None
    if isinstance(packs, dict) and isinstance(packs.get("enabled"), list):
        return [str(x) for x in packs["enabled"]]
    return None


def enabled_packs(profile_name: str | None = None) -> list[str]:
    """解析生效的 pack 列表。

    优先级：``settings.packs.enabled``（用户级显式配置）> ``profile.packs.enabled``
    （分析口径自述）> ``_FALLBACK_PACKS``（纯只读内核）。

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
    declared = _declared_packs(profile_name)
    names = _FALLBACK_PACKS if declared is None else declared
    return [n for n in names if _exists(n)]


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
            print(f"[repolucent] 警告：pack `{name}` 的 CLI 命令注册失败，已跳过："
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return handlers
