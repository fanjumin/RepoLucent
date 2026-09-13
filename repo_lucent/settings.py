# -*- coding: utf-8 -*-
"""配置外部化核心：settings.json（可选）+ 环境变量覆盖。

设计约束（设计文档 §4.1 + 第三方审计报告 TD-5）：
- settings.json 只承载非敏感配置（max_*、models 元数据、mcp/llm 开关等）。
- 密钥（api_key / base_url 等）一律只从环境变量读取，绝不进 settings.json 明文。
- 沿用 SCHEMA_VERSION 兼容约定：MINOR 只增可选字段，消费方忽略未知字段。
- 纯标准库（json / os / pathlib），零第三方依赖。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # repo_lucent/
_TOOL_ROOT = _HERE.parent                         # 工具项目根（settings.json 落点）
_DEFAULT_NAME = "settings.json"


def _search_paths() -> list[Path]:
    """settings.json 搜索链（优先级从低到高；靠后的文件覆盖靠前的）：

    1) 包内 settings.default.json   —— 随包发布，作为最低优先级兜底
    2) ~/.repolucent/settings.json —— 用户级全局配置
       （过渡期兼容：若不存在则回落读取旧目录 ~/.repolucent/）
    3) <tool>/settings.json        —— 项目级配置
    4) 环境变量 REPO_LUCENT_SETTINGS 指向的文件 —— 显式指定（最高优先级；
       兼容旧名 VR_INSIGHT_SETTINGS）

    merged 用 update 合并，故搜索顺序决定了"后者覆盖前者"，与我们想要的
    「env 指定 > 项目级 > 用户级 > 包内默认」一致。
    """
    paths: list[Path] = []
    paths.append(_HERE / "settings.default.json")
    paths.append(Path.home() / ".repolucent" / _DEFAULT_NAME)
    _legacy_user = Path.home() / ".repolucent" / _DEFAULT_NAME
    if _legacy_user.is_file() and _legacy_user not in paths:
        paths.append(_legacy_user)
    paths.append(_TOOL_ROOT / _DEFAULT_NAME)
    env = os.environ.get("REPO_LUCENT_SETTINGS") or os.environ.get("VR_INSIGHT_SETTINGS")
    if env:
        paths.append(Path(env))
    return paths


def load_settings() -> dict:
    """返回合并后的配置 dict；任何文件缺失/损坏都不致命（降级为空 dict）。"""
    merged: dict = {}
    for p in _search_paths():
        if p.is_file():
            try:
                merged.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001  —— 损坏配置静默降级，不影响工具运行
                pass
    return merged


def get_secret(key: str, default: str | None = None) -> str | None:
    """密钥只从环境变量读取（如 REPO_LUCENT_DASHSCOPE_KEY）。绝不经 settings.json。

    这是 P2 LLM provider 的密钥读取通道：get_secret(model["key_env"])。
    settings.json 只允许用 key_env / base_url_env 指向环境变量，不得出现明文密钥。

    过渡期兼容：REPO_LUCENT_ 前缀密钥未设置时回落读取旧 VR_INSIGHT_ 同名变量。
    """
    v = os.environ.get(key)
    if v is None and key.startswith("REPO_LUCENT_"):
        v = os.environ.get("VR_INSIGHT_" + key[len("REPO_LUCENT_"):])
    return default if v is None else v


def get_setting(key: str, default=None, *, env_var: str | None = None):
    """非敏感项：settings.json 优先，其次环境变量，其次 default。"""
    s = load_settings()
    if key in s and s[key] is not None:
        return s[key]
    if env_var and (v := os.environ.get(env_var)) is not None:
        return v
    return default


def is_mcp_enabled() -> bool:
    """MCP 服务开关：默认开启，可被 settings.json 的 mcp_enabled 关闭。"""
    return bool(get_setting("mcp_enabled", True))


def is_outbound_mcp_enabled() -> bool:
    """outbound MCP（本工具作为客户端消费外部 Server）总开关，默认开启。

    注意：开启 ≠ 有可用 server。真正可调用的工具来自 mcp_servers() 里显式声明的
    条目；未声明任何 server 时，工具清单与改造前完全一致（零行为变化）。
    """
    return bool(get_setting("mcp_outbound_enabled", True))


def mcp_servers() -> list[dict]:
    """外部 MCP Server 白名单（outbound）。

    条目形态：
        {"name": "demo", "transport": "stdio", "command": "python",
         "args": ["-m", "some_mcp_server"], "env": {...}, "timeout_s": 30}
        {"name": "remote", "transport": "http", "url": "http://127.0.0.1:8788/mcp",
         "headers": {...}, "timeout_s": 30}

    安全：命令只能来自这里（配置白名单），调用方不可传入任意 command。
    """
    v = get_setting("mcp_servers", [], env_var=None) or []
    if not isinstance(v, list):
        return []
    return [s for s in v if isinstance(s, dict) and s.get("name")]


# ------------------------------------------------------ 分析口径 profile ----
# 档位 A 通用化：把「分析口径」(仓库签名/组件/核心知识库/门禁集等) 收敛到
# settings.json 的 profile 段。profile 只放分析口径，绝不放密钥（密钥走 get_secret）。
# 无 profile 段（或任何字段为 null）时，调用方回落到 config.py 内置 VeroRun 常量，
# 行为与改造前完全等价。

#: 多仓库管理（--repo-name / /api/repos/switch）：按仓覆盖 profile 的进程内状态。
#: 值为 None（无覆盖）或已解析的 profile dict（来自 profiles/<name>.json）。
_PROFILE_OVERRIDE: dict | None = None


def resolve_profile_source(name: str | None):
    """把 profile 名解析为 dict；找不到或内置名返回 None（= 使用内置 VeroRun 口径）。

    搜索顺序：<tool>/profiles/<name>.json → ~/.repolucent/profiles/<name>.json。
    """
    if not name or name in ("verorun", "builtin", "default"):
        return None
    for base in (_TOOL_ROOT / "profiles", Path.home() / ".repolucent" / "profiles"):
        p = base / f"{name}.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                prof = d.get("profile", d) if isinstance(d, dict) else {}
                return prof if isinstance(prof, dict) else {}
            except Exception:  # noqa: BLE001 —— 损坏预设降级为内置口径
                return None
    return None


def set_profile_override(source) -> str:
    """设置/清除按仓 profile 覆盖；返回实际生效的 profile 名。

    source 可为 profile 名（str）、已解析 dict 或 None。必须在 apply_profile() 之前调用。
    """
    global _PROFILE_OVERRIDE
    if source is None:
        _PROFILE_OVERRIDE = None
        return "verorun"
    if isinstance(source, str):
        resolved = resolve_profile_source(source)
        _PROFILE_OVERRIDE = resolved
        return "verorun" if resolved is None else source
    if isinstance(source, dict):
        _PROFILE_OVERRIDE = source
        return str(source.get("name") or "custom")
    _PROFILE_OVERRIDE = None
    return "verorun"


def get_profile() -> dict:
    """返回当前 profile 配置；缺省返回空 dict（= VeroRun 内置默认）。"""
    if _PROFILE_OVERRIDE is not None:
        return _PROFILE_OVERRIDE
    p = load_settings().get("profile", {})
    return p if isinstance(p, dict) else {}


def profile_get(key: str, default=None):
    """读 profile 字段；未配置（或显式 null）则返回 default（调用方传内置 VeroRun 常量）。"""
    v = get_profile().get(key)
    return default if v is None else v


def is_llm_enabled(models_present: bool = False) -> bool:
    """LLM 开关：默认关闭；settings 显式开启或配置了 models 即为启用（P2 使用）。"""
    explicit = get_setting("llm_enabled", False)
    if explicit:
        return True
    if models_present:
        return bool(get_setting("models"))
    return False
