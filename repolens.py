# -*- coding: utf-8 -*-
"""RepoLens —— 命令行入口（本地开发辅助工具，不入 Git）。

通用 Python 仓库架构洞察工具：解析仓库结构、核心模块、代码规模、
边界与规范，输出 JSON / Markdown / HTML / AI 上下文 / 本地控制台。
对 VeroRun 仓库提供增强 profile（插件体系识别），其他 Python 仓库
可经 settings.json 的 profile 段或 profiles/generic-python.json 适配。

快速开始：
    python repolens.py --repo <仓库根目录>
    python repolens.py               # 仓库签名匹配时自动定位
    python -m repo_lens <subcommand> # 等价的模块入口

详细说明见同目录《开发者必读》与 SETTINGS.md。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_lens.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
