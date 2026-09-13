# Changelog

本文件从 v1.5.0 起记录（更早历史见《重构总计划-MASTER-合并审计报告.md》的 DONE/PARTIAL/OPEN 状态矩阵）。
版本语义：SCHEMA_VERSION 走 MAJOR.MINOR——MAJOR 破坏产物契约；MINOR 只增可选字段。
TOOL_VERSION 与产物解耦，任意变更都可递增。

## [2.0.0] - 2026-09-13

阶段 F 收口：规则引擎（`repo_lens/rules/`），完成《repolens升级实施方案 v1.5.1→v2.0.0》的最后一关，工具抵达 2.0.0 里程碑。

### Added
- 规则引擎 `repo_lens/rules/`（6 文件）：`_types.py`（Finding + 共享 AST 工具）、`spec.py` / `security.py` / `architecture.py` / `complexity.py` 四类 15 条规则、`__init__.py`（注册表 + `run_rules` 汇总）。
- 15 条规则：SPEC001-005（manifest/规范）、SEC001-004（硬编码密钥 / eval-exec / subprocess shell=True / SQL 拼接）、ARCH001-004（核心直导插件 / 插件环依赖 / 私有连接池 / 路由前缀）、CMP001-002（单文件行数 / 单函数体行数）。
- 产物新增可选顶层字段 `findings`（schema_version=1.0 / summary / items），由 `_analyze_compute` 收尾写入；`SCHEMA_VERSION` 1.3 → 1.4（MINOR，不破坏契约）。
- `gate.py` 新增门禁项 `findings`：`--fail-on findings` 在存在 error 级 finding 时返回退出码 1（warning/info 不计入，与文档一致）。
- `report_md.py` / `report_html.py` 新增「规则检查结果」章节。
- `verify_rules.py` 新增（合成 data + 合成仓库 + fixture 端到端 + opt-in 真实仓），并入 `_run_regress.py`（共 24 套）。

### Changed
- `config.py` 新增阈值字段 `max_file_lines`（默认 2000）/ `max_func_lines`（默认 120），供 CMP001/CMP002；CLI `--max-file-lines` 可覆盖。

### Verified
- `verify_rules.py` 5/5（CI 路径：合成 data 派生 + AST 命中 + fixture 端到端；真实仓路径 opt-in）。
- 23 套既有回归不受影响（findings 为新键，不影响既有断言）；golden 快照刷新一次。

### Notes
- 规则引擎只读派生，不修改任何 analyzer 产物；对语法错误文件与空仓库健壮（返回空 findings）。
- 全文采用「先方案、确认后写」交付：阶段 F 方案经用户确认（「开工」）后落地。

## [1.8.0] - 2026-09-13

阶段三：工程化收口（依据《repolens升级实施方案 v1.5.1→v2.0.0》§5）。三件事：
让「写能力」离开只读内核（3-A）、让 `query` 不必每次全量分析（3-B）、
并把多 kind 适配器与 LLM 流式两个遗留项收口（3-C）。
**产物契约仍未变**（`SCHEMA_VERSION` 保持 1.3，理由见 `__init__.py` 版本沿革注释）。

### Added
- **3-A pack 分层**（`repo_lens/packs/`）：把「写能力」与「业务探针」从只读内核剥离为
  两个可按场景分发的包。
  - `packs/gitflow/`：`push` / `pull` / `sync` / `group` / `batch` 五个子命令及其实现
    （原 `repo_lens/` 下的同名模块迁入）；`packs/verorun/`：`store_probe` /
    `ssh_readonly_probe` 两个业务探针（原 `repo_lens/scriptlib/` 下迁入）。
  - 启用策略 `settings.packs.enabled`：`null`（缺省）= 按 profile 默认；
    `[]` = **纯只读内核**；`["gitflow"]` = 仅启用列出者。内置 `verorun` profile
    默认启用全部 pack，**既有用户行为零变化**（这是本改造的硬约束）。
  - 生效范围刻意收窄到**两个入口**：CLI 子命令是否注册、脚本资产库条目是否可见
    （故 `script list/run/doctor` 与 MCP 的 `script.*` 清单一起收敛）。pack 模块
    始终可导入——「未启用」不作用于导入层，语义清晰且不脆。
  - 未启用时命令不进 argparse；直接调用命中「pack disabled」提示并 rc=2
    （`cli._PackAwareParser`），提示中给出当前生效 / 可用 pack 与启用方法。
  - `repo_lens/registry.core.json` 成为内核脚本的单一事实源（原 `registry.json`
    归档）；聚合注册表 = 内核 + 各启用 pack 的 `registry.json`。
    `scriptlib/adapters/base.py:ALLOWED_ENTRY_PREFIXES` 扩为
    `("repo_lens.scriptlib.", "repo_lens.packs.")`，作为 entry 白名单的**单一事实源**
    （`script_cmd._ALLOWED_PKGS` 与之由 `verify_packs` 断言一致，防双源分叉）。
- **3-B `query` 的 SQLite 加速层**（`repo_lens/index_db.py`，`index.db` 落 `cfg.cache_dir`）。
  定位是**加速层而非事实源**：`query` 原先必须先跑完整分析才能查询，现在库新鲜时
  直接取行 → 毫秒级。关键设计是「取回**原始行 payload** → 复用
  `query.run_query(rows=...)` 同一段投影 / 过滤代码」，故加速路径与全量分析路径的
  结果**必然逐字段一致**，无需维护两套实现。
  - 表：`meta`（fingerprint / built_at）/ `plugins` / `routes` / `symbols` / `files` /
    `core_modules`，附必要索引。
  - 新鲜度 `repo_fingerprint` = 仓库文件 `(relpath, mtime_ns, size)` 集合的 sha1
    （纯 stat，765 文件约 0.2s）。缺库 / 过期 / 损坏 / scope 不支持 → **一律回退全量分析**。
  - 建库走 `.tmp` + `os.replace` 原子替换，绝不半写。
  - CLI 新增 `query --rebuild-index`（强制全量并重建）；`--no-cache` 不读也不写库。
- **3-C OPEN-A：`binary` / `python_pkg` 两个适配器**（EXT-2 / EXT-3，
  多 kind 由 PARTIAL 收口为 DONE）：
  - `binary`：子进程执行**注册表声明**的可执行程序。命令与固定参数只来自注册表
    （调用方只能选条目、不能选命令）；list 形式 spawn 且 `shell=False` 硬编码，
    故 `$(...)` 等元字符被原样传递；调用方 argv 限长 40；超时强制生效；
    stderr 进 `meta` 而**不混入 stdout**（以免污染 stdout 的 JSON 解析）。
  - `python_pkg`：子进程 `python -m <第三方包>`——**刻意不做进程内 import**，
    因为第三方包顶层导入可能执行任意代码 / 污染 `sys.modules` / 挂死。
    `module` 必须匹配 `^[A-Za-z_][A-Za-z0-9_.]*$`，同时挡住 `-c` / `--foo`
    选项注入与 `../evil`、`a/b` 路径注入；缺包时用只解析**顶层名**的 `find_spec`
    探测（不导入目标包）并给出可操作提示。
  - 二者与 `builtin` / `mcp` 并列注册进 `ADAPTERS`（四种 kind 齐全）；
    未注册 kind 仍返回 `exit_code=2 / unsupported_kind`。
- **3-C OPEN-C：LLM 流式输出与 token 用量**（LLM-5）：
  - `openai_compat` 新增 `chat_stream(...)`，与一次性 `chat` 共用熔断 / 重试 / 密钥脱敏；
    请求体带 `stream=true` 与 `stream_options.include_usage`。
  - 实现刻意拆成「传输」与「折叠」两半：`iter_sse()` / `reduce_stream()` 是**纯函数**，
    可完全脱离网络单测；`reduce_stream` 按 `index` 归并分片到达的
    `tool_calls.arguments`，与一次性路径同构（非法 JSON 退化为 `{"_raw": ...}`）。
  - **重试语义的关键差异**：仅当尚未向外吐出任何增量时才重试——已回调过 `on_delta`
    再重试会造成文本重复，此时直接失败、交 runner 降级。
  - `ChatResult` 新增 `usage`；`Provider` 新增 `supports_stream` 与 `chat_stream`
    **基类兜底**（不支持流式的 provider 自动退化为一次性调用，只回调一次），
    使上层可统一按流式 API 书写。
  - `runner.SemanticRun` 新增 `usage`（跨轮累计）与 `streamed`；新增
    `accumulate_usage()` 纯函数。`run_semantic_audit(..., on_delta=...)` 为
    **显式 opt-in**：缺省仍走一次性 `chat`，默认路径行为不变。

### Changed
- `scriptlib/adapters/builtin.py`：退出码由原始 returncode 透传改为经
  `normalize_exit_code` 收敛到 0/1/2/3（EXT-5）。契约内取值**恒等变换**——实测全部
  内置脚本只返回 0..3，故行为零变化；仅契约外取值（126/127 → 2，负数与其它 → 3）
  被归类，归一前原值留存于 `meta['raw_exit_code']`（语义归一而不丢信息）。
- `ADAPTERS` 由 2 种 kind 扩为 4 种；`adapters/__init__.py` 的 docstring 补 kinds 一览表。
- `TOOL_VERSION` → **1.8.0**（`__init__.py` / `pyproject.toml`）；`SCHEMA_VERSION`
  保持 **1.3**：本版无产物字段增删改。
- `tests/golden/` 五件快照整体刷新（见 Fixed 第 4 条）。

### Fixed
- **`index_db.connect()` 在损坏分支泄漏 sqlite 连接**（本阶段自查发现）。
  `sqlite3.connect` 是惰性握手——即便文件不是库，连接也会建立并持有 OS 句柄，
  随后的探测语句才抛错。原实现该分支直接 `return None` 而不关闭，句柄残留会让
  Windows 锁住 `index.db`，令后续 `build` 的 `os.replace` 恒抛 `WinError 5`。
  **后果是自愈永久失效**：库一旦损坏，`query` 探测（泄漏）→ 回退全量 → 尝试重建
  必然失败，加速层再也无法恢复。已改为显式关闭。
- **`verify_index.py` 自身用 `with sqlite3.connect(...) as c` 读表名**：连接的上下文
  管理器只提交 / 回滚事务、**并不关闭连接**，该诊断连接锁住 `index.db`，使
  「库损坏 → 可重建」用例必然失败。已抽出 `_table_names()` 并在 `finally` 显式
  `close()`。
- **`tests/unit/test_cache.py` 中 `test_resolve_sig_changed_and_content_changed_misses`
  偶发失败**（v1.7.0 2-C 遗留的测试健壮性缺陷）：该用例把 `b"x = 1\n"` 改写为
  `b"x = 2\n"`（**字节数相同**），签名 `mtime_ns:size` 里只有 mtime 能区分二者，
  而它并未强制制造 mtime 差异，实际依赖文件系统时间戳粒度——粒度偏粗时两次写入
  落在同一刻度，判定退化为 `hit/sig`。实测同一套件三次运行分别出现 6 / 5 / 1 个失败。
  已按同文件姊妹用例的既有做法显式 `os.utime` 前移 mtime，并先 `assert
  signature(p) != rec["sig"]` 证伪。连跑 5 次全绿。
- **热点缓存态字段 `source` 曾进入事实源产物**（本阶段自查发现并修）：`analyze_hotspots`
  新增的 `source`（`head_hit` / `incremental` / `full`）被直接并入 `data["hotspots"]`，
  而它是**纯缓存态派生量**——同一输出目录二次运行必然取到不同值，进入 `repo_lens.json`
  即破坏 `--deterministic` 的可复现性（与 v1.7.0 已确立的「2-B 缓存统计刻意不写进任何
  产物」同一原则）。已在 CLI 边界剥离；该字段仍保留在 `analyze_hotspots` 的返回值中，
  供诊断与 `verify_hotspot` 断言。
- **`tests/fixture_repo/plugins/demo/routes.py` 的截断函数体**（v1.7.0 Notes 记录在案的
  既有缺陷）：第 9 行 `def list_items():` 无函数体。补 `return []` 后该文件成为
  **合法 Python**，此前被整体跳过的解析事实回归：路由 **0 → 2**、代码行 **52 → 53**、
  符号数 **7 → 11**。这正是 v1.7.0 Notes 预警的「改变既有 fixture 统计基线」，
  按约定另行确认（用户指令「② 修」）后落地，五件 golden 快照随之整体刷新。
- **流式传输的中途断连未被捕获**：`http.client.IncompleteRead` / `BadStatusLine`
  **不是 `OSError` 子类**，原先会穿透到 runner 之外；`chat_stream` 已显式纳入捕获
  并降级为 `LLMError`。

### Verified
- **`python _run_regress.py` → 23/23 green**：新增 `verify_hotspot`（21 例）、
  `verify_packs`（18 例）、`verify_index`（12 例）、`verify_adapters`（18 例）、
  `verify_llm_stream`（14 例）。
- **`pytest tests/` → 84 passed, 1 skipped**（85 用例；跳过项为 symlink 循环用例，
  需管理员权限才能创建目录符号链接）。
- 3-B 核心一致性断言：7 组 `scope` / `select` / `where` 组合下 **SQL 路径与 JSON 路径
  结果零差异**（`mismatches=[]`）。
- 热点增量加速（真实仓库 `F:\Sites\VeroRun`，1733 个有 churn 的文件）：
  冷跑 **40.4s → 热跑 6.1s**（`source=head_hit`，零 churn 重算）。
- `verify_adapters` 实测：`binary` 的 `$(echo pwned)` 参数被**原样传递**
  （证 `shell=False`）；`python_pkg` 对 `-c` / `--version` / `../evil` / `a/b` /
  `1bad` / `a..b;rm` 六类非法模块名一律拒绝执行。

### Notes（本阶段记录在案，未处理）
- **新增 kind 目前不可经 serve 执行**：`script_cmd.run_script_api` 的门控是 kind
  白名单（仅 `tool` / `mcp`），故 `binary` / `python_pkg` 条目会命中
  `not_runnable_via_api`。这是**刻意的 fail-closed 默认**——适配器注册不自动放宽
  执行面。是否放开、以何形式放开（例如经 `settings` 显式 opt-in 扩白名单）属安全
  边界决策，待确认后再动；`verify_adapters` 已把当前姿态固化为断言。
- **流式只到 provider / runner 层，未接入 serve 的交互式输出**：
  `run_semantic_audit(on_delta=...)` 已是显式 opt-in 通道，但目前没有消费方
  （审计是一次性判定，流式的交互收益有限）。需要时再接线。
- **`verify_hotspot` 是全套中最慢的一个**（本机 F: 共享盘约 6–7 分钟：21 个用例各自
  `git init` + 多次 commit + 多轮 `git log`）。`_run_regress.py` 的 560s/套件上限虽未
  触发，但余量已不大；若再加合成 git 仓用例，需同步上调上限或改为复用单一仓库。
- **`index_db` 的 `files.loc` 恒为 NULL**：`files` 表当前只登记「符号索引出现过的
  文件」，行数远小于全仓文件数，`loc` 也无来源。该表目前仅为将来的按文件查询预留，
  尚无消费方；真正用它之前需补全填充逻辑，否则查 `files` 会得到与 `overview`
  不一致的结果。
- **`index_db` 只支持 `plugins` / `core` 两个 scope**：其余 scope 走原路径（无加速）。
  符号 / 路由 / 文件表已建但未接入查询路径，后续可按需扩展。

### 阶段四（验证与压测增强，2026-09-13 续）
- **`verify_core_probe` 真实分析语义修正**：`real_repo_probe` 用例从「仅 record SKIPPED」
  升级为 **opt-in 真实分析**——设 `REPOLENS_REAL_REPO` 后即真正跑 `_analyze` + `_write_reports`
  并断言（`plugins>0` / `hotspots` 存在 / `meta.duration_ms>0` / `overview` 存在），使 DoD 真实仓
  核验闭合进套件而非靠手动 CLI。未设环境变量时维持 `SKIPPED=True`，CI 全绿（5/5）。
- **新增 `measure_wallclock.py`（阶段四 2a，零产品改动）**：对真实仓做冷/热两遍全流水线计时，
  用 `perf_counter` 包住 `_analyze`（含前端/热点/符号）与 `_write_reports`（落盘），分别报
  `analyze_ms` / `write_ms` / `total_ms`，并并排列出工具自报 `duration_ms` 以暴露口径陷阱。
  方法论关键点：冷/热须**共享 cache_dir** 才能测出热点增量缓存加速（先同目录预热写缓存，
  再隔离目录测冷 `--no-cache`，最后回同目录测热复用暖缓存）。
- **数值化证实 `duration_ms` 口径陷阱**（真实仓 `F:\Sites\VeroRun`，765 .py / 27.8 万行）：
  - 冷跑墙钟 **28635ms**（analyze 26953 + 落盘 1682），工具自报仅 **10867ms**（少报 ~2.6×）。
  - 热跑墙钟 **5348ms**（analyze 4920 + 落盘 428），工具自报仅 **1241ms**。
  - 冷→热加速 **23287ms（5.4×）** —— v1.8.0 热点增量缓存（HEAD 短路 / 祖先增量）生效；
    但热跑 5.3s 仍**未达方案「热<3s」目标**，瓶颈在 analyze 段（git 热点 churn + AST 解析）。
  - 工具自报 `duration_ms` 止于 `standards` 阶段，遗漏前端分析 / git 热点 / 符号索引 / 全部落盘。
- 未改任何产品行为 / 产物契约，`TOOL_VERSION` 不变（仅测试与压测工具增强）。可选项
  (2b) 是否在 `meta` 下新增门控 `stage_timings_ms` 永久修正口径陷阱，递延至后续确认。

## [1.7.0] - 2026-09-13

阶段二：性能与测试结构（依据《repolens升级实施方案 v1.5.1→v2.0.0》§4）。
本阶段只做一件事的两面：让「重复运行」变快（2-A 并行 + 2-B 增量），
并给这份变快加上不依赖人眼的证据（2-C 测试金字塔）。**产物契约未变**
（`SCHEMA_VERSION` 保持 1.3，无字段增删改），故本版为纯 MINOR。

### Added
- **2-A 并行 AST 解析**（`py_ast.py`）：新增顶层 worker `_parse_one()` 与
  `parse_files_parallel()`。批量 ≥ `PARALLEL_MIN_FILES`(200) 且 `workers != 1` 时走
  `ProcessPoolExecutor(chunksize=16)`，否则串行。Windows spawn 三条铁律全部满足：
  worker 为顶层函数、只接收 `(绝对路径, 相对路径)` 字符串元组（**文件内容由 worker
  自读**，主进程零 pickle 负载）、入口 `repolens.py` 与 `repo_lens/__main__.py` 均具备
  `if __name__ == "__main__"` 守卫。并行侧任何异常自动回退串行。
  新增 `--workers N` 与 `settings.analysis.workers`（`0`=auto=`min(CPU,4)`）。
- **2-B 内容寻址增量缓存**（`cache.py`）：缓存判定改为**双层**——
  ① 签名（`mtime_ns:size`）未变即命中，**零哈希、零读盘**；
  ② 签名失真（git checkout / 拷贝 / touch）时读内容算 SHA-1 前 12 位再比对，
  内容相同仍判命中。新增 `resolve()` 纯函数（返回 `hit/miss` + `reason`
  + `digest`，可单测）、`cache_key()`、以及配套台账
  `.insight_cache/filehash.json`（`relpath → hash`）。CLI 完成行新增
  「重解析 N/M 文件」，使增量效果可见、可回归。
- **2-C 测试金字塔**（`tests/`，三层 + 原集成层）：
  - `tests/unit/test_py_ast.py`（48 用例）：畸形缩进、语法错误、NUL、中文类名/函数名、
    装饰器叠加、async def、lambda 赋值、type hint 泛型、posonly/kwonly/varargs、
    抽象方法、空文件、**BOM**、CRLF、CR-only、**GBK**、缩进上限、Blueprint 变量路由、
    多装饰器 route、路由 methods 归一化、symlink 循环、0 字节文件、仅注释文件等，
    外加 2-A/2-B 新增契约（worker 兜底 entry 形状、`raw` 参数等价、workers 语义、
    串行与并行结果一致）。
  - `tests/unit/test_cache.py`（22 用例）：三层判定全路径（`sig` / `hash` / `changed` /
    `new` / `entry_missing` / `stat_error`）、**「签名命中不读盘」用「删掉文件后仍命中」
    证明**、记录哈希优先于台账、截断口径、缓存与台账的损坏/版本失配回源。
  - `tests/golden/`（8 用例 + 5 件快照）：fixture 仓库 `--deterministic` 下
    `repo_lens.json` / `repo_lens_report.md` / `AI_CONTEXT.md` / `AGENTS.md` /
    `repo_lens_symbols.json` **逐字节**快照，`pytest --update-golden` 显式更新，
    失败时输出首个差异的紧凑 unified diff 而非整份产物。
    另含**跨绝对路径稳定性**用例（同内容仓库在不同路径下必须产出逐字节相同产物），
    这是快照可进 Git、可跨机器复现的前提。
  - `tests/perf/test_budget.py`（5 用例）：fixture 冷/热跑的宽松耗时预算，
    以及两条**不依赖机器性能**的结构性门禁——小批量**不得**启动进程池、
    `workers=1` 必须串行（用 monkeypatch 让 `ProcessPoolExecutor` 构造即抛异常来证伪）。
    真实仓库指标仅人工采集（`REPOLENS_PERF_REPO=<仓库> pytest tests/perf -s`），
    刻意不进 CI 硬门禁。
  - 单元 / golden / perf 三个标记登记进 `pyproject.toml`，便于按层单跑。

### Changed
- `CACHE_VERSION` 2 → **3**：缓存记录新增 `hash` 字段并引入内容寻址判定，
  旧的「仅签名」记录无法参与哈希比对，故整体失效重建（一次性全量重解析）。
- `RepoConfig` / `ToolConfig` 新增 `workers` 字段，由 `settings.analysis.workers` 注入，
  CLI `--workers` 最高优先。`analysis` 段缺失或类型不对时回退 `0`(auto)，不抛异常。
- `_analyze_compute()` 的 AST 预热重构为「**判定与解析分离**」：主进程逐文件做廉价
  判定（签名 + 必要时的哈希），未命中项收集后交给进程池。串行/并行产出的
  `repo_lens.json` 与 `repo_lens_symbols.json` 经逐字节比对确认完全一致。
- `parse_python_file()` 新增可选参数 `raw: bytes | None`（复用已读内容，避免重复读盘）；
  既有 12 处两参调用（`core_analyzer` / `plugin_analyzer` / `interaction_analyzer` /
  `deep_analyzer`）**签名兼容、无需改动**。

### Fixed
- **UTF-8 BOM 导致整文件事实丢失**（1.6.0 `Notes` 中记录在案的既有缺陷，本阶段修复）。
  `fs_scan.read_text_safe()` 此前以 `utf-8` 解码并保留 BOM，使 `ast.parse(str)` 抛
  `SyntaxError: invalid non-printable character U+FEFF` 而**整文件跳过**，
  该类文件的路由/类/函数事实全部缺失。改法：抽出 `decode_text()`，在解码前剥离
  UTF-8 BOM（等价 `utf-8-sig`，但保留 UTF-8 → GB18030 的回退链），
  与方案 2-C「BOM / 编码边界用例」一并落地。BOM 不产生换行，故 LOC 口径不变。

### Verified
- **`python _run_regress.py` → 18/18 green**（BOM 修复 + 并行化 + 缓存改造后无任何回归）。
- **`pytest tests/` → 84 passed, 1 skipped**（跳过项为 symlink 循环用例，
  当前环境需管理员权限才能创建目录符号链接）。
- 真实仓库 `F:\Sites\VeroRun`（765 个 `.py` / 27.8 万行 / 8 核）实测：

| 场景 | 实测 |
|---|---|
| AST 解析 765 文件 | `workers=1` 14.5~23.2s（**抖动近 60%**）→ `workers=4` **5.7s** → `workers=8` 11.3s |
| 分析段耗时 | 冷跑 10.1s → 热跑（全命中）**1.0s** |
| 增量：改 1 个文件内容 | 重解析 **1/765** |
| 增量：仅 touch（内容不变） | 重解析 **0/765** |
| 完整 CLI 墙钟 | 冷 23.1s / 热 16.4s |
| 合成仓 363 个微型文件 | 并行 0.86x（**比串行慢**） |

结论：**2-B 的增量目标达成且有余量**（热跑分析段 1.0s，方案目标 <3s）；
**2-A 在 765 文件规模取得约 2.6x**（较 `workers=1`），且 `min(CPU,4)` 的自动上限是
必要保护——8 核机器上 `workers=8` 因进程创建/调度开销反而慢近一倍。

### Notes（本阶段记录在案，未处理）
- **方案给出的墙钟性能目标（冷 <10s / 热 <3s）在本机不达标**，但**瓶颈不在 2-A/2-B 的
  优化范围**：该仓上 `hotspot_analyzer`（`git log` 派生 churn）独占约 **10s**，
  `render_tree` + 前端仓 + 符号索引 + 产物落盘合计约 5s，二者都不受 AST 并行化或
  AST 缓存影响。方案的「hotspots/git 段每次全量重算（git log 便宜）」这一前提，
  在本仓（大体量历史 + F: 盘）上不成立。若要继续压墙钟，需针对热点段做增量或
  按 commit 数设阈值短路——已超出本阶段范围。
- **`duration_ms` 口径易误读**：CLI 完成行与 `--summary-only` 的「耗时」止于
  `standards` 阶段，**不含** `render_tree` / 前端仓 / git 热点 / 符号索引 / 落盘，
  因此会出现「报 1.0s、实际墙钟 16.4s」的观感落差。该口径为既有行为，
  修改会影响既有消费方，故本阶段仅在 README/SETTINGS 中显式说明，未改代码。
  （注：2-B 的缓存统计**刻意不写进任何产物**——冷跑与热跑该值必然不同，
  进 `repo_lens.json` 会破坏 `--deterministic` 的可复现性与 golden 层。）
- **`PARALLEL_MIN_FILES = 200` 在本机偏保守**：363 个微型文件的合成仓上并行反而慢 14%
  （进程启动开销盖过收益），而 765 个中等文件时快 2.6x。交叉点与「文件平均大小」
  强相关，单一文件数阈值无法同时覆盖两种负载。本阶段按方案原值保留，
  若后续出现「大量小文件」的真实仓库，建议改为按「总字节数」或
  「串行预估耗时」决策。
- **`tests/fixture_repo/plugins/demo/routes.py` 是被截断的文件**：第 9 行
  `def list_items():` 没有函数体，本身即语法错误（并非仅 BOM 问题）。
  BOM 修复后其错误信息由 `U+FEFF` 变为 `expected an indented block`，
  **可观察事实完全一致**（0 路由/类/函数、`syntax_ok=False`），故未改动该 fixture。
  若要让它回归「合法插件」语义（使 demo 插件真的有路由），需另行确认——
  那会改变既有 fixture 的统计基线。
- `--summary-only` 的 help 文案写「11 行 key=value」，实际为 14 行，文案滞后于实现。

## [1.6.0] - 2026-09-13

阶段一：生态对齐（依据《repolens升级实施方案 v1.5.1→v2.0.0》§3）。
本阶段目标是让 RepoLens 进入 2026 年 Agent 生态的原生视野（AGENTS.md），
并把已有的分析资产（AST / context / query）以 MCP 一等工具的粒度按需供给。

### Added
- **AGENTS.md 兼容产物**（新模块 `report_agents.py`）：把 `repo_lens.json` 既有字段
  （overview / core / plugins / standards / interactions）重排为 Agent 原生可读的
  速览 + 组件契约 + 清单必填/枚举 + 惯例统计 + 架构红线 + 命令速查 + 文档索引。
  三态输出，默认 `workspace`（写产物目录，**绝不触碰仓库根**）：
  `repo` 模式在 `<!-- …:begin auto -->`/`<!-- …:end auto -->` 标记块内原块替换，
  块外用户手写内容一字不动；`off` 不生成。
  配置：`settings.output.agents_md`，或环境变量 `REPO_LENS_AGENTS_MD`，
  或 CLI `--agents-md repo|workspace|off`（优先级 CLI > env > settings > 默认）。
  标记块**匹配**与品牌解耦（正则只认 `:begin auto`/`:end auto` 词形），
  为 v2.0.0 更名预留：改名前写下的块，改名后仍能被识别更新。
- **MCP 一等工具 `repo.context` / `repo.search`**（`mcp/catalog.py`）。
  `repo.context`（参数 `target` / `depth`）返回单个插件或核心模块的聚焦上下文
  Markdown（brief≈700B~4KB），使 Agent 无需先跑全量再翻文件；
  `repo.search`（参数 `symbol` / `limit`）返回 `file/line/kind/owner` 定位事实。
  既有 `repo.query` 改为与 CLI 共用同一实现。
- **AST 符号倒排索引**（新模块 `symbol_index.py`）：输入为 `py_ast` 已产出的逐文件
  事实（零新增解析成本），输出「符号 → file:line:kind:owner」。索引覆盖
  class / function / route 三类；路径统一归一 POSIX 斜杠（消除 Windows 反斜杠导致
  的产物漂移）；owner 由路径派生（`plugins/<id>/…` → `<id>`，其余取顶层目录）；
  路由 rule 去掉 `ast.unparse` 的成对引号，使 URL 可直接精确命中。
  完整索引落独立产物 `repo_lens_symbols.json`，主 JSON 只增轻量 `symbols`
  摘要段（`schema`/`count`/`index_file`）。
- **CLI 子命令 `search`**：`repolens search <symbol> [--limit N] [--format json|table]`。
  精确命中优先 → 大小写不敏感子串回退 → 上限 50 + `truncated` 标志。
  退出码对齐 grep：0=有命中 / 1=无命中 / 2=参数或环境错误。
- **JSON Schema 产物契约**（`repo_lens/schemas/repo_lens.schema.json`，随包分发）
  与**纯标准库轻校验器** `repo_lens/schema_check.py`：支持 `type`（含联合类型）、
  `required`、`properties`、`items`、`enum`、本地 `$ref`，不引入 jsonschema 依赖。
  可 CLI 使用：`python -m repo_lens.schema_check <repo_lens.json>`（rc=0/1/2）。
- `py_ast` 的类 / 函数 / 路由事实新增 `lineno` 字段（符号定位所需的最小新增）。
- 新增回归套件 `verify_agents.py`（11 用例）、`verify_symbols.py`（13 用例）、
  `verify_schema.py`（12 用例）；`verify_mcp.py` 增 5 个往返用例
  （repo.context 往返与字节上限、未知目标转 content 错误、repo.query 往返、
  repo.search 命中/未命中）。`_run_regress.py` 由 15 套扩到 18 套。
- `repo_lens/__init__.py` 集中定义产物文件名常量（`ARTIFACT_*`），
  为 v2.0.0 更名六件套 ④（产物改名）预留单一改动点。

### Changed
- `cli._cmd_context` / `cli._cmd_query` 的核心逻辑抽出为纯函数
  `build_context()` / `build_query()`，并新增 `build_search()` /
  `symbol_index_for()`；CLI 与 MCP catalog 共用同一实现，
  消除"CLI 一套、MCP 另一套"的双实现漂移风险。
- `--only` 默认值由 `json,md,html,ai` 扩为 `json,md,html,ai,agents,symbols`；
  传 `--only json` 等旧用法行为完全不变（新产物仅在显式/默认包含时生成）。
- `_write_reports()` 增可选参数 `parse_cache` / `agents_md_mode`，
  既有调用方（server / verify 脚本）签名兼容，无需改动。
- `SCHEMA_VERSION` 1.2 → **1.3**（MINOR：新增可选 `symbols` 段与 `lineno` 字段，
  消费方忽略未知字段即可）；`TOOL_VERSION` → 1.6.0；`CACHE_VERSION` 1 → 2
  （AST 缓存 entry 结构新增 `lineno`，旧缓存整体失效重建，保证符号索引行号完整）。

### Fixed
- **补齐 P0 遗留：7 套既有 verify 脚本按新 token 流程改造**（本阶段回归检查时发现）。
  P0 安全加固给 `_ConsoleServer` 增加了必填参数 `auth_token`、并要求 `/api/*` 携带令牌，
  但方案 §2「兼容注意」中点名的「既有验证脚本按新 token 流程改造」并未执行，
  导致 `verify_mcp_http / verify_ui / verify_mcp_outbound / verify_web_gap /
  verify_features / verify_datedir / verify_ui_startup` 七套**在本次修复前全部为红**
  （`TypeError: missing 1 required positional argument: 'auth_token'`，或普通
  `ThreadingHTTPServer` 缺 `auth_token` 属性导致连接被直接断开）。
  修法：六个直接构造 `ThreadingHTTPServer` 的脚本显式置 `srv.auth_token = ""`，
  `verify_ui_startup.py` 改用 `_ConsoleServer(addr, handler, "")` —— 即方案定义的
  「降级无鉴权模式」。鉴权矩阵本身仍由 `verify_security.py` 独占覆盖，
  这些套件聚焦各自被测能力，**不放宽任何生产代码的默认安全姿态**。
  修复后 `_run_regress.py` 18/18 全绿。
- `verify_mcp_outbound.py` 核心工具基线由魔数 `8` 改为 `len(catalog.CORE_TOOLS)`：
  该套件用「核心工具数 + registry active 脚本数」做动态基线，但核心数仍写死 8，
  本阶段新增 `repo.context` / `repo.search` 后即误报 3 例。改为从单一事实源
  `CORE_TOOLS` 推导，后续增删核心工具不再需要同步改验证脚本
  （与 P0「verify 去硬编码」同一原则）。

### Verified（DoD 真实仓库冒烟，2026-09-13）
方案 DoD 要求的真实仓库冒烟已补跑，目标仓 `F:\Sites\VeroRun`：

| 指标 | 方案 DoD | 实测 | 一致 |
|---|---|---|---|
| 插件数 | 38 | 38（`manifest 待修复 0`） | ✅ |
| 路由数 | 680 | 680 | ✅ |
| 总行数 | 27.8 万 | 278,367（其中代码行 234,143） | ✅ |

- 全量耗时 **16,776 ms**；产物 `repo_lens.json` 772 KB、`repo_lens_symbols.json` 1.13 MB
  （5482 符号：function 4585 / route 1269 / class 656）。
- `meta.tool_version = 1.6.0`、`meta.schema_version = 1.3` 正确写入产物。
- 新增产物在真实规模下生成正常（`AGENTS.md` 4.4 KB / `AI_CONTEXT.md` 7.8 KB /
  `repo_lens_report.md` 32.8 KB / `repo_lens_report.html` 37.8 KB）；
  `AGENTS.md` 走默认 `workspace` 模式写入产物目录，**仓库根原有 `AGENTS.md` 未被触碰**。

### Notes（本阶段记录在案的既有缺陷，不在本阶段范围）
- `verify_core_probe.py` 的 `REPOLENS_REAL_REPO` 仅作为 `cfg.repo_root` 路径参数传入
  （第 68-70 行 `_setup(...)`），**并不触发任何真实仓库分析**——名字暗示"真实仓库探针"，
  实际只服务于 profile / ToolConfig 的取值断言。故 DoD 的真实仓库冒烟由上述直接运行闭合，
  而非由该套件闭合。建议随阶段二「验证套件去名义化」一并修正其语义或改名。
- `fs_scan.read_text_safe()` 以 `utf-8` 解码并保留 BOM，导致带 UTF-8 BOM 的
  `.py` 文件在 `ast.parse(str)` 阶段被判定为 `SyntaxError: invalid non-printable
  character U+FEFF` 而整文件跳过（实测：`tests/fixture_repo/plugins/demo/routes.py`）。
  影响面：该类文件的路由/类/函数事实全部缺失，进而影响符号索引与报告完整性。
  修复方向（`utf-8-sig` 或改传 bytes 给 `ast.parse`）会改变既有统计口径，
  故按方案安排到阶段二 2-C 的「BOM / 编码边界用例」一并处理。
  **【已于 v1.7.0 修复】**详见 1.7.0 `Fixed` 段。

## [1.5.1] - 2026-09-13

阶段 P0：安全加固 + 工程化地基（依据《repolens升级实施方案 v1.5.1→v2.0.0》）。

### Added
- **serve 四层安全门控**：L1 Host 白名单（防 DNS rebinding）→ L2 Origin 校验（防跨站 POST）
  → L3 Token 鉴权（`secrets.token_urlsafe(24)`，恒时比较；`REPOLENS_TOKEN` 环境变量可固定，
  显式空串为降级无鉴权模式）→ L4 最小豁免（仅 `GET /` 与 `/static/*` 免 token）。
  浏览器 UI 由服务端把 token 注入页面 JS 上下文，`app.js` 统一附带 `X-RepoLens-Token` 头，
  401 时提示刷新页面。
- **verify_security.py**（新回归套件，21 用例）：401/403/200 安全矩阵 + 路由注册表回归 +
  豁免面 + 环境变量固定 + 降级模式。
- **工程化地基**：pyproject.toml（setuptools 后端、`repolens` 入口脚本、`test` 可选依赖组）、
  LICENSE（团队内部使用许可）、README.md（快速开始 / 子命令表 / 安全模型）、本 CHANGELOG。
- `_run_regress.py` 纳入 verify_security（14 → 15 套）。

### Changed
- **server.py 路由注册表重构**：106 分支 if/elif 收口为 `@route` 装饰器注册表
  （43 精确 + 2 前缀），do_GET/do_POST 缩为「门控 → 查表 → 调用 → 统一序列化」；
  每端点一个独立函数，结构上消除「函数内 import 遮蔽外层名字」类缺陷（DONE-14 家族）。
- verify_cache.py 去绝对路径硬编码（`Path(__file__)` 推导）；
  verify_core_probe.py 真实仓库探针改 `REPOLENS_REAL_REPO` 环境变量驱动，未设置时显式
  SKIPPED 并以 fixture 冒烟（任意目录 checkout 全绿）。
- tests/ 工程化整理：13 个非测试脚本（deploy/gen_dashboard/export_facts 等）迁至 `scripts/`，
  tests/ 只留真测试与 fixture 资产。

### Fixed
- 旧名残留清理：`insight pull` 命令建议、`insight context` 用法提示、AI_CONTEXT 切片脚注、
  diff 报告标题、dashboard 品牌字样统一改为 RepoLens 体系
  （保留项：`.insight_cache` 缓存目录名与 `VR_INSIGHT_*`/`~/.verorun-dev-insight` 兼容回落，
  属 v2.0.0 更名六件套范畴）。

## [1.5.0] - 2026-09（回填）

- 新增变更热点分析（hotspots：churn × 体量，借鉴 code-maat/CodeScene 方法论）+ Mermaid 图。
- `analysis` 段为 MINOR 新增（schema 1.2）。
- UI 侧栏化：控制台静态资源外置 repo_lens/ui/static（app.js / tokens.css / icons.js）。

## [1.4.0] - 2026-09（回填）

- 前端/桌面端仓库分析：`--extra-repo` 与自动发现同级 verorun-workplace，`frontend` 段
  （schema 1.1，MINOR）。
- P0 §4.1 配置外部化：settings.json 四级搜索链 + ToolConfig 注入 max_*；
  P0 §4.2 请求级分析缓存（TTL + 显式失效，消除 HTTP/MCP 每请求 5-30s 全量分析）。
- 多仓库注册表（`repos` 子命令 + `--repo-name`）与按仓 profile 覆盖。
