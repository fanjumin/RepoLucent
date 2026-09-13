# -*- coding: utf-8 -*-
"""outbound MCP 适配器（审计 EXT-4 + 总计划 P2-1）：本工具**作为客户端**消费外部 MCP Server。

与 inbound（`repo_lucent/mcp/`）方向相反但共用同一套 JSON-RPC 语义：
inbound 是"让外部 Agent 调我"，outbound 是"我去调外部 MCP Server"。

实现要点：
- stdio 传输复用既有 `scriptlib.subprocess_harness.JsonRpcProc`（审计 §4.3-3 要求：
  把"验证模板"升级为"运行时集成通道"）；HTTP 传输用标准库 urllib，零第三方依赖。
- 每个 server 一个长驻会话（首次调用时握手 initialize），带超时与进程回收。
- 传输/协议/超时全部可失败降级为 NormalizedResult(ok=False)，**绝不向上抛异常**。

安全红线：
- 命令以 list 形式 spawn，**绝不使用 shell=True**；
- 未配置 server 一律拒绝（白名单来自 settings.mcp_servers，不接受调用方任意 command）；
- 本适配器只负责"调用"，是否真执行由上层 confirm 门控决定，此处不自动放行。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

from ..subprocess_harness import JsonRpcProc
from .base import Adapter, AdapterError, NormalizedResult, argv_to_arguments

PROTOCOL_VERSION = "2024-11-05"

#: 子进程标记：本工具派生的 outbound MCP 子进程会带上它。
#: 必要性——若不标记，子进程继承同一份 settings（含 mcp_servers）后会再去发现并派生
#: 同一批 server，形成"发现→派生→再发现"的进程爆炸。带上标记后子进程不再做出站发现
#: （它只作为被消费的服务端），链路在第一层即被切断。
_CHILD_ENV_MARK = "REPO_LUCENT_MCP_CHILD"


def in_mcp_child() -> bool:
    """当前进程是否为本工具派生的 outbound MCP 子进程。"""
    return os.environ.get(_CHILD_ENV_MARK) == "1"


class McpOutboundError(Exception):
    """外部 MCP Server 不可达 / 协议错误 / 超时。"""


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class McpOutboundClient:
    """连到一个外部 MCP Server（stdio 子进程 或 HTTP 端点）。"""

    def __init__(self, spec: dict):
        self.name = spec.get("name") or "<unnamed>"
        self.transport = (spec.get("transport") or "stdio").lower()
        self.command = spec.get("command")
        self.args = list(spec.get("args") or [])
        self.env = dict(spec.get("env") or {})
        self.url = spec.get("url")
        self.headers = dict(spec.get("headers") or {})
        self.timeout_s = float(spec.get("timeout_s", 30))
        self._proc: JsonRpcProc | None = None
        self._next_id = 0
        self._started = False

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._started:
            return
        if self.transport == "stdio":
            if not self.command:
                raise McpOutboundError(f"server {self.name}: stdio 传输缺少 command")
            env = dict(os.environ)
            env.update({str(k): str(v) for k, v in self.env.items()})
            env.setdefault("PYTHONIOENCODING", "utf-8")
            # 切断出站发现的递归链：子进程不再派生它自己的出站 server
            env[_CHILD_ENV_MARK] = "1"
            proc = JsonRpcProc([str(self.command), *[str(a) for a in self.args]], env=env)
            try:
                proc.start()
            except Exception as e:  # noqa: BLE001
                raise McpOutboundError(f"server {self.name}: 启动失败 {type(e).__name__}: {e}")
            self._proc = proc
            resp = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "repolucent", "version": "1.0"},
            })
            if not resp or "result" not in resp:
                self.close()
                raise McpOutboundError(f"server {self.name}: MCP 握手失败（initialize 无 result）")
        elif self.transport == "http":
            if not self.url:
                raise McpOutboundError(f"server {self.name}: http 传输缺少 url")
        else:
            raise McpOutboundError(f"server {self.name}: 不支持的 transport={self.transport!r}")
        self._started = True

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.close()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._started = False

    # ---- JSON-RPC ----
    def _request(self, method: str, params: dict | None,
                 timeout_s: float | None = None) -> dict | None:
        timeout = float(timeout_s or self.timeout_s)
        payload = {"jsonrpc": "2.0", "id": 0, "method": method}
        if params is not None:
            payload["params"] = params

        if self.transport == "stdio":
            if self._proc is None:
                raise McpOutboundError(f"server {self.name}: stdio 会话未启动")
            self._next_id += 1
            rid = self._next_id
            self._proc.send(rid, method, params)
            return self._proc.read(rid, timeout_s=timeout)

        # ---- HTTP：单请求 POST ----
        payload["id"] = 1
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in self.headers.items()})
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            raise McpOutboundError(f"server {self.name}: HTTP 请求失败 {type(e).__name__}: {e}")
        except ValueError as e:
            raise McpOutboundError(f"server {self.name}: 响应非 JSON：{e}")
        if isinstance(raw, list):
            return raw[0] if raw else None
        return raw

    # ---- 工具面 ----
    def list_tools(self, timeout_s: float | None = None) -> list[dict]:
        self.start()
        resp = self._request("tools/list", {}, timeout_s=timeout_s)
        if not resp:
            raise McpOutboundError(f"server {self.name}: tools/list 超时或无响应")
        if "error" in resp:
            raise McpOutboundError(f"server {self.name}: {resp['error']}")
        return ((resp.get("result") or {}).get("tools") or [])

    def call_tool(self, tool: str, arguments: dict | None = None,
                  timeout_s: float | None = None) -> dict:
        """执行一个外部 MCP 工具，返回 {isError, text, json} 归一化结果。"""
        self.start()
        resp = self._request("tools/call", {"name": tool, "arguments": arguments or {}},
                             timeout_s=timeout_s)
        if not resp:
            return {"isError": True, "text": f"tools/call 超时或无响应：{tool}"}
        if "error" in resp:
            return {"isError": True, "text": f"JSON-RPC error: {resp['error']}"}
        result = resp.get("result") or {}
        parts = result.get("content") or []
        text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        parsed = None
        if text.strip().startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        return {"isError": bool(result.get("isError")), "text": text, "json": parsed}


# ---------------------------------------------------------------------------
# 会话注册表（按 server 名长驻复用，避免每次调用重启子进程）
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_SESSIONS: dict[str, McpOutboundClient] = {}

# 工具发现的可重入守卫（跨线程生效，故用进程级标志而非 thread-local）
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_ACTIVE = False


def get_client(name: str) -> McpOutboundClient:
    with _LOCK:
        c = _SESSIONS.get(name)
        if c is not None:
            return c
    spec = server_spec(name)
    if spec is None:
        raise McpOutboundError(f"未配置的 MCP server：{name}（在 settings.mcp_servers 中声明）")
    c = McpOutboundClient(spec)
    c.start()
    with _LOCK:
        _SESSIONS[name] = c
    return c


def release_all() -> None:
    with _LOCK:
        for c in _SESSIONS.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        _SESSIONS.clear()


# ---------------------------------------------------------------------------
# 配置读取（白名单：只认 settings.mcp_servers 里显式声明的 server）
# ---------------------------------------------------------------------------
def server_specs() -> list[dict]:
    from ...settings import mcp_servers
    if in_mcp_child():
        # 本进程是被消费的出站子进程：不再做出站发现，杜绝进程爆炸
        return []
    out = []
    for s in mcp_servers():
        if not s.get("enabled", True):
            continue
        out.append(s)
    return out


def server_spec(name: str) -> dict | None:
    for s in server_specs():
        if s.get("name") == name:
            return s
    return None


def discover_tools(server_name: str | None = None,
                   timeout_s: float | None = None) -> tuple[list[dict], list[str]]:
    """发现外部 MCP Server 暴露的工具。返回 (tools, errors)。

    单个 server 失败只记错误，不影响其它 server（隔离故障）。

    可重入守卫：若某个外部 server 又把本工具的 MCP 端点接了回去（或两 server 互指），
    tools/list 会触发"发现→调用→再发现"的无限递归。这里用进程级标志阻断嵌套发现。
    """
    global _DISCOVERY_ACTIVE
    with _DISCOVERY_LOCK:
        if _DISCOVERY_ACTIVE:
            return [], ["discovery re-entrancy guard：检测到嵌套工具发现，已阻断（防递归）"]
        _DISCOVERY_ACTIVE = True
    tools: list[dict] = []
    errors: list[str] = []
    try:
        names = [server_name] if server_name else [s.get("name") for s in server_specs()]
        for nm in names:
            try:
                c = get_client(nm)
                for t in c.list_tools(timeout_s=timeout_s):
                    tools.append({
                        "server": nm,
                        "name": t.get("name"),
                        "description": t.get("description") or "",
                        "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
                    })
            except Exception as e:  # noqa: BLE001
                errors.append(f"{nm}: {type(e).__name__}: {e}")
    finally:
        with _DISCOVERY_LOCK:
            _DISCOVERY_ACTIVE = False
    return tools, errors


# ---------------------------------------------------------------------------
# 适配器：registry 里 kind=mcp 的条目走这里
# ---------------------------------------------------------------------------
class McpAdapter(Adapter):
    """把一条 kind=mcp 的 registry 条目映射到外部 MCP Server 的某个工具。"""

    kind = "mcp"

    def run(self, spec: dict, argv: list[str], timeout_s: float | None = None) -> NormalizedResult:
        server = spec.get("server")
        tool = spec.get("tool")
        if not server or not tool:
            raise AdapterError("kind=mcp 条目需同时声明 server 与 tool")
        args = argv_to_arguments(list(argv), spec.get("inputs"))
        try:
            c = get_client(server)
            r = c.call_tool(tool, args, timeout_s=timeout_s)
        except McpOutboundError as e:
            return NormalizedResult(ok=False, exit_code=2, error="mcp_unreachable",
                                    hint=str(e), meta={"server": server, "tool": tool})
        meta = {"server": server, "tool": tool, "transport": c.transport}
        if r.get("isError"):
            return NormalizedResult(ok=False, exit_code=1, error="tool_reported_error",
                                    stdout=(r.get("text") or "")[:20000],
                                    json=r.get("json"), meta=meta)
        return NormalizedResult(ok=True, exit_code=0, stdout=(r.get("text") or "")[:20000],
                                json=r.get("json"), meta=meta)
