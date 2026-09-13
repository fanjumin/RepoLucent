# -*- coding: utf-8 -*-
"""v1.8.0 阶段三 3-C（OPEN-C / LLM-5）验收：流式输出 + token 用量统计。

设计要点：流式实现刻意拆成「传输」与「折叠」两半——
- ``iter_sse`` / ``reduce_stream`` 是**纯函数**，可完全脱离网络单测；
- ``chat_stream`` 只负责 HTTP 传输与回调，故本套件用**真实本地 HTTP 服务**
  （ThreadingHTTPServer 发 SSE）跑通一次端到端，再用替身驱动异常分支。

覆盖矩阵：
  1) iter_sse：识别 data: 帧，跳过注释/空行/event: 等噪声
  2) iter_sse：`[DONE]` 终止哨兵，其后帧不再产出
  3) iter_sse：单帧 JSON 非法 → 跳过该帧，不中断整条流
  4) reduce_stream：content 分片按序拼接 == 完整正文
  5) reduce_stream：tool_calls 按 index 归并分片参数，还原为 dict
  6) reduce_stream：末帧 usage 被捕获
  7) reduce_stream：无函数名的半帧被丢弃（不可还原）
  8) reduce_stream：arguments 非法 JSON → 退化为 {"_raw": ...}（与一次性路径同构）
  9) chat_stream 端到端（真实 HTTP + SSE）：回调收到增量、正文与 usage 正确、
     服务端确证收到 stream=true 与 include_usage、Authorization 已带
 10) chat_stream：HTTP 500 → 抛 LLMError（不穿透），按 max_retries 重试
 11) chat_stream：**已吐字后不再重试**（重试会造成文本重复）——urlopen 恰调用 1 次
 12) Provider 基类兜底：不支持流式的 provider 自动退化为一次性调用，仅回调一次
 13) runner：usage 跨轮累计并出现在 to_dict()；缺省路径 streamed=False（行为不变）
 14) runner：传 on_delta 时才走流式（streamed=True），兜底 provider 下仍拿到 usage

启动方式：python verify_llm_stream.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/llm_stream_verify.json。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent.llm.providers import openai_compat as OC              # noqa: E402
from repo_lucent.llm.providers.base import ChatResult, Provider, ToolCall  # noqa: E402

RESULTS: list[dict] = []

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:240]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

def _frame(obj) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n"

# ------------------------------------------------------------------ 1~3) iter_sse ----

def case_iter_sse() -> None:
    lines = [
        ": keep-alive comment\n",
        "\n",
        "event: message\n",
        'data: {"choices":[{"delta":{"content":"a"}}]}\n',
        "data:   \n",                                   # 空载荷跳过
        'data: {"choices":[{"delta":{"content":"b"}}]}\n',
    ]
    got = list(OC.iter_sse(lines))
    texts = [c["choices"][0]["delta"]["content"] for c in got]
    check("iter_sse_parses_frames_and_skips_noise", texts == ["a", "b"], f"texts={texts}")

    lines2 = ['data: {"n":1}\n', "data: [DONE]\n", 'data: {"n":2}\n']
    got2 = list(OC.iter_sse(lines2))
    check("iter_sse_stops_at_done_sentinel",
          [c.get("n") for c in got2] == [1], f"got={[c.get('n') for c in got2]}")

    lines3 = ['data: {not json}\n', 'data: {"n":7}\n', "data: [DONE]\n"]
    got3 = list(OC.iter_sse(lines3))
    check("iter_sse_skips_malformed_frame",
          [c.get("n") for c in got3] == [7], f"got={[c.get('n') for c in got3]}")

# ------------------------------------------------------------------ 4~8) reduce ----

def case_reduce_stream() -> None:
    def delta(text):
        return {"choices": [{"delta": {"content": text}}]}

    r = OC.reduce_stream([delta("你"), delta("好"), delta("世界")])
    check("reduce_concatenates_content_deltas", r.content == "你好世界",
          f"content={r.content!r}")

    # 两次工具调用，参数被切成多片且交错到达（OpenAI 真实形态）
    frag = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a",
             "function": {"name": "repo_summary", "arguments": '{"sc'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "call_b",
             "function": {"name": "repo_query", "arguments": '{"se'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'ope": "plugins"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": 'lect": ["name"]}'}}]}}]},
    ]
    r2 = OC.reduce_stream(frag)
    names = [c.name for c in r2.tool_calls]
    ok5 = (names == ["repo_summary", "repo_query"]
           and r2.tool_calls[0].arguments == {"scope": "plugins"}
           and r2.tool_calls[1].arguments == {"select": ["name"]}
           and [c.id for c in r2.tool_calls] == ["call_a", "call_b"])
    check("reduce_assembles_fragmented_tool_calls", ok5,
          f"names={names} args={[c.arguments for c in r2.tool_calls]}")

    usage = {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    r3 = OC.reduce_stream([delta("x"), {"choices": [], "usage": usage}])
    check("reduce_captures_usage_from_last_frame", r3.usage == usage,
          f"usage={r3.usage}")

    r4 = OC.reduce_stream([{"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": '{"a":1}'}}]}}]}])
    check("reduce_drops_nameless_tool_slot", r4.tool_calls == [],
          f"tool_calls={r4.tool_calls}")

    r5 = OC.reduce_stream([{"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1",
         "function": {"name": "f", "arguments": "{broken"}}]}}]}])
    ok8 = len(r5.tool_calls) == 1 and r5.tool_calls[0].arguments == {"_raw": "{broken"}
    check("reduce_degrades_invalid_args_json", ok8,
          f"args={r5.tool_calls[0].arguments if r5.tool_calls else None}")

# -------------------------------------------------------------- 9~10) 真实 HTTP ----

class _SSEHandler(BaseHTTPRequestHandler):
    """极简 SSE / 500 桩。mode=ok 时发流式 SSE；mode=err 时返回 500。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, *a):                       # 静音
        pass

    def do_POST(self):                               # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        self.server.requests.append({                       # type: ignore[attr-defined]
            "body": json.loads(raw.decode("utf-8") or "{}"),
            "auth": self.headers.get("Authorization"),
            "accept": self.headers.get("Accept"),
        })
        if getattr(self.server, "mode", "ok") == "err":
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"boom")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        body = "".join([
            _frame({"choices": [{"delta": {"content": '{"find'}}]}),
            "\n",
            _frame({"choices": [{"delta": {"content": 'ings": []}'}}]}),
            "\n",
            _frame({"choices": [], "usage": {"prompt_tokens": 21,
                                             "completion_tokens": 7,
                                             "total_tokens": 28}}),
            "\n",
            "data: [DONE]\n",
        ])
        self.wfile.write(body.encode("utf-8"))
        self.wfile.flush()

def _serve(mode: str = "ok"):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    srv.mode = mode                                  # type: ignore[attr-defined]
    srv.requests = []                                # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"

def case_chat_stream_over_http() -> None:
    srv, base = _serve("ok")
    try:
        prov = OC.OpenAICompat(base_url=base, api_key="sk-unit", model="m",
                               timeout_s=5.0, max_retries=0)
        seen: list[str] = []
        res = prov.chat_stream([{"role": "user", "content": "hi"}],
                               on_delta=seen.append)
        req = srv.requests[0] if srv.requests else {}
        body = req.get("body") or {}
        ok = (res.content == '{"findings": []}'
              and seen == ['{"find', 'ings": []}']
              and res.usage == {"prompt_tokens": 21, "completion_tokens": 7,
                                "total_tokens": 28}
              and body.get("stream") is True
              and (body.get("stream_options") or {}).get("include_usage") is True
              and req.get("auth") == "Bearer sk-unit"
              and "text/event-stream" in (req.get("accept") or ""))
        check("chat_stream_end_to_end_over_http", ok,
              f"content={res.content!r} deltas={len(seen)} usage={res.usage} "
              f"stream={body.get('stream')} auth={bool(req.get('auth'))}")
    finally:
        srv.shutdown()
        srv.server_close()

def case_chat_stream_http_error() -> None:
    srv, base = _serve("err")
    try:
        prov = OC.OpenAICompat(base_url=base, api_key="", model="m",
                               timeout_s=5.0, max_retries=1, backoff_base=0.0)
        err = None
        try:
            prov.chat_stream([{"role": "user", "content": "hi"}])
        except Exception as e:  # noqa: BLE001
            err = e
        calls = len(srv.requests)
        ok = (isinstance(err, OC.LLMError) and calls == 2
              and "500" in str(err))
        check("chat_stream_http_error_raises_and_retries", ok,
              f"err={type(err).__name__}: {err} calls={calls}")
    finally:
        srv.shutdown()
        srv.server_close()

# --------------------------------------------- 11) 已吐字后不得重试（反重复）----

class _StreamResp:
    """替身响应：按行产出，可在第 N 行后抛指定异常（模拟流中途断连）。"""

    def __init__(self, lines, fail_after=None, exc=None):
        self._lines = lines
        self._fail_after = fail_after
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for i, ln in enumerate(self._lines):
            if self._fail_after is not None and i >= self._fail_after:
                raise self._exc or ConnectionResetError("reset")
            yield ln.encode("utf-8")

def case_no_retry_after_emitting() -> None:
    calls = {"n": 0}
    lines = [_frame({"choices": [{"delta": {"content": "first"}}]}), "\n"]

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        # fail_after=1：产出首帧（index 0）后立刻断连，用于验证「已吐字则不重试」
        return _StreamResp(lines, fail_after=1, exc=ConnectionResetError("peer reset"))

    stub = types.SimpleNamespace(urlopen=_urlopen, Request=urllib.request.Request)
    orig = OC.urllib
    OC.urllib = types.SimpleNamespace(error=urllib.error, request=stub)  # type: ignore[assignment]
    try:
        prov = OC.OpenAICompat(base_url="http://127.0.0.1:1", api_key="", model="m",
                               timeout_s=1.0, max_retries=3, backoff_base=0.0)
        seen: list[str] = []
        err = None
        try:
            prov.chat_stream([{"role": "user", "content": "hi"}], on_delta=seen.append)
        except Exception as e:  # noqa: BLE001
            err = e
        ok = (isinstance(err, OC.LLMError) and calls["n"] == 1
              and seen == ["first"])
        check("no_retry_after_first_delta", ok,
              f"urlopen_calls={calls['n']} deltas={seen} err={err}")
    finally:
        OC.urllib = orig                              # type: ignore[assignment]

# --------------------------------------------- 12) 基类兜底（非流式 provider）----

def case_base_fallback() -> None:
    class Plain(Provider):
        name = "plain"
        supports_stream = False

        def chat(self, messages, tools=None, model=None, temperature=0.0,
                 timeout_s=None):
            return ChatResult(content="一次性正文",
                              usage={"total_tokens": 3})

    seen: list[str] = []
    res = Plain().chat_stream([{"role": "user", "content": "x"}], on_delta=seen.append)
    check("base_provider_stream_falls_back_to_chat",
          seen == ["一次性正文"] and res.content == "一次性正文"
          and (res.usage or {}).get("total_tokens") == 3,
          f"deltas={seen} usage={res.usage}")

# --------------------------------------------- 13~14) runner 接线与用量累计 ----

def case_runner_wiring(cfg, data, dims, provider, **kw):
    from repo_lucent.llm import runner as LR
    return LR.run_semantic_audit(cfg, data, dims, provider=provider,
                                 use_tools=False, **kw)

def case_runner_usage() -> None:
    from repo_lucent.cli import _analyze, _build_argparser, _setup

    out_dir = HERE / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    args = _build_argparser().parse_args(
        ["audit", "--repo", str(HERE / "tests" / "fixture_repo"),
         "--out", str(out_dir)])
    cfg = _setup(args)
    data, _d, _pc = _analyze(types.SimpleNamespace(no_cache=False,
                                                  deterministic=False), cfg)
    dims = None
    usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    class FakeProvider(Provider):
        name = "fake"
        supports_tools = False

        def chat(self, messages, tools=None, model=None, temperature=0.0,
                 timeout_s=None):
            return ChatResult(content=json.dumps({"findings": []}), usage=usage)

    # 13) 缺省路径：不走流式，但 usage 照记
    r1 = case_runner_wiring(cfg, data, dims, FakeProvider())
    check("runner_records_usage_without_streaming",
          r1.ran and r1.usage == usage and r1.streamed is False
          and r1.to_dict().get("usage") == usage,
          f"ran={r1.ran} usage={r1.usage} streamed={r1.streamed}")

    # 14) 显式 opt-in：走流式（兜底 provider 下仍应拿到 usage 与增量回调）
    seen: list[str] = []
    r2 = case_runner_wiring(cfg, data, dims, FakeProvider(), on_delta=seen.append)
    ok14 = (r2.ran and r2.streamed is True and len(seen) == 1
            and r2.usage == usage)
    check("runner_streams_only_when_on_delta_given", ok14,
          f"ran={r2.ran} streamed={r2.streamed} deltas={len(seen)} usage={r2.usage}")

def main() -> int:
    case_iter_sse()
    case_reduce_stream()
    case_chat_stream_over_http()
    case_chat_stream_http_error()
    case_no_retry_after_emitting()
    case_base_fallback()
    case_runner_usage()

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "llm_stream_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
