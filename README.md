# RepoLucent

> 通用 Python 仓库架构洞察工具 —— 本地私有化代码库事实源：**分析 / 门禁 / 审计 / MCP 按需供给**。
> 纯 Python 标准库，零第三方运行时依赖，离线可用，产物确定性可 diff。

**English (one-page)**: RepoLucent turns any Python repository into a deterministic,
structured *fact source* (`repo_lucent.json` + Markdown/HTML/AI-context/AGENTS.md/symbols),
with architecture gates, an audit engine, a local web console, and an MCP server
(stdio + HTTP) so AI agents can query context on demand. Pure standard library,
no runtime dependencies, fully offline.

---

## 快速开始

```bash
# 方式一：直接运行（推荐；零安装）
python repolucent.py --repo <仓库根目录>

# 方式二：pip 安装后获得 repolucent 命令（仍零运行时依赖）
pip install -e .
repolucent --repo <仓库根目录> --summary-only

# 无子命令即全量分析；自动定位仓库（脚本位于 <repo>/tools/... 时）
python repolucent.py --summary-only
```

分析产物写入 `<out>/<仓库名>/<日期>/`：`repo_lucent.json`（唯一事实源）、
`repo_lucent_report.md|html`、`AI_CONTEXT.md`、`repo_lucent_symbols.json`（符号倒排索引）、
`AGENTS.md`（Agent 原生约束文件，默认只落产物目录）。
`--only json,md,html,ai,agents,symbols` 可裁剪产物集合。

## 子命令一览

| 子命令 | 用途 |
|---|---|
| （默认） | 全量分析并落盘报告；`--fail-on` 挂门禁 |
| `serve` | 本地可视化控制台（浏览器 UI + Agent HTTP API，默认 127.0.0.1:8788） |
| `context` | 面向 Agent 的上下文切片（`--for plugin:x / module:x`，`--depth brief/normal/full`） |
| `query` | 受限结构化查询（`--from/--select/--where`，替代手工 grep 大 JSON） |
| `search` | 符号定位（类/函数/路由 → `file:line`，替代全仓 grep；rc 对齐 grep） |
| `snapshot` / `diff` | 基线快照与对比（"变了什么"） |
| `gate` | 架构门禁（manifest / 边界 / 路由前缀 / 超大文件） |
| `audit` | 代码审计：多维规则 → 统一 Finding → 放行裁决（可叠加 LLM 语义通道） |
| `log` / `remote-diff` / `untracked` | Git 提交历史 / 远程差异 / 未跟踪文件（只读） |
| `push` / `pull` / `sync` / `group` / `batch` | Git 工作流扩展（confirm 门控；随 gitflow pack 提供） |
| `script` | 脚本资产库：编目 / 受控运行 / 自检 / 基线 |
| `repos` | 多仓库注册表（按仓绑定分析 profile） |
| `mcp` | 以 stdio MCP server 运行，供 Agent 直接调用 |
| `mcp-out` | outbound MCP 客户端（消费外部 MCP Server） |

## AGENTS.md（Agent 原生上下文）

主流编码 Agent（Codex / Cursor / Jules / Claude Code / Copilot / Devin）会在仓库根读取
`AGENTS.md`。RepoLucent 可把既有分析事实重排为该文件，三态可选：

| 模式 | 行为 | 适用 |
|---|---|---|
| `workspace`（默认） | 写 `<out>/<repo>/<date>/AGENTS.md`，**绝不触碰仓库根** | 零意外 |
| `repo` | 写仓库根 `AGENTS.md`：已有标记块则块内替换（块外手写内容一字不动），无标记块则追加 | 团队统一上下文，建议 commit 共享 |
| `off` | 不生成 | 不需要 |

开关优先级：CLI `--agents-md` > 环境变量 `REPO_LUCENT_AGENTS_MD` > `settings.output.agents_md`
> 默认 `workspace`。生成幂等（连跑两次逐字节一致）；标记块匹配与品牌解耦，
更名后旧块仍可被识别更新。

## 产物契约（JSON Schema）

`repo_lucent.json` 的字段契约见 `repo_lucent/schemas/repo_lucent.schema.json`，配套纯标准库
轻校验器（不依赖 jsonschema，支持 `type`/`required`/`properties`/`items`/`enum`/本地 `$ref`）：

```bash
python -m repo_lucent.schema_check <out>/<repo>/<date>/repo_lucent.json
```

`SCHEMA_VERSION` 走 MAJOR.MINOR：MAJOR 破坏产物契约；MINOR 只增可选字段，
消费方必须忽略未识别字段（当前 1.3）。

## MCP 工具（按需供给）

`repo.summary` / `repo.context` / `repo.query` / `repo.search` / `repo.gate` / `repo.audit`
及 `repo.git.*` 全部只读，且与 CLI **共用同一执行面**（v1.6.0 起 `context`/`query`/`search`
不再各写一套实现）。`repo.search` 由 AST 符号倒排索引支撑，返回
`file / line / kind / owner` 事实，不产出建议。

## 安全模型（本地控制台）

`repolucent serve` 默认只绑定 `127.0.0.1`，并实施四层递进防护（v1.5.1 起）：

1. **L1 Host 白名单** —— 请求 Host 头必须属于 `127.0.0.1 / localhost / ::1`，否则 403（封死 DNS rebinding）；
2. **L2 Origin 校验** —— POST 带 Origin 头时必须为本地来源，否则 403；
3. **L3 Token 鉴权** —— `/api/*` 与 `/mcp` 需要令牌（`Authorization: Bearer <t>` 或 `X-RepoLucent-Token: <t>`）；
   启动时自动生成并打印，或用环境变量 `REPOLUCENT_TOKEN` 固定（MCP 客户端复用）；
   显式设 `REPOLUCENT_TOKEN=""` 可退回无鉴权模式（仅本机自担风险，启动横幅红色警告）；
4. **最小豁免** —— 仅 `GET /`（token 注入页面 JS 上下文）与 `/static/*`（目录穿越防护保留）免令牌。

MCP HTTP 客户端配置示例：

```json
{ "mcpServers": { "repolucent": {
    "transport": "http",
    "url": "http://127.0.0.1:8788/mcp",
    "headers": { "X-RepoLucent-Token": "<启动横幅打印的令牌或 REPOLUCENT_TOKEN>" }
} } }
```

stdio 通道（`repolucent mcp`）不经过 HTTP，无需令牌。

## 配置

配置外部化：`repo_lucent/settings.default.json`（包内默认）→ `~/.repolucent/settings.json`（用户级）
→ `<工具根>/settings.json`（项目级）→ `REPOLUCENT_SETTINGS` 环境变量（显式指定）。
密钥（LLM api_key 等）**只**走环境变量（`key_env` / `base_url_env` 指向），绝不落盘。
详见 [SETTINGS.md](SETTINGS.md)。

### 能力分层与执行边界（v1.8.0）

内核（`repo_lucent/*.py` + `registry.core.json`）只保留**通用只读分析**能力；
「写能力」与「业务探针」收敛到 `repo_lucent/packs/<name>/` 两个明确目录——
安全审计面由「整个包」收敛到「两个目录」，按场景分发的体量也随之变小。

| pack | 内容 | 承载能力 |
|---|---|---|
| `gitflow` | `push` / `pull` / `sync` / `group` / `batch` | **写远程** |
| `verorun` | `store_probe` / `ssh_readonly_probe` | VeroRun 业务探针 |

`settings.packs.enabled`：`null`（缺省）= 按 profile 默认（`verorun` 全启用，
故既有行为零变化）；`[]` = **纯只读内核**；`["gitflow"]` = 仅启用列出者。
未启用的 pack，其 CLI 子命令不注册、脚本条目在 `script list` 与 MCP 清单中一并消失。

脚本资产库按 **kind** 分派到四种适配器，新增 kind 必须显式注册，未注册一律拒绝：

| kind | 执行形态 | 安全依据 |
|---|---|---|
| `builtin` | 进程内 `importlib` 调 `main(argv)` | entry 前缀白名单（`scriptlib` / `packs`） |
| `mcp` | outbound JSON-RPC 调外部 MCP Server | `settings.mcp_servers` 白名单 |
| `binary` | 子进程执行**注册表声明**的可执行程序 | 命令只来自注册表；list 形式、`shell=False` 硬编码 |
| `python_pkg` | 子进程 `python -m <第三方包>` | 模块名严格校验（挡选项 / 路径注入） |

> 执行门控与 kind 白名单由 `script_cmd.run_script_api` 统一施加，**不随适配器注册而放宽**：
> 经 serve 执行仍只允许 `tool` / `mcp`，其余 kind 返回 `not_runnable_via_api`（fail-closed）。
> 退出码统一归一为契约 **0 成功 / 1 工具判问题 / 2 参数或环境错 / 3 内部异常**，
> 契约外原值留存于 `meta.raw_exit_code`。

## 性能与加速（2-A / 2-B / 3-B / 热点增量）

四条加速路径，**均不改变任何分析结论**——它们的共同设计原则是「加速层可牺牲，
事实源不可损」：任一步失败一律回退原路径，且一致性由回归套件逐字段断言。

- **并行 AST 解析**（2-A）：`ProcessPoolExecutor`；文件数 < 200 或 `--workers 1` 时自动
  串行；并行不可用（受限环境 / 子进程被禁）时静默降级串行。
- **内容寻址缓存**（2-B）：签名（`mtime_ns:size`）未变即命中，**零哈希、零读盘**；
  签名失真（git checkout / 拷贝 / touch）时回退内容哈希比对，内容相同仍判命中。
- **查询加速层**（3-B，`index.db`）：`query` 原先必须先跑完整分析才能查询；库新鲜时
  直接从 SQLite 取行 → 毫秒级。取回的是**原始行 payload**，再交给 `query.run_query`
  **同一段**投影 / 过滤代码，故加速路径与全量路径结果必然一致。
  指纹 = 仓库文件 `(relpath, mtime_ns, size)` 集合的 sha1（纯 stat）；缺库 / 过期 /
  损坏 → 自动回退全量。`query --rebuild-index` 可强制重建，`--no-cache` 不读也不写。
- **热点增量缓存**（v1.8.0）：`git log --numstat` 是大仓库上最大的单项开销。
  两级加速——① **HEAD 短路**：缓存记录上次 HEAD 与窗口下界，HEAD 未变则 churn 必然
  不变 → **零 git 调用**；② **祖先增量合并**：旧 sha 仍是新 HEAD 祖先时，只取
  `old..new` 新增并对「滑出 N 天窗口」的区间做减法（`new = old − 滑出 + 新增`，
  数学上与全量等价）。rebase / force-push 导致非祖先 → 自动回退全量。

配置：`settings.analysis.workers`（`0`=auto=`min(CPU,4)`，`1`=串行）或 CLI `--workers N`。

实测（VeroRun 仓库：765 个 `.py` / 27.8 万行 / 8 核）：

| 场景 | 实测 |
|---|---|
| AST 解析 765 文件 | `--workers 1` 14.5~23.2s（抖动大）→ `--workers 4` **5.7s** → `--workers 8` 11.3s |
| 分析段 | 冷跑 10.1s → 热跑（缓存全命中）**1.0s** |
| 增量（改 1 个文件内容） | 重解析 **1/765** |
| 仅 touch（内容不变） | 重解析 **0/765** —— mtime 失真不再触发重解析 |
| git 热点段（1733 个有 churn 的文件） | 冷跑 **40.4s** → 热跑 **6.1s**（`head_hit`，零 churn 重算） |

> 口径说明：CLI 完成行中的「耗时」是**分析段耗时**（截至 `standards` 阶段），
> **不含** `render_tree` / 前端仓 / **git 热点** / 符号索引 / 产物落盘。
> 该仓上 git 热点分析原先约占 10s 且每次全量重算，是墙钟时间的主要构成；
> v1.8.0 起由热点增量缓存消解（见上表）。
>
> 缓存态字段**刻意不写入任何产物**（含热点段的 `source`）——冷跑与热跑该值必然不同，
> 进 `repo_lucent.json` 会破坏 `--deterministic` 的可复现性与 golden 层。

## 测试与回归

四层结构（阶段二 2-C 起）。**集成层保持零第三方依赖**，可离线跑；测试金字塔需 pytest。

```bash
python _run_regress.py        # 集成层：23 套 verify_* 回归套件（零依赖）
pip install -e .[test]        # 可选依赖：pytest（仅测试期需要）
pytest tests/                 # 金字塔全量（85 用例：单元 + golden + 性能门禁）
pytest tests/unit -q          # 仅单元层（秒级）
pytest --update-golden        # 显式更新 golden 快照（产物变化随之进入 PR diff）
```

| 层 | 位置 | 用例数 | 作用 |
|---|---|---|---|
| 单元层 | `tests/unit/` | 72 | `py_ast` / `cache` 边界用例：BOM、GBK、CRLF、缩进上限、缓存三态判定、worker 契约 |
| golden 层 | `tests/golden/` | 8 | fixture 仓库 `--deterministic` 五件产物逐字节快照——产物契约的持续验证 |
| 性能门禁 | `tests/perf/` | 5 | fixture 宽松耗时预算 + 结构性门禁（小批量**不得**启动进程池） |
| 集成层 | `verify_*.py` | 23 套 | 端到端能力回归，统一入口 `_run_regress.py` |

> 集成层中 `verify_hotspot`（21 例，各建一个合成 git 仓库）耗时最长，在共享盘上约
> 6–7 分钟，是跑批的主要墙钟构成。

真实仓库性能**只做人工采集、不进 CI 硬门禁**（共享盘/网络盘抖动可达数倍，精细阈值必然误报）：

```bash
REPOLUCENT_PERF_REPO=F:\Sites\VeroRun pytest tests/perf -s
```

## 许可证

VeroRun 团队内部使用许可，见 [LICENSE](LICENSE)。
