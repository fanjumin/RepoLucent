#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RepoLens —— 模块入口（支持 `python -m repo_lens`，零路径硬编码）。

本文件使工具可通过模块方式调用。外部 Agent / MCP 客户端 / CI 一律使用：

    python -m repo_lens <subcommand> [--repo <仓库>] [--out <目录>]

该方式不依赖任何绝对文件路径，包位置由 PYTHONPATH 或 `pip install -e .` 决定。
顶层 `repolens.py` 仅为「直接 `python repolens.py`」的兼容入口，对外调用请统一用模块入口。
"""
from __future__ import annotations

from repo_lens.cli import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
