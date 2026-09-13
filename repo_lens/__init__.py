# -*- coding: utf-8 -*-
"""RepoLens —— 通用 Python 仓库架构洞察工具包。

本地开发辅助工具：解析仓库结构、核心模块、代码规模、边界与规范，
输出结构化的架构信息（JSON / Markdown / HTML / AI 上下文 / 本地控制台），
让开发者与 AI 助手无需每次通读源码即可掌握系统架构与开发规范。

分析口径经 settings.json 的 profile 段外置（档位 A 通用化）：
内置 verorun profile 服务 VeroRun 仓库，profiles/generic-python.json
可适配任意 Python 仓库。

本工具属于【本地开发工具】，不进入 Git 版本控制。
"""

TOOL_NAME = "repolens"
TOOL_VERSION = "2.0.0"

#: 产物 schema 版本（MAJOR.MINOR）—— 供人与 Agent 共同遵守的兼容契约。
#:
#: - MAJOR：删除字段、重命名字段、改变字段类型或语义。属破坏性变更，
#:   必须同步更新 report_md / report_html / report_ai 三个渲染器。
#: - MINOR：新增可选字段。消费方（含 Agent）应忽略未识别字段，不应报错。
#:
#: 当前产物顶层结构：meta / overview / core / plugins / interactions / standards
#: （--module 或 --plugin 时追加 deep_dive；发现前端仓库时追加 frontend；
#:   git 可用时追加 hotspots；v1.6.0 起追加 symbols 摘要段）。
#:
#: 版本沿革：
#:   1.1 frontend 段（MINOR，v1.4.0）
#:   1.2 analysis 段 / hotspots（MINOR，v1.5.0）
#:   1.3 symbols 摘要段（MINOR，v1.6.0）；完整符号倒排索引落在独立文件
#:       repo_lens_symbols.json，主 JSON 只保留 count/index_file 指针，避免撑大事实源。
#:   —— 此后至 v1.8.0 **维持 1.3**：v1.7.0（并行 AST / 内容寻址增量缓存）与
#:      v1.8.0（packs 分层 / SQLite 查询加速层 / 热点增量缓存）都是实现层优化，
#:      未增删改任何产物字段。3-B 的 index.db 与热点缓存均落在独立缓存根，
#:      热点新增的 source 字段属缓存态，已在 CLI 边界剥离，不进事实源。
#:   1.4 findings 规则引擎段（MINOR，v2.0.0，阶段 F）：新增可选顶层字段
#:       findings（schema_version / summary / items），由 repo_lens/rules/
#:       包只读派生（SPEC / SEC / ARCH / CMP 四类 15 条规则），不修改任何
#:       analyzer 产物；info 级不计入门禁，符合 --deterministic 可复现性。
SCHEMA_VERSION = "1.4"

#: 产物文件名常量（单一事实源）：更名六件套（v2.0.0）时集中改动此处。
ARTIFACT_JSON = "repo_lens.json"
ARTIFACT_MD = "repo_lens_report.md"
ARTIFACT_HTML = "repo_lens_report.html"
ARTIFACT_AI_CONTEXT = "AI_CONTEXT.md"
ARTIFACT_SYMBOLS = "repo_lens_symbols.json"
ARTIFACT_AGENTS_MD = "AGENTS.md"
