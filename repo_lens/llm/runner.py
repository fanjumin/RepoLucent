# -*- coding: utf-8 -*-
"""LLM 运行器（P2 需求2）：把「语义审计任务书 → 外部 Agent 判定」换成「内部模型判定」。

管线（设计文档 §3.2）：
    semantic.build_prompt(cfg, data, dims)      # 复用既有任务书（含 report_ai 上下文切片）
        → provider.chat(messages, tools=…)      # 工具 schema 来自自身 MCP 的 tools/list
        → 工具回环 McpLoopback.tools/call       # 与外部 Agent 同一执行面、同一 confirm 门控
        → 解析模型返回 JSON
        → semantic.load_items(...)              # 复用既有校验回灌，产 Finding(coverage=semantic)

红线（设计文档 §3.3 / 审计报告 LLM-1~3）：
- LLM-2 只加严不放行：语义 findings 交回 engine 时默认 semantic_can_block=False，
  本模块**绝不**自行提升语义发现的阻断效力，也不修改 verdict。
- LLM-1 密钥只经 settings.get_secret（环境变量）读取；本模块不打印、不落盘、不入 registry。
- LLM-3 任何 provider 异常（含熔断）一律降级为 ran=False + reason，不向上抛、不拖垮审计。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config import ToolConfig
from ..settings import get_secret, get_setting

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


@dataclass
class SemanticRun:
    """一次 LLM 语义审计的结果（成功或降级都返回本对象，绝不抛给调用方）。"""
    ran: bool = False
    reason: str = ""                       # ran=False 时的降级原因
    findings: list = field(default_factory=list)
    rejects: list[str] = field(default_factory=list)
    model: str | None = None
    provider: str | None = None
    rounds: int = 0                        # 实际 chat 轮数
    tool_calls: int = 0                    # 经 MCP 回环执行的工具次数
    raw_chars: int = 0                     # 模型返回文本长度（诊断用）
    #: 多轮累计 token 用量（LLM-5）；网关未回传过 usage 时为 None。
    usage: dict | None = None
    streamed: bool = False                 # 本次是否走了流式通道

    def to_dict(self) -> dict:
        d = {"ran": self.ran, "reason": self.reason,
             "findings": len(self.findings), "rejects": self.rejects,
             "model": self.model, "provider": self.provider,
             "rounds": self.rounds, "tool_calls": self.tool_calls,
             "raw_chars": self.raw_chars, "streamed": self.streamed}
        if self.usage is not None:
            d["usage"] = self.usage
        return d


def accumulate_usage(acc: dict | None, usage: dict | None) -> dict | None:
    """把一轮的 usage 累加到总计（纯函数）。任一为 None 时按「有则加、无则保持」。"""
    if not isinstance(usage, dict):
        return acc
    out = dict(acc or {})
    for k, v in usage.items():
        if isinstance(v, int):
            out[k] = int(out.get(k, 0)) + v
    return out


# ---------------------------------------------------------------------------
# 模型解析与 provider 构建
# ---------------------------------------------------------------------------
def llm_settings() -> dict:
    """读取 settings 里的 llm 段（超时/重试/轮数上限等），带内置默认。"""
    s = get_setting("llm", {}, env_var=None) or {}
    if not isinstance(s, dict):
        return {}
    return {
        "timeout_s": float(s.get("timeout_s", 30)),
        "max_retries": int(s.get("max_retries", 2)),
        "max_tool_rounds": int(s.get("max_tool_rounds", 3)),
        "use_tools": bool(s.get("use_tools", True)),
        "temperature": float(s.get("temperature", 0.0)),
    }


def resolve_model(name: str | None = None) -> tuple[dict | None, str | None]:
    """按 name 或 default_model 从 settings.models[] 取一条模型配置。

    返回 (entry, reason)：成功时 reason=None；失败时 entry=None 且 reason 说明原因。
    """
    tc: ToolConfig = ToolConfig.from_settings()
    if not tc.llm_enabled:
        return None, "LLM 未启用（settings 无 models[] 且 llm_enabled 未开）"
    if not tc.models:
        return None, "settings.models[] 为空，无可用模型"
    want = name or tc.default_model
    if want:
        for m in tc.models:
            if m.get("name") == want:
                return m, None
        return None, f"未找到名为 {want!r} 的模型（可选：{', '.join(m.get('name','?') for m in tc.models)}）"
    return tc.models[0], None


def build_provider(entry: dict):
    """由模型配置构建 provider。密钥/网关地址只从环境变量读（LLM-1）。

    返回 (provider, reason)；缺密钥等可预期情况返回 (None, reason) 由调用方降级。
    """
    from .providers.openai_compat import OpenAICompat

    prov = (entry.get("provider") or "openai_compat").lower()
    if prov not in ("openai_compat", "openai", "openai-compatible"):
        return None, f"不支持的 provider：{prov}"
    key_env = entry.get("key_env")
    api_key = get_secret(key_env) if key_env else None
    if key_env and not api_key:
        return None, f"环境变量 {key_env} 未设置（密钥只允许经环境变量注入）"
    base_url = None
    if entry.get("base_url_env"):
        base_url = get_secret(entry["base_url_env"])
    base_url = base_url or entry.get("base_url")
    if not base_url:
        return None, "缺少 base_url（请用 base_url_env 指向环境变量）"

    ls = llm_settings()
    try:
        p = OpenAICompat(base_url=base_url, api_key=api_key or "",
                         model=entry.get("model") or entry.get("name"),
                         timeout_s=float(entry.get("timeout_s", ls["timeout_s"])),
                         max_retries=int(entry.get("max_retries", ls["max_retries"])))
    except Exception as e:  # noqa: BLE001 - 构造失败（缺参数）也算可预期降级
        return None, f"provider 构建失败：{e}"
    return p, None


# ---------------------------------------------------------------------------
# 模型返回文本 → JSON
# ---------------------------------------------------------------------------
def extract_json(text: str):
    """从模型输出里稳健抽取 JSON：优先 markdown 代码块，其次首个 {...} 块。"""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except ValueError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def run_semantic_audit(cfg, data, dims=None, model_name: str | None = None,
                       use_tools: bool = True,
                       allow_confirmed_writes: bool = False,
                       provider=None, loopback=None,
                       on_delta=None) -> SemanticRun:
    """跑一次内部模型语义审计；任何失败都降级为 ran=False（不抛异常）。

    provider / loopback 可注入（测试替身），生产环境留空即走真实通道。
    ``on_delta``（LLM-5）为**显式 opt-in** 的流式回调用：传入时才走流式通道并逐段
    回调增量文本，缺省则完全沿用一次性 `chat`，默认路径行为不变。
    """
    from ..audit import semantic as SEM
    from .providers.base import LLMError, restore_tool_name, to_openai_tools

    ls = llm_settings()
    run = SemanticRun()

    # 1) 模型解析 ----
    if provider is None:
        entry, reason = resolve_model(model_name)
        if entry is None:
            run.reason = reason or "无可用模型"
            return run
        provider, reason = build_provider(entry)
        if provider is None:
            run.reason = reason or "provider 构建失败"
            return run
        run.model = entry.get("model") or entry.get("name")
    else:
        run.model = getattr(provider, "model", None) or model_name
    run.provider = getattr(provider, "name", type(provider).__name__)

    # 2) 任务书（复用既有 build_prompt，与外灌通道完全同源）----
    try:
        from ..audit import engine as AE
        dim_list = list(dims) if dims else list(AE.DEFAULT_DIMS)
        prompt = SEM.build_prompt(cfg, data, dim_list)
    except Exception as e:  # noqa: BLE001
        run.reason = f"任务书生成失败：{e}"
        return run

    messages: list[dict] = [
        {"role": "system",
         "content": "你是严格的代码审计助手。只依据给定上下文判定，只输出规定 JSON，"
                    "不要臆造文件路径；不确定就不报。"},
        {"role": "user", "content": prompt},
    ]

    # 3) 工具回环（可选；失败则退化为纯文本判定，不致命）----
    tools_payload: list[dict] = []
    known_names: list[str] = []
    lb = loopback
    want_tools = use_tools and ls["use_tools"] and getattr(provider, "supports_tools", False)
    if want_tools and lb is None:
        try:
            from .loopback import McpLoopback
            lb = McpLoopback(cfg.repo_root, cfg.out_dir)
            lb.start()
        except Exception:  # noqa: BLE001 - 回环不可用 → 纯文本判定，仍属可用降级
            lb = None
    if lb is not None and want_tools:
        try:
            tools_payload, known_names = to_openai_tools(lb.list_tools())
        except Exception:  # noqa: BLE001
            tools_payload, known_names = [], []

    # 4) 多轮 chat（含工具回环）----
    max_rounds = max(1, int(ls["max_tool_rounds"]))
    temperature = ls["temperature"]
    content = ""
    try:
        for _ in range(max_rounds):
            run.rounds += 1
            if on_delta is not None:
                # 仅显式 opt-in 才走流式；Provider 基类的 chat_stream 兜底实现
                # 保证不支持流式的 provider（测试替身 / 第三方）自动退化为一次性调用。
                run.streamed = True
                res = provider.chat_stream(messages, tools=tools_payload or None,
                                           temperature=temperature,
                                           on_delta=on_delta)
            else:
                res = provider.chat(messages, tools=tools_payload or None,
                                    temperature=temperature)
            # LLM-5：无论流式与否都累计 token 用量（网关未回传则保持 None）
            run.usage = accumulate_usage(run.usage, getattr(res, "usage", None))
            if res.has_tool_calls and lb is not None:
                # 把 assistant 的工具调用请求原样回灌，再逐条执行
                messages.append({
                    "role": "assistant",
                    "content": res.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name,
                                      "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                        for c in res.tool_calls],
                })
                from .loopback import tool_result_to_message
                for c in res.tool_calls:
                    real = restore_tool_name(c.name, known_names)
                    # 安全红线：默认不携带 confirm=True，写类脚本仅 dry-run
                    confirm = bool(allow_confirmed_writes) and real.startswith("script.")
                    r = lb.call_tool(real, c.arguments, confirm=confirm)
                    run.tool_calls += 1
                    messages.append(tool_result_to_message(c.id, r))
                continue
            content = res.content or ""
            break
        else:
            # 轮数耗尽：取最后一轮文本兜底；无文本则降级
            content = content or ""
            if not content:
                run.reason = f"工具调用轮数达上限（{max_rounds}）且无最终结论"
                return run
    except LLMError as e:
        run.reason = f"LLM 通道不可用，已降级：{e}"
        return run
    except Exception as e:  # noqa: BLE001 - 任何未知异常都降级，不拖垮审计主流程
        run.reason = f"LLM 通道异常，已降级：{e}"
        return run
    finally:
        # 仅关闭本函数自己创建的回环（注入的不关，交由调用方管理）
        if loopback is None and lb is not None:
            try:
                lb.close()
            except Exception:  # noqa: BLE001
                pass

    run.raw_chars = len(content)

    # 5) 解析 → 复用既有校验回灌（只加严、不放行由 engine 的 semantic_can_block 控制）----
    raw = extract_json(content)
    if raw is None:
        run.reason = "模型返回内容无法解析为 JSON（已降级，未产生语义发现）"
        return run
    try:
        findings, rejects = SEM.load_items(raw)
    except Exception as e:  # noqa: BLE001
        run.reason = f"语义结果校验失败：{e}"
        return run
    run.findings = list(findings)
    run.rejects = list(rejects)
    run.ran = True
    return run
