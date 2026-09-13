# -*- coding: utf-8 -*-
"""外部工具适配器注册表（审计 §4.3）：按 kind 分派，替代单一 `_ALLOWED_PKG` 前缀判断。

已注册 kind（v1.8.0 起四种齐全）：

| kind | 适配器 | 执行形态 | 安全依据 |
|---|---|---|---|
| `builtin` | `BuiltinAdapter` | 进程内 `importlib` 调 `main(argv)` | entry 前缀白名单（scriptlib / packs） |
| `mcp` | `McpAdapter` | outbound JSON-RPC 调外部 MCP Server | `settings.mcp_servers` 白名单 |
| `binary` | `BinaryAdapter` | 子进程执行注册表声明的可执行程序 | 命令只来自注册表；list 形式、无 shell |
| `python_pkg` | `PythonPkgAdapter` | 子进程 `python -m <第三方包>` | module 名严格校验（挡选项/路径注入） |

新增一种外部工具：在 adapters/ 下写一个 Adapter 子类并在此注册即可，
`run_script_api` 无需改动；三重门控由 script_cmd 统一施加，不随适配器分派而放宽。
"""
from __future__ import annotations

from .base import (ADAPTERS, Adapter, AdapterError, NormalizedResult,
                   argv_to_arguments, dispatch, get_adapter, register)
from .binary import BinaryAdapter
from .builtin import BuiltinAdapter
from .mcp import McpAdapter, McpOutboundError
from .python_pkg import PythonPkgAdapter

register(BuiltinAdapter())
register(McpAdapter())
register(BinaryAdapter())          # EXT-2（v1.8.0 / OPEN-A）
register(PythonPkgAdapter())       # EXT-3（v1.8.0 / OPEN-A）

__all__ = ["Adapter", "AdapterError", "NormalizedResult", "ADAPTERS",
           "BuiltinAdapter", "McpAdapter", "McpOutboundError",
           "BinaryAdapter", "PythonPkgAdapter",
           "dispatch", "get_adapter", "register", "argv_to_arguments"]
