# -*- coding: utf-8 -*-
"""外部工具适配器契约层（审计 EXT-1~EXT-6）：把 registry 从「单 kind 白名单」升级为「多 kind 适配器」。

统一结果结构（EXT-6）：一切外部工具（含内置脚本）都归一为 NormalizedResult，
让上层（serve / MCP / CLI）不必关心底层是 importlib、子进程还是 JSON-RPC。

安全初衷保持不变（EXT-1）：
- 适配器只是**分派**，不是放开。entry 前缀校验仍在 BuiltinAdapter 内执行；
- `run_script_api` 的三重门控（active + kind∈可跑集 + confirm + argv 限长 + 127.0.0.1）不因分派而放宽；
- 新增 kind 必须**显式注册**到 ADAPTERS，未注册 kind 一律拒绝执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: entry 白名单前缀（安全边界的**单一事实源**）：内核脚本库 + pack 目录。
#: 二者之外一律拒绝执行。BuiltinAdapter 与 script_cmd 共用本常量，避免双源分叉；
#: v1.8.0 pack 分层把"业务探针"迁入 repo_lens.packs，故前缀由单值扩为元组。
ALLOWED_ENTRY_PREFIXES: tuple[str, ...] = ("repo_lens.scriptlib.", "repo_lens.packs.")


def entry_allowed(entry: str | None) -> bool:
    """entry 是否落在允许的执行白名单内（空 entry 一律拒绝）。"""
    return bool(entry) and any(str(entry).startswith(p) for p in ALLOWED_ENTRY_PREFIXES)


# ---------------------------------------------------------------------------
# 退出码归一化（审计 EXT-5）
# ---------------------------------------------------------------------------
#: 归一化后的契约退出码：0 成功 / 1 工具判问题 / 2 参数或环境错 / 3 内部异常
EXIT_CODES = (0, 1, 2, 3)

#: shell 约定的「未能执行」取值：126 不可执行 / 127 命令不存在。
#: 二者都是**环境错**而非工具判问题，故归入 2。
_SHELL_ENV_ERRORS = (126, 127)


def normalize_exit_code(code: int) -> int:
    """把工具原始退出码归一到契约 ``0/1/2/3``（EXT-5）。

    映射刻意保守——**契约内的 0..3 一律原样保留**（故对既有内置脚本是恒等变换，
    不改变任何既有语义），只收敛契约外取值：

    126 / 127  → 2   无法执行 / 命令不存在（环境错，shell 约定）
    负数        → 3   被信号终止（POSIX 约定）
    其它        → 3   契约外取值，统一归入内部异常

    归一前的原始值由调用方放进 ``meta['raw_exit_code']`` 保留，便于诊断定位，
    做到「语义归一」而不「信息丢失」。
    """
    if code in EXIT_CODES:
        return code
    if code < 0:
        return 3
    if code in _SHELL_ENV_ERRORS:
        return 2
    return 3


def with_raw_exit_code(meta: dict, code: int, normalized: int) -> dict:
    """归一值与原值不同时，在 meta 里补记 ``raw_exit_code``。"""
    if normalized != code:
        meta = dict(meta)
        meta["raw_exit_code"] = code
    return meta


# ---------------------------------------------------------------------------
# 输出归一化（EXT-6 的公共小件，供各适配器共用，避免多份实现分叉）
# ---------------------------------------------------------------------------
def maybe_json(text: str):
    """stdout 若能解析为 JSON 则返回对象，否则 None（只看首个非空字符）。"""
    s = (text or "").strip()
    if not (s.startswith("{") or s.startswith("[")):
        return None
    import json
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


@dataclass
class NormalizedResult:
    """外部工具执行的统一结果（EXT-6）。

    ok            是否成功（退出码 0 且无协议错误）
    exit_code     归一化退出码：0 成功 / 1 工具判问题 / 2 参数或环境错 / 3 内部异常
                  （沿用本工具既有契约 cli.py 的 0/1/2/3）
    stdout        工具标准输出（截断保护）
    json          若输出可解析为 JSON 则给出，否则 None
    artifacts     工具产出的文件路径等（多为外部工具不适用，留空）
    error/hint    失败原因与可操作提示
    meta          适配器私有诊断信息（transport/server/tool 等）
    """
    ok: bool
    exit_code: int = 0
    stdout: str = ""
    json: Any = None
    artifacts: list = field(default_factory=list)
    error: str | None = None
    hint: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"ok": self.ok, "exit_code": self.exit_code, "stdout": self.stdout,
             "artifacts": self.artifacts, "error": self.error, "hint": self.hint}
        if self.json is not None:
            d["json"] = self.json
        if self.meta:
            d["meta"] = self.meta
        return d


class AdapterError(Exception):
    """适配器内部错误（配置缺失、协议失败等）；由 dispatch 转成 exit_code=2/3。"""


class Adapter:
    """适配器基类：实现 run(spec, argv) -> NormalizedResult。"""

    #: registry 里的 kind 值
    kind = "builtin"

    def run(self, spec: dict, argv: list[str], timeout_s: float | None = None) -> NormalizedResult:
        raise NotImplementedError


#: kind -> Adapter 实例（显式注册；未注册的 kind 拒绝执行）
ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    ADAPTERS[adapter.kind] = adapter


def get_adapter(kind: str | None) -> Adapter | None:
    return ADAPTERS.get(kind or "builtin")


def dispatch(spec: dict, argv: list[str], timeout_s: float | None = None) -> NormalizedResult:
    """按 kind 选择适配器执行；未注册 kind 直接返回 exit_code=2（不执行任何东西）。"""
    kind = spec.get("kind") or "builtin"
    ad = get_adapter(kind)
    if ad is None:
        return NormalizedResult(
            ok=False, exit_code=2, error="unsupported_kind",
            hint=f"kind={kind!r} 无对应适配器（已注册：{', '.join(ADAPTERS) or '无'}）")
    try:
        return ad.run(spec, list(argv), timeout_s=timeout_s)
    except AdapterError as e:
        return NormalizedResult(ok=False, exit_code=2, error="adapter_error",
                                hint=str(e), meta={"kind": kind})
    except Exception as e:  # noqa: BLE001 - 外部工具异常不得外泄为崩溃
        return NormalizedResult(ok=False, exit_code=3, error="adapter_exception",
                                hint=f"{type(e).__name__}: {e}", meta={"kind": kind})


# ---------------------------------------------------------------------------
# argv ⇄ arguments：让 MCP/JSON-RPC 这类「具名参数」工具复用 registry 的 inputs 声明
# ---------------------------------------------------------------------------
def argv_to_arguments(argv: list[str], inputs: list[dict] | None) -> dict:
    """把 `--name value` / `--flag` 形式的 argv 还原成具名参数 dict。

    与 script/registry 的 inputs 声明对齐：flag/bool 类只出现名字即为 True，
    其余取后续 token 作为值；未声明的参数一律忽略（防注入未声明字段）。
    """
    norm = {i["name"].lstrip("-").replace("-", "_"): i for i in (inputs or [])}
    out: dict = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("--"):
            i += 1
            continue
        key = tok[2:].replace("-", "_")
        spec = norm.get(key)
        if spec is None:
            i += 1
            continue
        t = spec.get("type", "string")
        if t in ("flag", "boolean"):
            out[key] = True
            i += 1
            continue
        if i + 1 >= len(argv):
            break
        out[key] = argv[i + 1]
        i += 2
    return out
