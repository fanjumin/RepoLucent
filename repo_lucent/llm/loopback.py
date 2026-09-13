# -*- coding: utf-8 -*-
"""MCP 回环客户端（设计文档 §3.2 第 2 条）：自身 LLM 也是 MCP 客户端。

`llm.runner` 要调工具时，不做任何直连函数调用，而是启动本工具自己的
`python -m repo_lucent mcp` 子进程，用现成的 `JsonRpcProc` 走标准 tools/call。
因此 LLM 与外部 Agent 走**同一条执行面、同一 confirm 门控**，零新增执行路径。

安全红线：
- 默认 `allow_confirmed_writes=False` —— 即便模型想调写类脚本，也只给 dry-run
  （confirm=false）。要放开必须显式声明，且仍受 run_script_api 全套门控约束。
- 只读核心工具（repo.*）无门控问题，可直接调用。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from ..scriptlib.subprocess_harness import JsonRpcProc

_TOOL_ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根（repo_lucent 的父目录）


class McpLoopback:
    """连到本工具自身 MCP stdio 服务的轻量客户端。"""

    def __init__(self, repo_root, out_dir, python_exe: str | None = None,
                 startup_timeout_s: float = 25.0):
        self.repo_root = str(repo_root)
        self.out_dir = str(out_dir)
        self.python_exe = python_exe or sys.executable
        self.startup_timeout_s = startup_timeout_s
        self._proc: JsonRpcProc | None = None
        self._next_id = 0
        self._tools_cache: list[dict] | None = None
        self.tool_calls_made = 0

    # ---- 生命周期 ----
    def start(self) -> None:
        env = dict(os.environ)
        # 让子进程可导入 repo_lucent（等价于「已安装」场景，但不写死本工具绝对路径）
        env["PYTHONPATH"] = str(_TOOL_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = JsonRpcProc(
            [self.python_exe, "-m", "repo_lucent", "mcp",
             "--repo", self.repo_root, "--out", self.out_dir],
            env=env)
        proc.start()
        self._proc = proc
        # MCP 握手：initialize（必须最先）
        resp = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vr-insight-llm-loopback", "version": "1.0"},
        }, timeout_s=self.startup_timeout_s)
        if not resp or "result" not in resp:
            self.close()
            raise RuntimeError("MCP 回环握手失败（initialize 无 result）")

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.close()
            except Exception:  # noqa: BLE001 - 关闭失败不该影响主流程
                pass
            self._proc = None

    # ---- 底层 JSON-RPC ----
    def _request(self, method: str, params: dict | None = None,
                 timeout_s: float = 30.0) -> dict | None:
        if self._proc is None:
            raise RuntimeError("McpLoopback 未启动")
        self._next_id += 1
        rid = self._next_id
        self._proc.send(rid, method, params)
        return self._proc.read(rid, timeout_s=timeout_s)

    # ---- 工具面 ----
    def list_tools(self, refresh: bool = False) -> list[dict]:
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        resp = self._request("tools/list", {})
        tools = ((resp or {}).get("result") or {}).get("tools") or []
        self._tools_cache = list(tools)
        return self._tools_cache

    def call_tool(self, name: str, arguments: dict | None = None,
                  confirm: bool = False, timeout_s: float = 60.0) -> dict:
        """走标准 tools/call 执行一个 MCP 工具，返回归一化结果 dict。"""
        args = dict(arguments or {})
        if confirm:
            args["confirm"] = True
        resp = self._request("tools/call", {"name": name, "arguments": args},
                             timeout_s=timeout_s)
        self.tool_calls_made += 1
        if not resp:
            return {"isError": True, "text": f"tools/call 超时或无响应：{name}"}
        if "error" in resp:
            return {"isError": True, "text": f"JSON-RPC error: {resp['error']}"}
        result = resp.get("result") or {}
        parts = result.get("content") or []
        text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        return {"isError": bool(result.get("isError")), "text": text}

    # ---- 上下文管理 ----
    def __enter__(self) -> "McpLoopback":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def tool_result_to_message(call_id: str, result: dict, max_chars: int = 8000) -> dict:
    """把工具结果包装成 OpenAI 的 tool 角色消息。

    超长结果截断，避免把整仓上下文炸进对话窗口（模型端 token 成本与稳定性）。
    """
    text = result.get("text", "")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"
    return {"role": "tool", "tool_call_id": call_id, "content": text}
