# -*- coding: utf-8 -*-
"""MCP 服务化子包（需求3 主体）：把本工具既有的确定性能力编译成标准工具清单，
让千问办公 / WorkBuddy 等 MCP-capable Agent 直接发现并执行。

设计红线（贯穿全文，不可放宽）：
- 所有写类执行一律走 script_cmd.run_script_api 既有门控（127.0.0.1 + kind=tool
  + status=active + confirm 门控 + _ALLOWED_PKG 白名单），MCP 层只搬运、不放宽。
- 核心分析工具均为只读（summary/query/gate/audit 只读；git.* 仅 log/remote-diff/
  untracked/ci-check 只读），不暴露任何 push/pull/sync 写操作。
- stdio 模式下 stdout 只能是纯 JSON-RPC 行；任何人读日志一律走 stderr。

单一事实源：catalog.py 的 list_tools / call_tool。stdio 与 HTTP（server.py 的 /mcp）
两套传输壳共用同一 catalog，保证「Agent 调用的结果」与「浏览器/Agent HTTP 调用的结果」口径一致。
"""
from __future__ import annotations

from .catalog import (  # noqa: F401
    SERVER_INFO,
    CORE_TOOLS,
    list_tools,
    call_tool,
    handle_jsonrpc,
)

__all__ = ["SERVER_INFO", "CORE_TOOLS", "list_tools", "call_tool", "handle_jsonrpc"]
