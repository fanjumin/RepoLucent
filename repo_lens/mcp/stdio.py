# -*- coding: utf-8 -*-
"""MCP stdio 服务端壳（需求3 主体最小可跑闭环）。

协议帧对齐 scriptlib/subprocess_harness 的 _DEMO_SERVER：行分隔 JSON-RPC、通知不回、
未知方法返回 -32601、业务异常降级为 content 错误而非崩断流。这样 harness 能直接当它的
e2e 客户端（见 §2.3 验收）。

安全红线（EXT-a）：stdout 只能是纯 JSON-RPC 行。一切人读日志走 stderr；库告警、
GBK 崩溃都会毁掉帧。stdout 写后务必 flush。注意：cli.run() 已对 stdout 做
reconfigure(encoding="utf-8", errors="replace")，本模块不再重复设置，但仍只写 JSON。
"""
from __future__ import annotations

import sys
import json

from .catalog import handle_jsonrpc


def serve(cfg) -> None:
    """从 stdin 逐行读 JSON-RPC，向 stdout 写应答，直到 EOF。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            # 坏帧：静默跳过，不污染 stdout
            continue
        rid = msg.get("id")
        if rid is None:
            # 通知：不回（与 demo 一致，防挂起）
            continue
        try:
            resp = handle_jsonrpc(cfg, msg)
        except Exception as e:  # 极端兜底：绝不让一帧错误崩掉整个流
            resp = {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"server error: {e}"}],
                               "isError": True}}
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            # stdout 写不出去（如被关闭）：转 stderr 记录，正常退出
            print(f"[mcp:stdio] 写出响应失败：{e}", file=sys.stderr)
            return
