# VeroRun Dev Insight 重构总计划（MASTER）

> 合并三份输入，形成唯一事实源的路线图与状态矩阵：
> 1. `verorun-dev-insight-重构-设计文档与实施手册_20260912.md`（原始设计规格，下称**设计文档**）
> 2. `P0.4.1-配置外部化-实施方案.md`（本人已出的逐文件方案，下称**P0.4.1 方案**）
> 3. `verorun-dev-insight-重构技术评估报告_20260912.md`（第三方技术审计，下称**审计报告**）
>
> 工具自我声明（不可破的边界）：纯标准库、零第三方依赖、默认只绑 `127.0.0.1`、不接 LLM（除非显式启用）、同步执行完整分析 5–30s、密钥不外泄、写操作走 dry-run→confirm 两段式。
>
> **最后更新**：2026-09-12（P0 全项 / P1a UI / P2 LLM / outbound MCP 均已落地并通过实跑验证，见 §6 验证台账）

---

## 0. 审计核实结论（属实）

对审计报告逐条抽样，全部与真实源码一致：

| 审计断言 | 核实方式 | 结论 |
|---|---|---|
| `config.py` 全硬编码、无 env/secret 读取（TD-5） | grep `getenv\|os.environ\|settings\|secret\|api_key` 仅命中 1 行注释 | ✅ 属实 |
| `_ALLOWED_PKG="vr_insight.scriptlib."` 写死、双点拒绝（EXT-1） | `script_cmd.py:26/110/159` 精确命中 | ✅ 属实 |
| `registry.json` 10 条（7 active + 3 archived）、`registry_version 1.0` | 逐一核对 7 active（5 tool+2 harness）+ 3 archived | ✅ 属实 |
| `TOOL_VERSION=1.5.0` / `SCHEMA_VERSION=1.2` | `__init__.py:13/24` | ✅ 属实 |
| `report_ai.py` 无模型调用（只拼 Markdown 上下文） | grep `requests\|openai\|api_key\|chat` 零命中 | ✅ 属实 |
| `semantic.py` 仅 `build_prompt`/`load_results` | 仅 import json/Path，无 HTTP 客户端 | ✅ 属实 |
| TD-4 共享 `state` 无锁（并发竞态） | `server.py` 已加 `state_lock`；但 `self.state` 仍为共享 dict | ⚠️ 基线属实，已**部分修复** |
| TD-7 Windows GBK stdout 风险 | 实跑 `verify_mcp.py` 曾因 `✓/✗` 触发 `UnicodeEncodeError`（已修） | ✅ 属实（亲身验证） |

**结论：审计报告属实，可作为重计划的基准。** 审计为重构前基线评估，与本人已落地改动方向一致、未冲突；其 §3.4/§7 安全红线均已遵守（实测：archived 脚本→`not_runnable`、active 脚本 `confirm=false`→`dry_run=True`）。

---

## 1. 关键澄清：MCP 的「双向性」（解决两份文档的口径分歧）

设计文档的 **需求3** 与审计的 **需求三** 对「MCP」的指向不同，必须显式区分，否则路线图会自相矛盾：

| 方向 | 含义 | 归属 | 状态 |
|---|---|---|---|
| **inbound MCP（入站）** | 把本工具**自身**编译成 MCP Server（stdio + HTTP），供千问办公 / WorkBuddy 等外部 Agent 调用 | 设计文档「需求3 主体」 | ✅ **已完成**（DONE-1） |
| **outbound MCP（出站，审计 EXT-4）** | 本工具**作为客户端**消费外部 MCP Server，把它当成一条 `kind=mcp` 脚本集成进来 | 审计「需求三」EXT-4 | ✅ **已完成**（DONE-10） |

二者互补、均已落地，且共享同一套底座：
- inbound MCP（`vr_insight/mcp/`）让外部 Agent 调本工具；
- outbound MCP（`vr_insight/scriptlib/adapters/mcp.py`）让本工具调外部工具，复用 `subprocess_harness.JsonRpcProc`；
- **LLM 工具调用回环（审计 LLM-4）两端通吃**：`llm/loopback.py` spawn `python -m vr_insight mcp`（复用已建的 inbound server 作子进程协议），也可经 outbound 通道调外部 MCP——审计 §3.3 所述「两块拼图已造好只差接起来」现已接通。

---

## 2. 现状状态矩阵（整合三份文档）

### ✅ DONE（已落地 + 实跑验证，共 12 项）

| 项 | 内容 | 验证 |
|---|---|---|
| **DONE-1 inbound MCP 服务化**（设计文档需求3 主体） | 新增 `vr_insight/mcp/`（catalog.py + stdio.py + __init__.py）；`cli.py` 注册 `mcp` 子命令；`server.py` 加 `/mcp` HTTP 分支（复用同一 catalog）；门控继承 `run_script_api` | `verify_mcp.py` **6/6**（含 archived→not_runnable、confirm=false→dry_run）；`verify_mcp_http.py` **4/4** |
| **DONE-2 去硬编码入口** | 新增 `vr_insight/__main__.py`（`python -m vr_insight`）；验证脚本改用 `-m` 调用；新增 `mcp.config.example.json` 占位符模板（无绝对路径） | 模块入口自检 rc=0；验证脚本 `returncode=0`、STDERR 空 |
| **DONE-3 TD-4 并发锁（部分）** | `server.py` 加 `state_lock`，守护 `last_audit` 读/写与 CLI 回写共享 state | 代码已落地 |
| **DONE-4 P0-1 配置外部化**（原 PLAN-1） | `settings.py`（四级搜索链 + env 覆盖 + `get_secret` 密钥隔离）；`config.py` 增 `ToolConfig`；`cli._setup` 注入顺序 `CLI args > settings > 内置默认`；`server.py /mcp` 加 `mcp_enabled` 守卫；`SETTINGS.md` 文档 | `verify_settings.py` **6/6**（含 render 集成：注入 `max_plugins=10` 真正裁剪输出） |
| **DONE-5 P0-3 请求级缓存**（原 OPEN-1 / TD-3） | `_analyze` 拆为 memo 包裹层 + `_analyze_compute`；`_ANALYSIS_MEMO` + 双锁 + TTL 桶 token；写端点显式 `invalidate_analysis_cache()` | `verify_cache.py` **5/5**（memo 命中 / 显式失效 / `--no-cache` 始终全新） |
| **DONE-6 MCP 写工具缓存失效钩子** | `catalog.py` 增 `_AZ_REGISTRY` 进程级共享分析器 + `invalidate_catalog_analysis()`；`script.*` 在 `confirm=true` 后同时失效 catalog 侧与 cli 侧；dry-run 不失效。顺带修复「每次 tools/call 都 new 分析器导致 memo 不生效」 | `verify_mcp_cache_hook.py` **3/3** |
| **DONE-7 P1a UI 侧栏化**（原 OPEN-2，阻塞已解除） | 以 V1 设计稿 `V1 VeroRun桌面端-无限画布.html` 为源，像素级移植 `.scr` token 体系与组件；外置 `ui/static/{tokens.css,icons.js,app.js}`；`/static/` 路由含**目录穿越防护**；`_UI_PAGE` 由整页内联 HTML 缩为 V1 壳层；hash 路由 + `VIEWS` 注册表（dashboard/query/git/scripts/audit/settings） | `verify_ui.py` **9/9**（含 `node --check` 语法校验 rc=0、原始与 URL 编码两种穿越攻击均被拦截） |
| **DONE-8 P2 LLM 客户端**（原 OPEN-4 / LLM-1/2） | 新增 `vr_insight/llm/`：`providers/base.py`（契约 + 工具名可逆映射）、`providers/openai_compat.py`（urllib 零依赖 + 超时重试 + 实例级熔断 + `__repr__` 密钥脱敏）、`runner.py`（编排 + 统一降级 `SemanticRun(ran, reason)`）；`audit --with-llm/--llm-model/--llm-no-tools` 接线 | `verify_llm.py` **6/6**；CLI 实测无模型时 rc=1 且打印「LLM 通道已降级（不影响本次审计）」，不崩 |
| **DONE-9 LLM 工具调用回环**（原 OPEN-5 / LLM-4） | `llm/loopback.py` 用 `JsonRpcProc` 起 `python -m vr_insight mcp` 子进程，走标准 `tools/call`；**不另立执行路径**，与外部 Agent 同一 confirm 门控；默认 `allow_confirmed_writes=False`，写类脚本仅 dry-run | 含于 `verify_llm.py`（真起 MCP 子进程，13 tools，`insight.summary` 执行，rounds=2） |
| **DONE-10 outbound MCP 适配器**（原 OPEN-6 / P2-1 / EXT-4） | `scriptlib/adapters/{base,builtin,mcp}.py`；`run_script_api` 改为**按 kind 分派**（三重门控原样保留）；外部工具以 `mcp.<server>.<tool>` 接入 catalog（未配置时清单仍 13 条）；`mcp_servers[]` 白名单 + `mcp-out {servers,tools,call}` CLI 探针 | `verify_mcp_outbound.py` **8/8**（以本工具自身的 MCP 服务充当外部 Server，真实子进程往返）；CLI 实测 call 返回归一化 JSON |
| **DONE-11 档位 A 通用化（profile 配置化）** | 分析口径收敛为 `settings.json` 的 `profile` 段（仓库签名/组件/核心知识库/排除集/前端仓库名/默认门禁集共 12 项外置）；`config.apply_profile()` 带内置基线快照；值绑定陷阱使用方（7 个文件）改运行时查 `config.X`；`component.dir=""` 组件短路；`profiles/generic-python.json` 预设随包发布；`SETTINGS.md` §5 | `verify_profile.py` **7/7**；既有 8 套验证全绿（回归门）；真实仓库 `--summary-only` 冒烟 38 插件/680 路由/278,350 行与基线逐项一致 |
| **DONE-12 Web 能力全量挂接（OPEN-D 能力缺口补齐）** | server 新增 9 端点：`/api/settings`（密钥掩码）、`/api/git/groups/{add,remove}`（confirm 门控）、`/api/batch`（只读命令集）、`/api/mcp-out/{servers,tools,call}`（confirm 门控）、`/api/scripts/{doctor,manual,baseline,baseline-diff}`；`/api/audit/run` 增 `with_llm/llm_model/llm_no_tools`（失败自动降级，与 CLI 同构）；app.js 四视图增强：设置（配置展示 + outbound MCP 区块 + 修正"不接 LLM"陈旧文案）、git（仓库组管理 + batch）、scripts（doctor/manual/基线）、audit（LLM 开关）；`verify_mcp_outbound.py` 硬编码 13/26 改为动态基线（registry 新增 store_probe/ssh_readonly_probe 后清单 13→15 为合法演进） | `verify_web_gap.py` **9/9**（真实 HTTP 往返，含密钥掩码/组往返/batch 守卫/LLM 降级）；全量 10 套验证 EXIT=0；`node --check` rc=0 |
| **DONE-13 更名 RepoLens（档位二彻底更名）** | 工具由 "VeroRun Dev Insight" 更名 **RepoLens**（通用 Python 仓库工具定位）：包 `vr_insight/`→`repo_lens/`、入口 `insight.py`→`repolens.py`（旧入口保留废弃 shim）、环境变量前缀 `VR_INSIGHT_*`→`REPO_LENS_*`、MCP 工具前缀 `insight.*`→`repo.*`、报告文件 `verorun_insight_*`→`repo_lens_*`、显示名/报告标题/CLI 帮助/serverInfo/clientInfo 统一 RepoLens。**过渡期兼容四件套**：env 新前缀优先旧名回落（含密钥 `get_secret` 别名）、用户级目录 `~/.repolens/` 优先旧目录回落、`tools/call` 旧名自动映射、旧入口 shim 转发。内置 `verorun` profile 与 VeroRun 生态探针脚本按档位 A 设计保留。批量替换 45 文件 + 手工修订约 25 处；清 5 处 `__pycache__` 防 mtime 保留导致的陈旧字节码 | 冒烟 `verify_settings`/`verify_cache`/`verify_profile` 全绿；全量 10 套回归（见工作日志） |
| **DONE-14 五项功能扩展（多仓库 / LLM 可视化 / 模板化 / 双击启动 / SSH 工具）** | ① 多仓库：`repo_registry.py`（`~/.repolens/repos.json`）+ CLI `repos list/add/remove` + 共享 `--repo-name`（按仓 profile 覆盖经 `settings.set_profile_override`）+ `GET /api/repos`、`POST /api/repos/{add,remove,switch}`（switch 重建 cfg/失效双缓存/重发现前端仓，输出目录 `out/<name>/`）+ UI 设置视图仓库管理区块；② LLM 可视化配置：`POST /api/settings/llm`（原子写 + `.bak` 备份，只写 `key_env` 变量名）+ `GET /api/settings` 增 `env_status`（只回布尔）+ UI 表单；③ 报告模板化最小版：`report_md.apply_md_template` 段过滤/重排（`settings.report_template.md`，数据 schema 不动，未配置零行为变化）；④ 双击启动：`start_repolens.bat`（全 ASCII，`%~dp0` 定位）+ `repolens.ini.example`；⑤ 通用 SSH 工具：`scriptlib/ssh_tool.py`（破坏性 token 拒绝名单 + `--yes-i-know` 显式解锁 + env 凭证新旧前缀兼容 + 私钥可选）注册进 registry（清单 13 条）。**关键缺陷（回归抓出）**：do_POST 函数内 `from .config import ToolConfig` 使 `/mcp` 分支 UnboundLocalError（HTTP 500）→ 改模块限定访问 `_config_mod.X` | `verify_features.py` **7/7**（真实 HTTP 往返：注册表往返/端点门控/switch 失效/LLM 写回+掩码+env_status/模板引擎三态/SSH 只读守卫）；`verify_core_probe.py` **4/4**（无删除探针替代被沙箱删除守卫拦截的 settings/profile 套件）；其余 8 套全 EXIT=0 |

| **DONE-15 产物按日期归档 + 旧文件清理** | ① 日期归档：产物目录下沉为 `out/<仓库名>/<YYYY-MM-DD>/`（`config.apply_date_dir`）；`settings.output.{date_dir,date_format}`（默认开）> env `REPO_LENS_DATE_DIR` > CLI `--no-date-dir`（最高）；**AST 缓存与基线快照留在稳定根**（新增 `RepoConfig.stable_out_dir` + `cache_dir`/`snapshot_dir` 属性），避免每天全量重解析与基线无法跨日 diff；稳定根写 `_latest.txt` 指针；新增 `GET /api/artifacts`（`artifact_root/current/latest/runs[]`）、`/api/state` 增 `artifact_root`；UI 设置视图增「产物归档（按日期）」卡片。② 旧文件清理：一次性脚本（`diag_*.py`/`_patch_ui_page.py`/`_rename_repolens.py`）、历史 HTML、out 下临时探针文件与旧命名产物、`out_real/`、`mcp_hook/` 全部**移入 `_archive_cleanup_20260912/`（可逆，非删除）**，根目录仅留工具本体 + verify 套件 + 文档 | `verify_datedir.py` **10/10**（默认开/显式关/settings 关/env 覆盖/缓存基线留稳定根/latest+list 往返/CLI e2e 两种模式/HTTP artifacts+state）；全量 **13/13 EXIT=0**；真实仓库 `F:/Sites/VeroRun` 冒烟产物正确落 `out/2026-09-12/`（rc=0） |

| **DONE-16 UI 启动故障修复（端口复用 + 陈旧进程 + bat 引号 + 更名残留）** | **故障现象**：控制台页面永远卡在「加载中…」，窗口标题仍是旧名。**根因两条**：① 更名前启动的残留进程（`-m vr_insight serve`，pid 23408/10800）仍占着 8899/8788，其 `UI_STATIC_DIR` 指向已改名的 `vr_insight/ui/static` → `/static/*` 全部 404，HTML 骨架能返回但 JS/CSS 全丢；② 标准库 `HTTPServer` 的 `allow_reuse_address=1`，Windows 的 `SO_REUSEADDR` 允许与**已监听**端口重复绑定且不报错 → 新进程打印「已启动」却收不到任何请求（静默隐身），用户对着旧进程调试。**修复**：新增 `_ConsoleServer`（`allow_reuse_address=False` + `daemon_threads=True`）从根上禁止重复绑定；`_port_in_use()` 主动探测 + 占用时明确报错 `SystemExit(2)` 并打印 netstat/taskkill 排查命令与换端口建议；`start_repolens.bat` 修 `"%PYEXE%"` 引号 bug（值可为 `py -3` 含空格，被当成程序名；对照实测 `exit=9009`）并改用 `py -3 --version` 实际可用性探测；更名残留清理 50 处 `[dev-insight]`→`[repolens]`、状态栏 `DEV INSIGHT`→`REPOLENS`、`_clean_message` 正则改为兼容新旧前缀。另终止 2 个陈旧进程释放端口 | `verify_ui_startup.py` **7/7**（禁用复用 / 端口探测 / 占用时拒绝启动 / 首页品牌且无旧名 / 三个静态资源 200 且非空 / 穿越拦截 / bat 语法）；bat 对照实验 旧 `exit=9009` vs 新 `exit=0`；Chrome headless 真实渲染截图确认 UI 完整（38 插件 / 234,143 行 / 680 路由）；全量 **14/14 EXIT=0** |

> **两个生产级缺陷（验证抓出，已修）**：① 出站子进程继承 `VR_INSIGHT_SETTINGS` 导致无限派生孙进程（进程爆炸）→ 注入 `VR_INSIGHT_MCP_CHILD=1` 切断链路；② server 自引用/互指时 `tools/list` 无限套娃 → `discover_tools` 加进程级可重入守卫。

### ⏳ PARTIAL（框架已建，部分 kind 未实现）

| 项 | 已完成 | 缺口 |
|---|---|---|
| **registry 多 kind 适配器**（EXT-1/2/3，原 OPEN-3） | 适配器骨架 `base.py`（`Adapter` 契约 + `dispatch()` + `NormalizedResult`）+ `builtin`（`kind=tool`）+ `mcp`（`kind=mcp`，outbound）；**EXT-6 结果归一化已由 `NormalizedResult` 完成**；`script_cmd.py` 按 kind 分派已上线 | **`binary`（EXT-2）与 `python_pkg`（EXT-3）两个适配器未实现**；`dispatch` 对未注册 kind 返回 `exit_code=2` 拒绝执行（安全默认） |

### ⬜ OPEN（剩余项，共 5 项）

| 项 | 内容 | 现状证据 | 依赖 |
|---|---|---|---|
| **OPEN-A `binary` / `python_pkg` 适配器**（EXT-2/3） | 让 registry 能托管外部二进制与第三方 Python 包入口；复用已建 `Adapter` 骨架，属低风险增量 | `adapters/__init__.py` 仅 `register(BuiltinAdapter())` + `register(McpAdapter())` | 独立 |
| **OPEN-B EXT-5 退出码归一化** | 内置脚本当前**直接透传原始 returncode**（`builtin.py:52` `exit_code=code`），未按「0/1/2/3」语义映射 | `NormalizedResult` 已定义 0 成功 / 1 工具判问题 / 2 参数或环境错 / 3 内部异常，但 builtin 通道未套用 | 独立小改 |
| **OPEN-C LLM-5 流式输出 / token 用量统计** | 超时重试 + 熔断**已落地**；流式与用量未做 | `openai_compat.py` 仅一次性 `chat`，grep `stream\|usage` 零命中 | 独立 |
| **OPEN-D P2-2 移动端抽屉**（能力挂接已于 2026-09-12 补齐为 DONE-12） | ~~能力缺口~~ 已补齐：`/api/settings`、`/api/git/groups/{add,remove}`、`/api/batch`、`/api/mcp-out/{servers,tools,call}`、`/api/scripts/{doctor,manual,baseline,baseline-diff}`、`/api/audit/run` 增 `with_llm/llm_model/llm_no_tools`；UI 四视图增强（设置/仓库组/脚本管理/LLM 审计） | `verify_web_gap.py` **9/9**；`sweep` 未挂（目录遍历面大、开发期工具，有意不挂） | 仅剩移动端抽屉（纯样式） |
| **OPEN-E TD-1 `cli.py` 按域拆分** | `cli.py` 非空行约 1,482 行，是包内最大单体；本轮重构又追加了 `mcp` / `mcp-out` / `--with-llm` 三组接线 | 规模量级：约为次大文件 `script_cmd.py`(357) 的 4 倍 | 独立（纯结构重排） |

> **已消解项**：OPEN-2 的阻塞（缺 V1 设计源文件）已由你提供源文件而解除，并已落地为 DONE-7。

---

## 3. 合并后的统一路线图（P0 → P2）

排序原则（沿用审计 §6）：先做解耦与地基，再做复用度最高的功能，最后做体验增强。

### P0 · 地基（并发正确性 + 配置可注入）—— ✅ 全部完成

| 序 | 事项 | 状态 | 交付 |
|---|---|---|---|
| P0-1 | 配置/密钥外部化 | ✅ **DONE-4** | `settings.py` + `ToolConfig` + `SETTINGS.md`；6/6 PASS |
| P0-2 | 并发去竞态（TD-4） | ✅ **DONE-3**（锁已加） | 可选增强：把 `self.state` 改为「每次请求局部快照、不共享」以彻底消除竞态 |
| P0-3 | analyzer 请求级缓存（TD-3） | ✅ **DONE-5** | memo + TTL 桶 + 显式失效；5/5 PASS；并由 **DONE-6** 补齐 MCP 写路径一致性 |

### P1 · 三主线并行 —— ✅ 主线完成

| 序 | 事项 | 归属 | 状态 |
|---|---|---|---|
| P1-1 | UI 外置静态资源 + 侧边栏导航骨架 | 需求一 | ✅ **DONE-7**（9/9） |
| P1-2 | registry 多 kind 适配器 | 需求三 EXT-1/2/3 | ⏳ **PARTIAL**：骨架 + `builtin` + `mcp` 已完成；`binary`/`python_pkg` = OPEN-A |
| P1-3 | LLM 单/多模型客户端（LLM-1/2） | 需求二 | ✅ **DONE-8**（6/6） |
| P1-4 | LLM 工具调用回环（LLM-4） | 需求二×三 交汇 | ✅ **DONE-9**（直接复用 DONE-1 的 inbound MCP，同一 catalog、同一 confirm 门控） |

### P2 · 合流与收尾 —— 主体完成，收尾剩余

| 序 | 事项 | 归属 | 状态 |
|---|---|---|---|
| P2-1 | outbound MCP 适配器（EXT-4） | 需求二×三 公共底座 | ✅ **DONE-10**（8/8） |
| P2-2 | 侧边栏视图全量迁移 + 移动端抽屉 + 图标统一 | 需求一 | ⬜ **OPEN-D** |
| P2-3 | 流式/重试/用量（LLM-5）、退出码归一化（EXT-5）、`cli.py` 按域拆分（TD-1） | 横向 | ⬜ **OPEN-B / OPEN-C / OPEN-E**（重试+熔断已含于 DONE-8；EXT-6 归一化已含于 DONE-10） |

**一句话路线（已走完前两段）**：
`P0 地基 ✅ → P1 三主线 ✅（UI / LLM 客户端 + 工具回环；多 kind 部分） → P2 outbound MCP 合流 ✅`，当前处于 **P2 收尾 + 横向技术债清理**阶段。

---

## 4. 安全红线（审计 §3.4 / §7，已写入合并约束）

凡引入 LLM / 外部工具 / MCP，必须守住以下红线：

1. **密钥只从环境变量读**，严禁入库、不入 `registry.json`、不回显（`get_secret()` 只走 `VR_INSIGHT_*` env；`settings.json` 仅允许 `key_env`/`base_url_env` 指向；provider `__repr__` 恒定 `***set***`）。
2. **LLM/外部工具发起的写操作必须复用既有 confirm 门控**：`run_script_api` 限定「仅 `active` 且 `kind∈{tool,mcp}`、`confirm=true` 才真跑、`argv` 限长 40、仅 `127.0.0.1`」——模型通道不得绕过。
3. **默认只读**：git push 等写操作保持 dry-run→confirm 两段式；LLM 回环默认 `allow_confirmed_writes=False`。
4. **「接了 LLM / MCP」不得成为绕过 `127.0.0.1` + confirm 边界的理由。**
5. **命令只来自配置白名单**：outbound 的 server 定义仅取自 `settings.mcp_servers`，CLI 不接受任意 command；spawn 用 list，绝无 `shell=True`。

> 红线符合性已验证：archived 脚本→`not_runnable_via_api`、active 脚本 `confirm=false`→`dry_run=True`、语义 findings **只加严不放行**（blocking 计数 `1 → 1（咨询）→ 2（显式放行才纳入）`）。

---

## 5. 下一步建议（按价值/风险排序）

已进入收尾与清理阶段，剩余项均为**低风险增量**，可复用已建骨架：

1. **OPEN-A `binary` / `python_pkg` 适配器**（推荐优先）——`Adapter` 契约、`dispatch()`、`NormalizedResult` 均已就绪，新增两个适配器各约 50–80 行，是把 registry 多 kind 从 PARTIAL 收口为 DONE 的最短路径。
2. **OPEN-B EXT-5 退出码归一化**——把 `builtin.py` 的原始 returncode 套进 `NormalizedResult` 的 0/1/2/3 语义，独立小改。
3. **OPEN-C LLM 流式 + 用量**——可选；当前一次性 `chat` 对审计场景已够用，仅在需要交互体验时再做。
4. **OPEN-D 侧栏收尾 + 移动端抽屉**——体验增强，非阻塞。
5. **OPEN-E `cli.py` 按域拆分**——纯结构重排（1,482 行 → 建议按 serve/audit/mcp/script/analyze 分域），风险在于改动面大，建议放在最后、且必须保留 8 套验证全绿。

> 口径说明：本文档为**计划层总纲**；任一阶段动手前仍按你「先方案、确认后再改码」的约定执行（P0-1 即已先出方案再落地）。

---

## 6. 验证台账（实跑证据，8 套全部 exit 0）

| 验证脚本 | 覆盖 | 用例 | 结果 | 证据 |
|---|---|---|---|---|
| `verify_mcp.py` | inbound MCP stdio（initialize/tools/list/tools/call + 两条安全红线） | 6 | ✅ 6/6 | `out/mcp_verify.json` |
| `verify_mcp_http.py` | `/mcp` HTTP 分支（进程内起服务 + urllib POST） | 4 | ✅ 4/4 | `out/mcp_verify_http.json` |
| `verify_cache.py` | 请求级缓存（命中 / 显式失效 / `--no-cache`） | 5 | ✅ 5/5 | `out/cache_verify.json` |
| `verify_mcp_cache_hook.py` | MCP 写工具失效钩子（共享复用 / confirm 失效 / dry-run 不失效） | 3 | ✅ 3/3 | `out/mcp_cache_hook_verify.json` |
| `verify_settings.py` | 配置外部化（等价旧行为 / 注入生效 / CLI 覆盖 / 密钥隔离 / render 集成） | 6 | ✅ 6/6 | `out/settings_verify.json` |
| `verify_ui.py` | P1a UI（静态可服务 / `node --check` / 壳层骨架 / 目录穿越防护） | 9 | ✅ 9/9 | `out/ui_verify.json` |
| `verify_llm.py` | LLM（降级 / 回灌 / 只加严 / 回环 / 熔断 / 脱敏） | 6 | ✅ 6/6 | `out/llm_verify.json` |
| `verify_mcp_outbound.py` | outbound MCP（零行为变化 / 发现 / 门控 / 白名单 / HTTP / kind 分派） | 8 | ✅ 8/8 | `out/mcp_outbound_verify.json` |
| **合计** | | **47** | **✅ 47/47** | |

> 每轮改动后均重跑全量回归，确认零回归。

---

## 7. 代码规模（量级参考）

| 文件 | 非空行 | 本轮变化 |
|---|---|---|
| `vr_insight/cli.py` | ~1,482 | 增 `mcp` / `mcp-out` / `--with-llm` 接线 + `_analyze` memo 包裹；**TODO TD-1 待拆分** |
| `vr_insight/server.py` | ~539 | `_UI_PAGE` 整页内联 HTML 外置为静态资源，显著瘦身；增 `/static` 与 `/mcp` 分支 |
| `vr_insight/mcp/catalog.py` | ~446 | 新增（inbound catalog + 外部工具接入 + 共享分析器 + 失效钩子） |
| `vr_insight/scriptlib/adapters/mcp.py` | ~255 | 新增（outbound 客户端：stdio + HTTP） |
| `vr_insight/llm/runner.py` | ~241 | 新增（语义审计编排 + 统一降级） |
| `vr_insight/llm/providers/openai_compat.py` | ~126 | 新增（urllib 零依赖 + 重试 + 熔断） |
| `vr_insight/settings.py` | ~85 | 新增（配置加载器 + 密钥隔离） |

> 规模含义：新增能力集中在 `mcp/`（inbound）、`llm/`（LLM）、`scriptlib/adapters/`（outbound）三个**彼此解耦的新包**；对既有单体仅做接线式修改，未做侵入式重写。唯一遗留的结构债是 `cli.py` 持续膨胀（OPEN-E）。
