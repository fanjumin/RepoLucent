# -*- coding: utf-8 -*-
"""脚本资产库（scriptlib）—— 从 AI Agent 开发期脚本中提炼、参数化后的一等可复用能力。

设计边界（见《脚本资产整合方案》）：
- 只收纳"通用、可参数化、优先标准库、无仓库/内网硬绑定"的脚本；一次性审计探针不入库。
- 每个脚本都提供 main(argv) -> int 入口，退出码语义与工具一致：0=通过、1=发现问题、2=参数/环境错误。
- 每个脚本都可被 `repolens.py script run <id>` 经 importlib 进程内调用，或独立 `python -m` 运行。

元数据事实源在 ../registry.core.json（内核通用资产）与 ../packs/*/registry.json
（场景化 pack 资产）；本包不重复版本号，避免双源分叉。
"""

SCRIPTLIB_VERSION = "1.0"
