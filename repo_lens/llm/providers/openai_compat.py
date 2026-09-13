# -*- coding: utf-8 -*-
"""OpenAI Chat Completions 兼容 provider（标准库 urllib 实现，零第三方依赖）。

兼容：OpenAI / DashScope 兼容模式 / vLLM / Ollama OpenAI 网关 / 本地代理等
      —— 只要暴露 POST {base_url}/chat/completions 且响应体同构即可。

安全与稳定性（设计文档 §3.3）：
- LLM-1 密钥只由调用方从环境变量注入；本类不落盘、不打印，`__repr__` 恒定脱敏。
- LLM-3 超时/重试独立于分析管线，并带熔断：连续失败达阈值后短时间快速失败，
  避免外部模型抖动拖垮整台 serve。
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable, Iterator

from .base import ChatResult, LLMError, Provider, ToolCall

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class OpenAICompat(Provider):
    """OpenAI 兼容 chat provider。"""

    name = "openai_compat"
    supports_tools = True
    supports_stream = True

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout_s: float = 30.0, max_retries: int = 2,
                 backoff_base: float = 0.5,
                 circuit_threshold: int = 3, circuit_cooldown_s: float = 60.0):
        if not base_url:
            raise LLMError("缺少 base_url（settings 用 base_url_env 指向环境变量）")
        if not model:
            raise LLMError("缺少 model")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
        self._model = model
        self.timeout_s = float(timeout_s)
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = float(backoff_base)
        self.circuit_threshold = max(1, int(circuit_threshold))
        self.circuit_cooldown_s = float(circuit_cooldown_s)
        # 熔断状态（实例级；runner 每次构建新 provider，故不会跨任务污染）
        self._consecutive_failures = 0
        self._open_until = 0.0

    # ---- 密钥脱敏：repr/str 永不泄漏 ----
    def __repr__(self) -> str:
        return (f"<OpenAICompat model={self._model!r} base_url={self._base_url!r} "
                f"api_key={'***set***' if self._api_key else '(unset)'}>")

    __str__ = __repr__

    # ---- 熔断 ----
    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _note_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_threshold:
            self._open_until = time.monotonic() + self.circuit_cooldown_s

    def _note_success(self) -> None:
        self._consecutive_failures = 0
        self._open_until = 0.0

    # ---- 主入口 ----
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             model: str | None = None, temperature: float = 0.0,
             timeout_s: float | None = None) -> ChatResult:
        if self.circuit_open:
            raise LLMError("LLM 熔断开启（连续失败达阈值），本次请求快速失败")

        url = f"{self._base_url}/chat/completions"
        payload: dict = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        timeout = float(timeout_s or self.timeout_s)
        last_err: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self.backoff_base * (2 ** (attempt - 1)))
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8", errors="replace"))
                self._note_success()
                return self._parse(raw)
            except urllib.error.HTTPError as e:
                # 4xx（除 408/429）为不可恢复：鉴权/参数错，重试无意义
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:  # noqa: BLE001 - 错误体读取失败不影响主判
                    pass
                if e.code not in _RETRYABLE_STATUS:
                    self._note_failure()
                    raise LLMError(f"LLM HTTP {e.code}（不可恢复）：{detail or e.reason}")
                last_err = LLMError(f"LLM HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = LLMError(f"LLM 网络错误：{e}")
            except (ValueError, TypeError) as e:
                # 响应体非 JSON：不可恢复
                self._note_failure()
                raise LLMError(f"LLM 响应解析失败：{e}")

        self._note_failure()
        raise LLMError(f"LLM 请求失败（已重试 {self.max_retries} 次）：{last_err}")

    # ---- 流式入口（LLM-5）----
    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    model: str | None = None, temperature: float = 0.0,
                    timeout_s: float | None = None,
                    on_delta: Callable[[str], None] | None = None) -> ChatResult:
        """流式 chat：逐段回调增量文本，返回完整 ChatResult（含 usage）。

        与 `chat` 共用熔断 / 重试 / 密钥脱敏；请求体加 `stream=true`，并用
        `stream_options.include_usage` 索取末帧 usage（网关不支持时该选项被忽略，
        usage 为 None，不影响主流程）。

        重试语义（与一次性调用的关键差异）：**仅当尚未向外吐出任何增量时**才重试。
        已经回调过 `on_delta` 再重试会导致文本重复，故此时直接失败、交由 runner 降级。
        """
        if self.circuit_open:
            raise LLMError("LLM 熔断开启（连续失败达阈值），本次请求快速失败")

        payload: dict = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        timeout = float(timeout_s or self.timeout_s)
        last_err: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self.backoff_base * (2 ** (attempt - 1)))
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            emitted = False
            chunks: list[dict] = []
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    for ch in iter_sse(_iter_lines(resp)):
                        chunks.append(ch)
                        for piece in _delta_texts(ch):
                            emitted = True
                            if on_delta is not None:
                                on_delta(piece)
                self._note_success()
                return reduce_stream(chunks)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:  # noqa: BLE001 - 错误体读取失败不影响主判
                    pass
                if e.code not in _RETRYABLE_STATUS:
                    self._note_failure()
                    raise LLMError(f"LLM HTTP {e.code}（不可恢复）：{detail or e.reason}")
                last_err = LLMError(f"LLM HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError,
                    http.client.HTTPException) as e:
                # 流中途断连（IncompleteRead / BadStatusLine）不是 OSError 子类，
                # 必须显式纳入，否则会穿透到 runner 之外。
                last_err = LLMError(f"LLM 流式传输中断：{type(e).__name__}: {e}")
            except (ValueError, TypeError) as e:
                self._note_failure()
                raise LLMError(f"LLM 流式响应解析失败：{e}")
            if emitted:
                # 已向外吐字：重试会造成重复输出，故就地失败（不重试）。
                break

        self._note_failure()
        raise LLMError(f"LLM 流式请求失败（已重试 {self.max_retries} 次）：{last_err}")

    # ---- 响应归一化 ----
    @staticmethod
    def _parse(raw: dict) -> ChatResult:
        try:
            msg = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 响应结构异常（缺 choices[0].message）：{e}")

        content = msg.get("content") or ""
        calls: list[ToolCall] = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {"_raw": args}
            calls.append(ToolCall(id=c.get("id") or "", name=fn.get("name") or "",
                                  arguments=args))
        usage = raw.get("usage")
        return ChatResult(content=content, tool_calls=calls, raw=raw,
                          usage=usage if isinstance(usage, dict) else None)


# ---------------------------------------------------------------------------
# 流式解析（LLM-5）：传输与折叠分离，折叠部分为**纯函数**，可在无网络下单测
# ---------------------------------------------------------------------------
def _iter_lines(resp) -> Iterator[str]:
    """逐行读取 SSE 响应体，字节解码为 str。

    直接迭代 HTTP 响应对象（真实流式，不整体缓冲）；解码失败以 replace 兜底，
    绝不因个别坏字节中断整条流。
    """
    for raw in resp:
        yield raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def iter_sse(lines: Iterable[str]) -> Iterator[dict]:
    """SSE 文本行流 → JSON 帧序列（纯函数）。

    - 只认 ``data:`` 行；空行、注释行（``:`` 开头）、``event:`` / ``id:`` 等字段跳过；
    - ``data: [DONE]`` 为终止哨兵，到此结束（其后不再产出）；
    - 单帧 JSON 非法时**跳过该帧**而非抛异常——部分网关会插入纯文本心跳，
      不应因此中断整条流。
    """
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _delta_texts(chunk: dict) -> Iterator[str]:
    """取一帧里所有 choices 的增量文本（多数帧只有 1 个 choice）。"""
    for choice in (chunk.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        piece = (choice.get("delta") or {}).get("content")
        if piece:
            yield piece


def _loads_args(raw: str) -> dict:
    """把流式拼回的 arguments 串解析为 dict；非法则原样送进 `_raw`（与一次性路径同构）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def reduce_stream(chunks: Iterable[dict]) -> ChatResult:
    """把流式帧折叠为 ChatResult（纯函数）。

    - **content**：各帧增量文本顺序拼接，得到与一次性调用等价的完整正文；
    - **tool_calls**：OpenAI 用 ``index`` 标识同一次调用，``function.arguments``
      是**分片**到达的，故按 index 累加参数串，收尾统一 json 解析；
    - **usage**：`stream_options.include_usage` 生效时由末帧携带，取到即记。
    """
    parts: list[str] = []
    slots: dict[int, dict] = {}
    usage: dict | None = None

    for chunk in chunks:
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in (chunk.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                parts.append(piece)
            for tc in (delta.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index")
                idx = idx if isinstance(idx, int) else 0
                slot = slots.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = str(tc["id"])
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = str(fn["name"])
                if fn.get("arguments"):
                    slot["args"] += str(fn["arguments"])

    tool_calls = []
    for idx in sorted(slots):
        slot = slots[idx]
        if not slot["name"]:
            continue                    # 只有参数碎片、无函数名的半帧，不可还原故丢弃
        tool_calls.append(ToolCall(id=slot["id"], name=slot["name"],
                                   arguments=_loads_args(slot["args"])))
    return ChatResult(content="".join(parts), tool_calls=tool_calls,
                      raw={"stream": True}, usage=usage)
