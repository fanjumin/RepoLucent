# -*- coding: utf-8 -*-
"""LLM provider 契约层（P2 需求2：自身多模型 LLM 集成）。

设计约束（设计文档 §3 + 第三方审计报告）：
- 纯标准库，零第三方依赖（provider 走 urllib，见 providers/openai_compat.py）。
- 契约极小：chat(messages, tools=..., model=...) -> ChatResult{content, tool_calls}。
- 一切可恢复失败以 LLMError 抛出，由 runner 统一降级为「导出任务书给外部 Agent」，
  绝不拖垮分析管线（LLM-3）。
- 密钥只经 settings.get_secret（环境变量）注入，本层不持有、不落盘、不进 repr（LLM-1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


class LLMError(Exception):
    """LLM 通道可恢复错误：网络抖动、超时、熔断开启、协议解析失败等。

    runner 捕获后一律降级（ran=False + reason），不向上抛给审计主流程。
    """


@dataclass
class ToolCall:
    """模型发起的一次工具调用请求。"""
    id: str
    name: str                       # 已还原为 MCP 原名（如 repo.summary）
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatResult:
    """一次 chat 的归一化结果，屏蔽各 provider 的响应差异。"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    #: token 用量（LLM-5）；网关未回传时为 None。形如
    #: ``{"prompt_tokens": n, "completion_tokens": m, "total_tokens": t}``。
    usage: dict | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class Provider:
    """Provider 抽象基类：新增 provider 只需实现 chat()。"""

    #: 人类可读标识，用于日志/降级原因
    name = "base"

    #: 是否支持工具调用（不支持时 runner 自动退化为纯文本判定）
    supports_tools = False

    #: 是否支持**真流式**输出（LLM-5）。False 时 `chat_stream` 退化为一次性回调。
    supports_stream = False

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             model: str | None = None, temperature: float = 0.0,
             timeout_s: float | None = None) -> ChatResult:
        raise NotImplementedError

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    model: str | None = None, temperature: float = 0.0,
                    timeout_s: float | None = None,
                    on_delta=None) -> ChatResult:
        """流式 chat 的**基类兜底**：不支持流式的 provider 一次性返回，只回调一次。

        这样上层可以统一按「流式 API」书写，无需为每个 provider 分支判定。
        """
        res = self.chat(messages, tools=tools, model=model,
                        temperature=temperature, timeout_s=timeout_s)
        if on_delta is not None and res.content:
            on_delta(res.content)
        return res

    def __repr__(self) -> str:
        # 基类兜底：子类若持有密钥，须自行脱敏（见 openai_compat.OpenAICompat.__repr__）
        return f"<{type(self).__name__} name={self.name}>"


# ---------------------------------------------------------------------------
# MCP 工具名 ⇄ OpenAI function 名 的双向映射
# OpenAI function.name 限定 ^[a-zA-Z0-9_-]{1,64}$，而 MCP 工具名含点号（repo.summary），
# 故出口做一次可逆替换，入口还原，保证回环调用打到真实 MCP 工具。
# ---------------------------------------------------------------------------
def sanitize_tool_name(name: str) -> str:
    return name.replace(".", "_").replace("/", "_").replace(":", "_")


def restore_tool_name(name: str, known: list[str]) -> str:
    """把 OpenAI 返回的 function name 还原为 MCP 原名。

    先命中已知工具表（精确），失败时再退化为下划线→点号的首段还原。
    """
    for k in known:
        if k == name:
            return k
    for k in known:
        if sanitize_tool_name(k) == name:
            return k
    return name


def to_openai_tools(mcp_tools: list[dict]) -> tuple[list[dict], list[str]]:
    """MCP tools/list 结果 → OpenAI tools 数组，同时返回原名清单供还原。"""
    out: list[dict] = []
    names: list[str] = []
    for t in mcp_tools or []:
        nm = t.get("name")
        if not nm:
            continue
        names.append(nm)
        out.append({
            "type": "function",
            "function": {
                "name": sanitize_tool_name(nm),
                "description": (t.get("description") or "")[:1024],
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            },
        })
    return out, names
