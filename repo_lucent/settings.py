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
# 口径唯一来源：分析口径（仓库签名/组件/核心知识库/门禁集/报告章节/品牌等）
# **只**来自 `profiles/<name>.json`（随包发布）或 `~/.repolucent/profiles/<name>.json`
# （用户级）。代码内的常量一律是「中性空值」，不再承载任何具体项目的口径——
# 这消除了改造前「未声明 profile 即静默使用内置 VeroRun 口径」的隐式耦合。
#
# 强制显式声明：未声明 profile、profile 名不存在、内容非法——三者一律报错退出
# （见 require_profile / profile_error_exit），绝不静默降级。
#
# profile 只放分析口径，绝不放密钥（密钥走 get_secret）。

#: 多仓库管理（--repo-name / /api/repos/switch）：按仓覆盖 profile 的进程内状态。
#: 值为 None（无覆盖）或已解析的 profile dict（来自 profiles/<name>.json）。
_PROFILE_OVERRIDE: dict | None = None

#: 显式声明 profile 的环境变量通道（供 CI / 脚本使用）。
_PROFILE_ENV = "REPO_LUCENT_PROFILE"

#: 进程内 --profile 覆盖（由 cli._setup / server 在 apply_profile() 之前设置）。
_OVERRIDE_NAME: str | None = None


class ProfileNotDeclared(Exception):
    """未声明分析口径、或声明的 profile 无法解析。调用方应转 profile_error_exit()。"""


def resolve_profile_source(name: str | None):
    """把 profile 名解析为 dict；找不到返回 None。

    搜索顺序：<tool>/profiles/<name>.json → ~/.repolucent/profiles/<name>.json。
    改造前这里对 "verorun"/"builtin"/"default" 特判返回 None（= 使用代码内置口径）；
    该特例已删除——verorun 口径现由随包发布的 profiles/verorun.json 承载。
    """
    if not name:
        return None
    for base in (_TOOL_ROOT / "profiles", Path.home() / ".repolucent" / "profiles"):
        p = base / f"{name}.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                prof = d.get("profile", d) if isinstance(d, dict) else {}
                return prof if isinstance(prof, dict) else None
            except Exception:  # noqa: BLE001 —— 内容损坏交由 require_profile 报错
                return None
    return None


def available_profiles() -> list[str]:
    """扫描两处 profiles 目录，返回可用预设名（供报错提示列出可选项）。"""
    names: set[str] = set()
    for base in (_TOOL_ROOT / "profiles", Path.home() / ".repolucent" / "profiles"):
        if base.is_dir():
            names |= {p.stem for p in base.glob("*.json")}
    return sorted(names)


def active_profile_name() -> str | None:
    """按优先级返回显式声明的 profile 名；未声明返回 None。

    优先级：--profile（_OVERRIDE_NAME）> REPO_LUCENT_PROFILE > settings.json 的 profile.name。
    """
    if _OVERRIDE_NAME:
        return _OVERRIDE_NAME
    env = os.environ.get(_PROFILE_ENV)
    if env and env.strip():
        return env.strip()
    p = load_settings().get("profile")
    if isinstance(p, dict) and p.get("name"):
        return str(p["name"]).strip() or None
    return None


def require_profile() -> tuple[str, dict]:
    """解析并返回 (name, profile_dict)；任一环节失败即抛 ProfileNotDeclared。

    `_PROFILE_OVERRIDE`（已解析的 dict）优先——它是权威口径，名字只是标签；
    否则按 active_profile_name() 的优先级解析，并回填 _PROFILE_OVERRIDE，
    使 get_profile()/profile_get() 与 apply_profile() 读到同一份口径（单一事实源）。
    """
    global _PROFILE_OVERRIDE
    prof = _PROFILE_OVERRIDE
    if not (isinstance(prof, dict) and prof):
        name = active_profile_name()
        if not name:
            raise ProfileNotDeclared("未声明分析口径 profile")
        prof = resolve_profile_source(name)
        if prof is None:
            raise ProfileNotDeclared(
                f"未找到 profile 预设 `{name}`（或该文件内容损坏）")
        if not prof:
            raise ProfileNotDeclared(f"profile `{name}` 内容为空")
        _PROFILE_OVERRIDE = prof
    return str(prof.get("name") or "custom"), prof


def profile_error_exit(exc: ProfileNotDeclared):
    """把 ProfileNotDeclared 转为 SystemExit（字符串消息 → main() 映射为退出码 2）。"""
    avail = available_profiles()
    hint = (
        "\n修复方式（任选其一）：\n"
        "  1) 命令行显式指定：--profile <名>\n"
        "  2) 环境变量：REPO_LUCENT_PROFILE=<名>\n"
        "  3) settings.json 写入：\"profile\": {\"name\": \"<名>\"}\n"
        f"可用预设：{', '.join(avail) if avail else '(未发现任何 profiles/*.json)'}"
    )
    raise SystemExit(f"[repolucent] {exc}{hint}")


def set_profile_override(source, *, name: str | None = None) -> str | None:
    """设置/清除按仓 profile 覆盖；返回实际生效的 profile 名（未生效返回 None）。

    source 可为 profile 名（str）、已解析 dict 或 None。必须在 apply_profile() 之前调用。
    `name` 用于 --profile 通道：既设置 _OVERRIDE_NAME（供 active_profile_name 读取），
    又解析出 dict 写入 _PROFILE_OVERRIDE。解析失败不抛错——由 require_profile() 统一报错。
    """
    global _PROFILE_OVERRIDE, _OVERRIDE_NAME
    if name is not None:
        _OVERRIDE_NAME = name or None
    if source is None:
        # 只清除"按仓覆盖"；进程级声明（_OVERRIDE_NAME，来自 --profile）保持不变，
        # 使切换/临时覆盖结束后仍能回到启动时声明的口径。
        _PROFILE_OVERRIDE = None
        return None
    if isinstance(source, str):
        _OVERRIDE_NAME = source or None
        resolved = resolve_profile_source(source)
        _PROFILE_OVERRIDE = resolved
        return source if resolved is not None else None
    if isinstance(source, dict):
        _PROFILE_OVERRIDE = source or None
        _OVERRIDE_NAME = None          # dict 即权威口径，名字取自 prof["name"]
        return str(source.get("name") or "custom") if source else None
    _PROFILE_OVERRIDE = None
    _OVERRIDE_NAME = None
    return None


def get_profile() -> dict:
    """返回当前生效的 profile 配置；未声明时返回空 dict。

    空 dict 不再意味着"VeroRun 内置默认"——调用方（apply_profile / repo_signature）
    会把它当作"口径缺失"处理并报错。正常流程中 require_profile() 已将其填充。
    """
    if _PROFILE_OVERRIDE is not None:
        return _PROFILE_OVERRIDE
    p = load_settings().get("profile", {})
    return p if isinstance(p, dict) else {}


def profile_get(key: str, default=None):
    """读 profile 字段；未配置（或显式 null）则返回 default。

    注意：default 由调用方给出，且**必须**是中性值（空列表/空 dict/空串），
    不得再传任何具体项目的口径常量。
    """
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
