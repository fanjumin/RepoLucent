# -*- coding: utf-8 -*-
"""测试基线口径引导（去 VeroRun 硬编码 · 阶段 7）。

背景
----
改造前存在一条隐式契约：**未声明 profile 即静默使用代码内置的 VeroRun 口径**
（`config.py` 里的 `PLUGINS_DIR="plugins"`、17 条核心知识库、`/admin/{identifier}`
路由惯例、VeroRun 报告文案……）。该契约已删除——口径唯一的来源是
`profiles/<name>.json`，且必须**显式声明**，否则报错退出（退出码 2）。

于是每个 `verify_*.py` / `perf` 套件都必须先声明口径。本模块把这件事收敛到一处，
避免十几套脚本各写一份容易走样的引导代码：

    from _boot import use_baseline_profile
    use_baseline_profile()          # 声明 verorun 预设并立即生效

它做两件事，缺一不可
--------------------
1. 设 `REPO_LUCENT_PROFILE=verorun`
   —— 供**子进程**（CLI / 本地控制台 / MCP stdio / HTTP）继承。
2. 调 `config.apply_profile()`
   —— 供**进程内**直接调用 analyzer 的用例。`config` 的模块级常量在 apply 之前
   一律是「中性空值」：插件目录为 `""`、核心知识库为 `{}`、边界规则为 `None`。
   只设环境变量而不 apply，进程内拿到的就是"空口径"产物（表现为 `plugins=0`、
   `boundary_observations.rule=None`、符号 `owner` 退化为顶层目录名）。

注意
----
`apply_profile()` 会把解析结果缓存进 `settings._PROFILE_OVERRIDE`（权威口径，
优先于环境变量与 settings.json）。因此需要**切换**口径的用例，应在切换前显式
`set_profile_override(...)`；本模块只负责"声明基线口径"这一件事。
"""
from __future__ import annotations

import os

#: 测试金字塔统一的基线口径——与 `tests/golden/*` 快照的录制口径一致。
BASELINE_PROFILE = "verorun"


def use_baseline_profile(name: str = BASELINE_PROFILE, *, apply: bool = True) -> str:
    """声明基线口径；返回实际生效的 profile 名。

    ``apply=False`` 只设环境变量（用例仅通过子进程调用 CLI / 服务端时够用）；
    进程内直接调用 analyzer 的用例必须保持默认的 ``apply=True``。
    """
    os.environ.setdefault("REPO_LUCENT_PROFILE", name)
    if not apply:
        return name
    from repo_lucent import config
    return config.apply_profile()
