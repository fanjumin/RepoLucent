# 通用化改造（档位 A：配置化）实施方案

> 目标：让本工具能在**非 VeroRun 的 Python 仓库**上跑起来，切换分析口径**不改任何代码**。
> 原则：**无 `settings.json` 时行为与现状完全等价**——既有 47 个验证用例必须全绿。
>
> 状态：**已实施并验收通过（2026-09-12）**——verify_profile.py 7/7 PASS，既有 8 套验证全绿，真实仓库冒烟与基线一致。
>
> 实施偏差记录（两条，均为守住「零行为变化」硬约束的修正）：
> 1. §4.6 `default_gates()` 无配置时返回 `[]`（维持「不传 --fail-on 就不跑门禁」的历史行为），而非 `GATE_CHOICES` 全集——否则所有未传 `--fail-on` 的运行会突然开始跑门禁并可能 exit 1，破坏硬约束 1。
> 2. §4.3 值绑定陷阱的实际影响面比预估的 3 处大：`cli.py` 顶层导入全部 analyzer，故 `core_analyzer` / `fs_scan` / `hotspot_analyzer` / `deep_analyzer` / `interaction_analyzer` 的顶层值绑定也一并改为运行时查 `config.X`；`core_modules` 采用增量合并语义（profile 出现的键覆盖内置，未出现的保留）；`apply_profile()` 增加内置基线快照，保证同进程多次切换 profile 不互相污染。

---

## 1. 范围与非目标

### 1.1 目标（做什么）
把散落在 7 个文件里的 VeroRun 专属常量，收敛到 `settings.json` 的 `profile` 段，使「分析口径」成为配置项。

### 1.2 非目标（明确不做）
| 不做 | 原因 |
|---|---|
| Profile 适配层 / `component_analyzer` 泛化 | 属档位 B，改动面中等偏大，本次不触碰 |
| 报告结构（`report_md/html/ai`）改造 | 档位 A 下报告字段沿用现状；非 VeroRun 仓库只是部分章节为空 |
| 支持 Java / Go / TS | AST 基于标准库 `ast`，跨语言需换解析器，是另一量级工程 |
| 内核 / 外壳分离 | 属档位 C，等于二次架构重构 |

### 1.3 硬约束
1. **零行为变化**：不提供 `settings.json` 时，所有取值回落到现有 VeroRun 常量。
2. **零第三方依赖**：不引入任何库。
3. **密钥规则不变**：`profile` 段只放分析口径，绝不放密钥。
4. 复用 P0 §4.1 已建的 `settings.py` 读取通道，不另造轮子。

---

## 2. 现状耦合清单（逐项定位，本次核查实锤）

| # | 常量 | 位置 | 当前值（VeroRun 专属） | 外置后键名 |
|---|---|---|---|---|
| 1 | 仓库定位硬判 | `config.py:169/173` | 必须同时存在 `plugins/` 与 `plugin_manager/`，否则 `SystemExit` | `profile.repo_signature` |
| 2 | `PLUGINS_DIR` | `config.py:110` | `"plugins"` | `profile.component.dir` |
| 3 | `MANIFEST_NAME` | `config.py:111` | `"plugin.json"` | `profile.component.manifest` |
| 4 | `MANIFEST_REQUIRED_FALLBACK` | `config.py:114-117` | `identifier/name/version/description/author/min_app_version/agent_role/capabilities` | `profile.component.required_fields` |
| 5 | `CORE_MODULES` | `config.py:87-104` | 17 个 VeroRun 目录 + 中文释义 | `profile.core_modules` |
| 6 | `AUTO_CORE_EXCLUDE` | `config.py:107` | `{docs, deploy, scripts, tools}` | `profile.auto_core_exclude` |
| 7 | `DEFAULT_EXCLUDE_DIRS` | `config.py:22-40` | 含 `.stock_deps` `.trae` `.openclaw` `server_backup` `poc` `poc2` `verorun-plugin-test-report` | `profile.exclude_dirs` |
| 8 | `ROOT_ENTRY_ALLOWLIST` | `config.py:51-54` | `auth_server.py` `health_guardian.py` `run_gunicorn.py` `run_auth_wsgi.py` `version.py` | `profile.root_entry_allowlist` |
| 9 | `KEY_DOCS` | `config.py:120-130` | 含 `docs/plugin-standard-v1.7.md`、`docs/i18n-standard.md` 等 | `profile.key_docs` |
| 10 | `PLUGIN_STANDARD_RE` | `config.py:133` | `docs/plugin-standard-v*.md` | `profile.standard_doc_pattern` |
| 11 | `FRONTEND_REPO_NAMES` | `frontend_analyzer.py:19` | `("verorun-workplace",)` | `profile.frontend_repo_names` |
| 12 | 默认门禁集 | `gate.py` `GATE_CHOICES` + `evaluate()` | 5 项中 4 项绑插件语义 | `profile.default_gates` |

> 说明：`plugin_analyzer.py:69` 的 `identifier` 正则 `^[a-z0-9_]+$` 与 `hooks`/`permissions`/`price_type` 等字段读取，在档位 A 下**不逐字段参数化**——非 VeroRun 仓库通过 `component.dir = ""` 整体短路该分析器即可。

---

## 3. 设计：`settings.json` 的 `profile` 段

### 3.1 Schema

```jsonc
{
  "profile": {
    "name": "verorun",                      // 仅用于展示/日志，默认 "verorun"

    "repo_signature": {                     // 仓库定位特征（#1）
      "dirs": ["plugins", "plugin_manager"],// 这些目录必须全部存在
      "files": []                           // 这些文件必须全部存在；空数组=不要求
    },

    "component": {                          // 组件（VeroRun 语义下=插件）（#2/3/4）
      "dir": "plugins",                     // 空字符串 = 无组件概念，短路 plugin_analyzer
      "manifest": "plugin.json",
      "required_fields": ["identifier", "name", "version", "description",
                          "author", "min_app_version", "agent_role", "capabilities"],
      "id_pattern": "^[a-z0-9_]+$"
    },

    "core_modules": {                        // 核心目录知识库（#5）
      "plugin_manager": "插件框架：发现/加载/生命周期/Hooks/事件总线",
      "orchestrator": "工作流编排引擎：DAG 节点、调度器、触发分发"
      // ……省略，缺省则用内置 VeroRun 全量字典
    },
    "auto_core_exclude": ["docs", "deploy", "scripts", "tools"],  // #6

    "exclude_dirs": null,                   // #7 null/省略=用内置 VeroRun 集合
    "root_entry_allowlist": null,           // #8 同上
    "key_docs": null,                       // #9 同上
    "standard_doc_pattern": "docs/plugin-standard-v*.md",  // #10

    "frontend_repo_names": ["verorun-workplace"],  // #11

    "default_gates": ["manifest-invalid", "boundary-violation",   // #12
                      "plugin-cycle", "route-unprefixed",
                      "file-too-large"]
  }
}
```

### 3.2 两个预设

**A. 默认（不写 `profile` 段）= 现状 VeroRun**，见上。

**B. `generic-python`（普通 Python 仓库）**：

```jsonc
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

---

## 4. 逐文件改动清单

### 4.1 `vr_insight/settings.py` —— 增 `profile` 读取器（新增约 25 行）

```python
def get_profile() -> dict:
    """返回当前 profile 配置；缺省返回空 dict（= VeroRun 内置默认）。"""
    p = get_setting("profile", {})
    return p if isinstance(p, dict) else {}


def profile_get(key: str, default=None):
    """读 profile 字段；未配置则返回 default（调用方传内置 VeroRun 常量）。"""
    v = get_profile().get(key, None)
    return default if v is None else v
```

> 复用既有 `get_setting`，不新增搜索链/环境变量逻辑。

### 4.2 `vr_insight/settings.default.json` —— 增 `profile` 段

写入 §3.1 的 VeroRun 默认值（与现状逐项一致），作为「显式化的现状基线」，便于用户复制修改。

### 4.3 `vr_insight/config.py` —— 常量改为可覆盖（关键，注意陷阱）

**陷阱（必须避开）**：`plugin_analyzer.py` / `sloc_summary.py` 等以 `from .config import MANIFEST_NAME` 形式导入。`from X import Y` 是**值绑定**，若后续再改 `config.MANIFEST_NAME`，已导入模块的 `MANIFEST_NAME` 不会变。

**对策**：新增 `apply_profile()`，并在 `_setup` 最早期调用；同时把**会被覆盖的常量**在使用方改为「运行时查 config 模块属性」。

```python
def apply_profile() -> str:
    """按 settings.profile 覆盖模块级常量；返回生效的 profile 名。

    必须在 _setup 中最早调用，且不晚于任何 analyzer 的惰性导入。
    """
    global PLUGINS_DIR, MANIFEST_NAME, MANIFEST_REQUIRED_FALLBACK, CORE_MODULES
    global AUTO_CORE_EXCLUDE, DEFAULT_EXCLUDE_DIRS, ROOT_ENTRY_ALLOWLIST
    global KEY_DOCS, PLUGIN_STANDARD_RE

    from .settings import profile_get
    name = profile_get("name", "verorun")

    comp = profile_get("component", {}) or {}
    PLUGINS_DIR = comp.get("dir", PLUGINS_DIR)
    MANIFEST_NAME = comp.get("manifest", MANIFEST_NAME)
    if comp.get("required_fields") is not None:
        MANIFEST_REQUIRED_FALLBACK = list(comp["required_fields"])

    CORE_MODULES = profile_get("core_modules", CORE_MODULES) or CORE_MODULES
    AUTO_CORE_EXCLUDE = set(profile_get("auto_core_exclude", AUTO_CORE_EXCLUDE))
    DEFAULT_EXCLUDE_DIRS = set(profile_get("exclude_dirs", DEFAULT_EXCLUDE_DIRS))
    ROOT_ENTRY_ALLOWLIST = set(profile_get("root_entry_allowlist", ROOT_ENTRY_ALLOWLIST))
    KEY_DOCS = list(profile_get("key_docs", KEY_DOCS))
    PLUGIN_STANDARD_RE = profile_get("standard_doc_pattern", PLUGIN_STANDARD_RE)
    return name
```

**使用方改法（3 处）**：
- `plugin_analyzer.py`：顶部 `from .config import MANIFEST_NAME` → `from . import config`，引用处改 `config.MANIFEST_NAME`；并在入口加「`component.dir` 为空 → 返回空 items」的短路。
- `standards_extractor.py`：同上改 `PLUGIN_STANDARD_RE` / `KEY_DOCS` 为运行时查。
- `frontend_analyzer.py`：`FRONTEND_REPO_NAMES` 改为从 profile 读（见 4.5）。

> `sloc_summary.py` / `git_churn.py` / `dangerous_api_scan.py` 是**函数内部惰性导入** `DEFAULT_EXCLUDE_DIRS`，`apply_profile()` 之后执行即可自动拿到新值，**无需改动**。

### 4.4 `config.autodetect_repo` —— 仓库签名可配置（#1）

```python
sig = profile_get("repo_signature", None) or {"dirs": ["plugins", "plugin_manager"], "files": []}
need_dirs = sig.get("dirs") or []
need_files = sig.get("files") or []

def _match(c: Path) -> bool:
    return all((c / d).is_dir() for d in need_dirs) and all((c / f).is_file() for f in need_files)
```

- `need_dirs` 与 `need_files` 均为空 → 接受显式 `--repo` 任意目录（不再 `SystemExit`）。
- 默认行为与现状逐字等价（`all()` 对空列表恒真，但默认 `dirs` 非空）。
- 报错文案改为动态拼签名，便于定位：`未找到匹配仓库（要求目录: plugins, plugin_manager）`。

### 4.5 `vr_insight/frontend_analyzer.py` —— 前端仓库名可配置（#11）

```python
from .settings import profile_get
FRONTEND_REPO_NAMES = tuple(profile_get("frontend_repo_names", ("verorun-workplace",)) or ())
```

空元组 → `discover_frontend_repos()` 直接返回 `[]`，不再去同级目录找。

### 4.6 `vr_insight/gate.py` —— 默认门禁集可配置（#12）

```python
def default_gates() -> list[str]:
    from .settings import profile_get
    return list(profile_get("default_gates", GATE_CHOICES_WITHOUT_ALL) or [])
```

`cli.py` 中 `--fail-on` 缺省处由 `GATE_CHOICES` 改用 `default_gates()`；`expand()` 保持不变（`GATE_CHOICES` 仍是合法项全集，便于显式指定）。

### 4.7 `vr_insight/cli.py` —— `_setup` 注入（1 行 + 1 处）

```python
def _setup(args, with_target=False) -> RepoConfig:
    from .config import apply_profile
    apply_profile()                      # ← 新增：最早调用
    ...
```

并在 `audit`/`gate` 相关缺省处改用 `default_gates()`。

### 4.8 `SETTINGS.md` —— 新增「§5 分析口径（profile）」章节

含 schema、两个预设示例、以及「哪些视图在非 VeroRun 仓库下会为空」的说明。

### 4.9 新增 `profiles/` 目录（可选，推荐）

- `profiles/generic-python.json`：§3.2 的 B 预设，用户 `cp` 为 `settings.json` 即用。
- 随包发布，不自动加载（避免意外改变默认行为）。

---

## 5. 验收：新增 `verify_profile.py`（7 用例）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 无 settings | 行为等价：对 `tests/fixture_repo` 跑 `summary`，字段与基线逐项一致 |
| 2 | generic profile 定位 | `--repo` 指向**本工具自身**（无 `plugins/`+`plugin_manager/`）不再 `ConfigError`，正常产出 |
| 3 | 核心知识库可换 | 注入 `core_modules`，`summary.core_modules` 随之变化 |
| 4 | 仓库签名可换 | `repo_signature.files=["pyproject.toml"]` 时，只有含该文件的目录被接受 |
| 5 | 组件短路 | `component.dir=""` 时 `plugins.items == []` 且不抛异常 |
| 6 | 门禁集可换 | `default_gates=["file-too-large"]` 时 `expand()` 只展开 1 项 |
| 7 | 前端仓库名可换 | `frontend_repo_names=[]` 时 `discover_frontend_repos()` 返回 `[]` |

**回归门**：既有 8 套验证（47 用例）必须全部 exit 0——尤其 `verify_settings.py`（配置通道）与 `verify_mcp*.py`（工具清单仍 13 条）。

---

## 6. 风险与回滚

| 风险 | 应对 |
|---|---|
| 模块级常量值绑定陷阱（§4.3） | 使用方改为运行时查 `config.X`；`sloc_summary` 等惰性导入者无需改。验收用例 1/3 直接覆盖 |
| `apply_profile()` 调用时机晚于某处导入 | 只在 `_setup` 最早期调用一次；`scriptlib/*` 均为函数内惰性导入，不受影响 |
| `exclude_dirs` 配错导致扫描爆炸 | 缺省即内置集合；`profile.exclude_dirs` 为 `null` 时不覆盖 |
| 非 VeroRun 仓库下部分视图为空 | 已知且接受（档位 A 边界）；文档 §5 明确列出 |

**回滚**：删除 `settings.json` 的 `profile` 段即完全恢复现状；所有改动形如「有配置则用、无配置则用内置常量」，无不可逆结构变更。

---

## 7. 落地顺序（逐文件报批）

1. `settings.py`（`get_profile` / `profile_get`）
2. `settings.default.json`（`profile` 段）
3. `config.py`（`apply_profile` + `autodetect_repo` 签名化）
4. `plugin_analyzer.py`（运行时查 config + 组件短路）
5. `standards_extractor.py`（运行时查 config）
6. `frontend_analyzer.py`（前端仓库名）
7. `gate.py`（`default_gates()`）
8. `cli.py`（`_setup` 注入 + `--fail-on` 缺省）
9. `SETTINGS.md` + `profiles/generic-python.json`
10. `verify_profile.py` 实跑 → 全量回归

> 每完成一个文件即可单独评审；确认后我按此顺序改码并附实跑证据。
