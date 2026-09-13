# Verorun Dev Insight · 配置外部化（settings.json）

> 对应设计文档 §4.1（P0 横切地基）。纯增量、向后兼容：不放置任何 settings 文件时，行为与本工具旧版本完全一致。

## 1. 设计原则

1. **向后兼容（最高优先）**：无 `settings.json` 时，行为与旧版完全一致。
2. **优先级链（低 → 高）**：包内默认 < 用户级 `~/.repolucent/settings.json` < 项目级 `<tool>/settings.json` < 环境变量 `REPO_LENS_SETTINGS` 指向的文件。
3. **密钥隔离**：`api_key` / `base_url` 等敏感项**只**从环境变量 `REPO_LENS_*` 读取；`settings.json` 只承载非敏感配置。
4. **兼容契约**：`settings` 字段均为可选、MINOR 级；消费方忽略未知字段（沿用 `__init__.py` 的 `SCHEMA_VERSION`）。
5. **零第三方依赖**：仅标准库（`json` / `os` / `pathlib`）。

## 2. settings.json 搜索链

按下列顺序加载，**后者覆盖前者**（同一 key 以最后出现的文件为准）：

| 优先级 | 路径 | 说明 |
|---|---|---|
| 1（最低） | `<pkg>/settings.default.json` | 随包发布的内置默认，作为兜底 |
| 2 | `~/.repolens/settings.json`（旧 `~/.repolucent/` 兼容回落） | 用户级全局配置 |
| 3 | `<tool 项目根>/settings.json` | 项目级配置 |
| 4（最高） | `REPO_LENS_SETTINGS` 环境变量指向的文件 | 显式指定，CI/容器常用 |

任一文件缺失或 JSON 损坏都会被静默忽略（降级为更低的优先级 / 空 dict），不影响工具运行。

## 3. 可配置字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mcp_enabled` | bool | `true` | 是否启用 MCP 服务（`/mcp` HTTP 分支 + stdio 入口均受控；默认开启） |
| `mcp_outbound_enabled` | bool | `true` | outbound MCP 总开关（本工具作为客户端消费外部 Server，EXT-4） |
| `mcp_servers` | list[dict] | `[]` | **外部 MCP Server 白名单**（outbound），每项见 §3.1 |
| `llm_enabled` | bool | `false` | LLM 集成总开关（P2 使用；配置 `models` 时自动视为启用） |
| `default_model` | str | `null` | 默认模型名（P2 使用） |
| `models` | list[dict] | `[]` | 多模型元数据（P2 使用），每项：`{name, provider, model, key_env, base_url_env}` |
| `max_plugins_in_ai_context` | int | `60` | AI 上下文插件表行数上限（原 `RepoConfig` 内置默认，现可外部化） |
| `max_tree_depth` | int | `2` | 目录树展示深度（CLI `--tree-depth` 仍优先） |
| `output` | dict | `{"date_dir": true, "date_format": "%Y-%m-%d", "agents_md": "workspace"}` | 产物目录与 AGENTS.md 输出策略，见 §3.2 / §3.3 |
| `analysis` | dict | `{"workers": 0}` | AST 并行解析策略（v1.7.0，阶段二 2-A），见 §3.4 |
| `packs` | dict | `{"enabled": null}` | pack 分层启用策略（v1.8.0，阶段三 3-A），见 §3.5 |
| `analysis.max_file_lines` | int | `2000` | 规则 CMP001 单文件代码行上限（v2.0.0，阶段 F）；CLI `--max-file-lines` 覆盖 |
| `analysis.max_func_lines` | int | `120` | 规则 CMP002 单函数体行数上限（v2.0.0，阶段 F）；`RepoConfig` 默认值 |

> `CLI args > settings.json > 内置默认`。例如 `repolens.py --tree-depth 3` 会覆盖 settings 中的 `max_tree_depth`。

### 3.1 外部 MCP Server（outbound，EXT-4）

本工具作为**客户端**去消费外部 MCP Server（与 `mcp` 子命令的 inbound 方向相反）。
白名单来自 `mcp_servers`，**不接受命令行传入任意 command**。

```json
{
  "mcp_outbound_enabled": true,
  "mcp_servers": [
    { "name": "demo", "transport": "stdio",
      "command": "python",
      "args": ["-m", "some_mcp_server"],
      "env": { "PYTHONPATH": "/path/to/pkg" },
      "timeout_s": 60 },
    { "name": "remote", "transport": "http",
      "url": "http://127.0.0.1:8788/mcp",
      "timeout_s": 30 }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `name` | 唯一标识；外部工具在 MCP 清单里命名为 `mcp.<name>.<tool>` |
| `transport` | `stdio`（派生子进程，复用 `JsonRpcProc`）或 `http`（`urllib` POST，零依赖） |
| `command`/`args`/`env` | 仅 `stdio` 需要；以 list 形式 spawn，**绝不使用 shell** |
| `url`/`headers` | 仅 `http` 需要 |
| `timeout_s` | 每次 JSON-RPC 往返超时（默认 30） |
| `enabled` | 置 `false` 可临时停用某 server（默认 true） |

**安全红线**

- 未配置任何 server 时，工具清单与改造前完全一致（8 核心 + 活跃脚本），零行为变化。
- 调用外部工具与 `script.*` 同门控：`confirm=false` 只返回 dry-run 预览，**绝不自动放行**。
- 出站子进程会带 `REPO_LENS_MCP_CHILD=1` 标记，子进程不再做出站发现 —— 防止
  “发现→派生→再发现”的进程爆炸（server 配置自引用时尤其关键）。
- 工具发现带进程级可重入守卫，阻断嵌套/互指 server 造成的无限递归。

**CLI 探针**

```bash
repolens.py mcp-out servers                       # 列出已配置的外部 Server
repolens.py mcp-out tools [--server NAME]         # 发现外部工具
repolens.py mcp-out call <server> <tool> --arg k=v --confirm
```

### 3.2 产物按日期归档（output，DONE-15）

默认产物目录结构：`out/<仓库名>/<YYYY-MM-DD>/`（多仓库切换时按仓库名分根，再套日期层）。

```json
{ "output": { "date_dir": true, "date_format": "%Y-%m-%d" } }
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `date_dir` | `true` | 置 `false` 则产物直接写入 `--out` 目录（归档前行为） |
| `date_format` | `"%Y-%m-%d"` | `strftime` 格式；非法格式自动回退默认 |

优先级：**CLI `--no-date-dir`（最高，单次关闭） > 环境变量 `REPO_LENS_DATE_DIR=0|1` > `settings.output.date_dir`**。

关键设计：只有**产物**下沉到日期目录；**AST 缓存（`.insight_cache`）与基线快照（`history/`）留在稳定根**
（`RepoConfig.stable_out_dir`）。否则每天新建目录会导致每天首次分析退化为全量解析，且基线无法跨日 diff。
稳定根写 `_latest.txt` 指针（第一行目录名、第二行绝对路径）供脚本/UI 定位最近一次产物。

- 端点：`GET /api/artifacts` → `{artifact_root, current, latest, runs[]}`；`/api/state` 增 `artifact_root` 字段
- UI：设置视图「产物归档（按日期）」卡片列出历史日期目录与产物文件
- 无新增子命令；`--no-date-dir` 为唯一 CLI 开关

### 3.3 AGENTS.md 输出模式（output.agents_md，v1.6.0）

`AGENTS.md` 必须写在**仓库根**才会被主流编码 Agent（Codex / Cursor / Jules /
Claude Code / Copilot / Devin）发现，而这会改变 `git status`。因此改为三态显式选择：

```json
{ "output": { "agents_md": "workspace" } }
```

| 值 | 行为 | 适用 |
|---|---|---|
| `workspace`（默认） | 写 `<out>/<仓库>/<日期>/AGENTS.md`，随日期归档，**绝不触碰仓库根** | 零意外；Agent 手动引用 |
| `repo` | 写仓库根 `AGENTS.md`：已存在标记块则**块内替换**（块外用户手写内容一字不动）；无标记块则追加到文末；文件不存在则新建 | 团队统一上下文；建议 commit 进仓库让所有 Agent 共享 |
| `off` | 不生成 | 不需要 |

优先级：**CLI `--agents-md repo\|workspace\|off`（最高） > 环境变量
`REPO_LENS_AGENTS_MD` > `settings.output.agents_md` > 内置默认 `workspace`**。
非法值一律回落 `workspace`，不会因配置写错而中断分析。

设计要点：

- **幂等**：块内正文不含时间戳 / 耗时 / 绝对路径，同一仓库连跑两次产物逐字节一致，
  因此 `repo` 模式的写入可以进 Git、可在 review 里 diff。
- **品牌解耦**：标记块**匹配**只用 `:begin auto` / `:end auto` 词形，
  不绑定 `repolens` 字样——v2.0.0 更名后，旧仓库里既有的块仍能被识别并原地更新。
- **`--only` 联动**：需要 `--only` 含 `agents` 令牌（默认含）。`--only json` 等旧用法
  不会产生任何 AGENTS.md，行为与升级前一致。
- **显式提示**：`repo` 模式写仓库根时，启动输出会在 stderr 打印一次提醒
  （即使 `--quiet` 也不静默），避免"莫名多出一个未跟踪文件"。

### 3.4 AST 并行解析（analysis.workers，v1.7.0）

AST 解析是全量分析中最昂贵的一步，改为进程池并行（阶段二 2-A）。

```json
{ "analysis": { "workers": 0 } }
```

| 值 | 行为 |
|---|---|
| `0`（默认） | auto：`min(cpu_count, 4)`。上限刻意压到 4——实测 8 核机器上 `workers=8` 反而比 `workers=4` 慢一倍（进程创建与调度开销盖过收益） |
| `1` | 串行。既是兼容开关（用于对照排查），也是并行不可用时的兜底路径 |
| `N > 1` | 显式进程数 |
| 负数 / 非法值 | 按「最安全解释」处理为串行，绝不因配置写错而中断分析 |

优先级：**CLI `--workers N`（最高） > `settings.analysis.workers` > 内置默认 `0`**。

联动与边界：

- **`--no-cache` 与并行正交**：禁用缓存只影响是否复用 AST 结果，不影响解析是否并行。
- **文件数 < 200 恒串行**（`py_ast.PARALLEL_MIN_FILES`）：小仓库下进程创建开销
  大于收益（实测 363 个微型文件时并行比串行慢约 14%，765 个中等文件时快约 2.6 倍）。
- **自动降级**：进程池不可用（受限环境禁子进程、资源不足、pickle 失败）时静默回退串行。
  「优化可以失效，正确性不可以」——串行与并行的产物经逐字节比对确认完全一致（`tests/unit`）。
- **Windows spawn 前提**：worker 为顶层函数、只接收字符串元组（内容由 worker 自读）、
  入口具备 `if __name__ == "__main__"` 守卫。三者均已满足，勿在重构中破坏。

### 3.5 pack 分层启用（packs.enabled，v1.8.0）

把「写能力」从只读内核剥离为可按场景分发的包（阶段三 3-A）。

```json
{ "packs": { "enabled": ["gitflow"] } }
```

| 值 | 行为 |
|---|---|
| `null`（默认） | 按 profile 默认：`verorun` → `["gitflow","verorun"]`；`generic-python` → `["gitflow"]`；未登记 profile → `["gitflow"]` |
| `[]` | **纯只读内核**：任何 pack 的脚本与子命令都不可用 |
| `["gitflow"]` | 仅启用列出的 pack（未登记的名字被忽略，配置笔误不致运行时报错） |

包构成：

| pack | 内容 | 承载能力 |
|---|---|---|
| `gitflow` | 子命令 `group` / `batch` / `push` / `pull` / `sync` | **写远程**（智能推送、拉取、多远程同步、仓库组批量） |
| `verorun` | 脚本 `store_probe` / `ssh_readonly_probe` | VeroRun 业务探针（数据存储 / SSH 巡检） |

生效范围**恰好两个入口**（语义清晰、不脆）：

1. **CLI 子命令是否注册**——未启用时该命令不进 argparse；直接调用会命中
   「pack disabled」提示并以 rc=2 退出，提示中给出当前生效 pack 与启用方法。
2. **脚本资产库条目是否可见**——未启用 pack 的 `registry.json` 条目不参与聚合，
   故 `script list/run/doctor` 与 MCP 的 `script.*` 工具清单一起收敛。

> 边界说明：pack 的 Python 模块**始终可导入**（导入不产生写副作用），
> "未启用"只作用于上面两个入口。`repo_lens/registry.core.json` 是内核脚本的
> 单一事实源；内核脚本的 `entry` 白名单前缀恒为
> `repo_lens.scriptlib.` / `repo_lens.packs.`（`scriptlib/adapters/base.py`
> 的 `ALLOWED_ENTRY_PREFIXES` 为双源一致性的事实源）。
>
> **兼容保证**：内置 `verorun` profile 默认启用全部 pack，故既有用户行为零变化。

## 4. 密钥处理（强制）

`settings.json` **禁止**出现任何明文密钥（`api_key` / `base_url` 等）。敏感项通过 `key_env` / `base_url_env` 指向环境变量，运行时由 `settings.get_secret(env_name)` 读取：

```json
{
  "models": [
    {
      "name": "qwen-plus",
      "provider": "openai_compat",
      "model": "qwen-plus",
      "key_env": "REPO_LENS_DASHSCOPE_KEY",
      "base_url_env": "REPO_LENS_DASHSCOPE_BASE_URL"
    }
  ],
  "default_model": "qwen-plus"
}
```

对应环境变量（在 shell / 容器 / CI 中注入，**不入库**）：

```bash
export REPO_LENS_DASHSCOPE_KEY="sk-xxxx"
export REPO_LENS_DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

读取通道：`from .settings import get_secret; get_secret(model["key_env"])`。

## 5. 分析口径（profile，档位 A 通用化）

`profile` 段决定工具**如何理解一个仓库**：仓库怎么定位、组件（VeroRun 语义下=插件）怎么发现、核心模块知识库、排除规则、默认门禁集等。把它换成非 VeroRun 预设，即可分析任意 Python 仓库，**不改任何代码**。

硬约束：**不写 `profile` 段（或任何字段为 `null`）时，全部取值回落到 `config.py` 内置 VeroRun 常量，行为与旧版完全一致。**

### 5.1 字段一览

| 字段 | 说明 | `null` 时回落 |
|---|---|---|
| `name` | 展示/日志用名称 | `"verorun"` |
| `repo_signature.dirs` / `.files` | 仓库定位特征：目录/文件必须全部存在，否则 `SystemExit`；两者均空 = 接受任意显式 `--repo` 目录 | `dirs=["plugins","plugin_manager"], files=[]` |
| `component.dir` | 组件根目录；**空字符串 = 无组件概念，短路插件分析** | `"plugins"` |
| `component.manifest` | 清单文件名 | `"plugin.json"` |
| `component.required_fields` | 清单必填字段（schema 缺失时的回退） | 8 个 VeroRun 字段 |
| `core_modules` | 核心目录知识库（目录 → 中文职责），**增量合并**：出现的键覆盖内置，未出现的保留 | 内置 VeroRun 全量字典 |
| `auto_core_exclude` | 自动识别核心目录时的排除集 | `{docs,deploy,scripts,tools}` |
| `exclude_dirs` | 全局目录排除集 | 内置 VeroRun 集合 |
| `root_entry_allowlist` | 根目录入口脚本白名单 | 内置 5 个入口 |
| `key_docs` | 重点文档索引清单 | 内置 9 份文档 |
| `standard_doc_pattern` | 规范文档 glob（相对仓库根）；空字符串 = 不提取 | `"docs/plugin-standard-v*.md"` |
| `frontend_repo_names` | 前端/桌面端仓库自动发现名单；空 = 关闭约定发现，只认 `--extra-repo` | `["verorun-workplace"]` |
| `default_gates` | `--fail-on` 缺省门禁集；`null` = 维持历史行为（不传 `--fail-on` 就不跑门禁） | 不启用 |

### 5.2 非_VeroRun 预设（generic-python）

随包发布于 `profiles/generic-python.json`，复制内容到 `settings.json` 即用：

```json
{
  "profile": {
    "name": "generic-python",
    "repo_signature": { "dirs": [], "files": ["pyproject.toml"] },
    "component": { "dir": "", "manifest": "", "required_fields": [], "id_pattern": "" },
    "core_modules": {},
    "auto_core_exclude": ["docs", "tests", "scripts", "tools", "build", "dist"],
    "exclude_dirs": [".git", ".github", ".pytest_cache", ".mypy_cache", ".idea",
                     ".vscode", "venv", ".venv", "env", "node_modules",
                     "__pycache__", "build", "dist", "htmlcov"],
    "root_entry_allowlist": [],
    "key_docs": ["README.md", "CHANGELOG.md", "AGENTS.md"],
    "standard_doc_pattern": "",
    "frontend_repo_names": [],
    "default_gates": ["file-too-large"]
  }
}
```

### 5.3 档位 A 边界（重要）

- 报告结构不变；非 VeroRun 仓库下 **plugins / boundary / standards / interactions 相关章节为空**——这是预期行为（组件短路），不是故障。
- 仅支持 Python（AST 基于标准库 `ast`）；跨语言支持属档位 B/C。
- `component.id_pattern` 已在 schema 中预留，档位 A 下插件校验规则不逐字段参数化。

## 6. 示例（项目级 settings.json）

```json
{
  "mcp_enabled": true,
  "max_plugins_in_ai_context": 60,
  "max_tree_depth": 2
}
```

仅此即可让 `max_*` 上限随配置调整而无需改动 `config.py` 源码——这正是 §4.1 的验收门：**配置能注入而源码不动**。

## 7. 多仓库注册表 / LLM 可视化配置 / 报告模板（本轮新增）

### 7.1 多仓库注册表（repos）

注册表存于 `~/.repolens/repos.json`：`[{name, path, profile, added_at}]`。

```bash
repolens.py repos add --name verorun-main --path "F:\Sites\VeroRun" --profile verorun
repolens.py repos list            # 同时列出可用 profile 预设
repolens.py repos remove --name verorun-main --confirm
# 任意分析子命令可改用注册项（其 profile 预设同时生效）：
repolens.py analyze --repo-name verorun-main
```

- `profile` 预设解析顺序：`<tool>/profiles/<name>.json` → `~/.repolens/profiles/<name>.json`；`null`/`verorun` = 内置口径。
- Web：`GET /api/repos`（只读）、`POST /api/repos/{add,remove}`（confirm 门控）、`POST /api/repos/switch`（运行中换仓：重建 cfg + 按仓 profile 覆盖 + 失效 `_AZ_REGISTRY`/`_ANALYSIS_MEMO` + 重发现前端仓；输出目录切至 `out/<name>/`）。
- UI：设置视图「多仓库管理」区块（注册/切换/移除）。

### 7.2 LLM 可视化配置

UI 设置视图提供 LLM 表单（开关 / default_model / models[] JSON），经 `POST /api/settings/llm` 原子写回**项目级** `settings.json`（写前自动备份 `.bak`）。

**安全红线**：表单只写 `key_env`/`base_url_env` 环境变量名，密钥本体永不落盘；`GET /api/settings` 的 `env_status` 只回各变量「已设置/未设置」布尔。

### 7.3 报告模板（md，最小版）

`settings.json` 增 `report_template` 段，只对 Markdown 报告做**段的过滤与重排**（数据 schema 不动）：

```json
{ "report_template": { "md": ["overview", "tree", "core", "plugins", "standards"] } }
```

段 id：`overview / tree / frontend / core / plugins / interactions / standards / workflow / deep_dive`。未配置、空列表或全部 id 未命中时输出与旧版逐字节一致；标题块与结尾落款恒保留。HTML/仪表盘暂不参与模板化。

## 8. 与 P2 LLM 的衔接

- `get_secret()` 是 P2 `llm/providers/openai_compat.py` 的密钥读取通道。
- `ToolConfig.models[]` 是 P2 `llm/runner.py` 的多模型配置来源。
- 本阶段只铺设读取通道与默认值注入，不新增 `llm/` 业务代码（P2 负责）。
