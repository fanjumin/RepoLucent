# VeroRun AI 系统架构洞察报告

> 由 RepoLucent v1.2.0 自动生成 · 2026-09-09 10:55:36 · 仓库 `C:\Users\jumin\.openclaw-autoclaw\workspace\projects\RepoLucent\tests\fixture_repo` · 耗时 55 ms

## 1. 总览

| 指标 | 数值 |
|---|---|
| 系统核心模块 | 3 个 |
| 业务插件（已识别） | 2 个 |
| Python 文件 | 13 个（全仓文本文件口径） |
| 代码行 | 53 行 / 总 60 行 |
| 插件路由总数 | 2 条 |
| manifest 校验失败插件 | 1 个 |
| 核心边界观察项 | 0 个 |
| 资产文件（md/yml/json 等，不计代码行） | 3 个 |

> 统计口径：代码行统计口径：真实代码扩展名 (.py/.js/.ts/.vue/.html/.css/.sh/.sql)；已排除 docs/ 目录、根目录本地调试脚本、临时文件；.md/.yml/.json 等文档/文案仅计入资产文件数，不计代码行。

### 1.1 代码量统计

**按语言/扩展名（代码量 TOP）**

| 类型 | 代码行 | 文件数 |
|---|---:|---:|
| `.py` | 53 | 10 |

**按顶层目录（代码量 TOP）**

| 目录 | 代码行 | 总行 | 文件 |
|---|---:|---:|---:|
| `plugin_manager` | 31 | 34 | 3 |
| `plugins` | 18 | 22 | 7 |
| `i18n` | 2 | 2 | 1 |
| `shared` | 2 | 2 | 1 |
| `<root>` | 0 | 0 | 1 |

**代码量最大文件 TOP 10**

| 文件 | 代码行 | 总行 |
|---|---:|---:|
| `plugin_manager\base.py` | 19 | 21 |
| `plugins\demo\routes.py` | 8 | 10 |
| `plugin_manager\discovery.py` | 7 | 7 |
| `plugins\demo\__init__.py` | 6 | 7 |
| `plugin_manager\manager.py` | 5 | 6 |
| `i18n\__init__.py` | 2 | 2 |
| `plugins\_base\db.py` | 2 | 2 |
| `plugins\bad\__init__.py` | 2 | 2 |
| `shared\__init__.py` | 2 | 2 |
| `plugins\__init__.py` | 0 | 1 |

## 2. 目录结构（深度 2）

```
fixture_repo/
├── i18n/  (1 .py)  ← 核心
│   └── __init__.py
├── plugin_manager/  (3 .py)  ← 核心
│   ├── base.py
│   ├── discovery.py
│   └── manager.py
├── plugins/  (5 .py)  ← 插件
│   ├── _base/  (1 .py)
│   ├── bad/  (1 .py)  ← 插件
│   ├── demo/  (2 .py)  ← 插件
│   └── __init__.py
├── shared/  (1 .py)  ← 核心
│   └── __init__.py
├── AGENTS.md
├── debug_probe.py
└── test_results.json
```

## 3. 系统核心模块

| 模块 | 职责 | .py | LOC | 关键类 | 路由 |
|---|---|---:|---:|---|---:|
| i18n | 国际化：系统级翻译函数 _() 与语言管理 | 1 | 2 | — | 0 |
| plugin_manager | 插件框架：发现/加载/生命周期/Hooks/事件总线/技能注册/商店 | 3 | 34 | PluginManager、BasePlugin | 0 |
| shared | 共享基础设施：HTTP 客户端、日志、可观测性 | 1 | 2 | — | 0 |

### 3.2 插件开发契约：BasePlugin（plugin_manager/base.py）

所有插件必须继承 `plugin_manager.base.BasePlugin`：

| 方法 | 签名 | 必须 | 说明 |
|---|---|---|---|
| `setup` | `setup(self)` | **是** | Called once before activate. |
| `activate` | `activate(self)` | **是** | Enable plugin features. |
| `deactivate` | `deactivate(self)` |  | Tear down. |
| `t` | `t(self, text, locale=None)` |  | Translate text via plugin i18n. |

运行时由 PluginManager 注入：`self.manager`（管理器）、`self.app`（Flask 应用）、`self.plugin_info`（清单信息）、`self._log`（独立日志器）。

### 3.3 PluginManager 关键 API（plugin_manager/manager.py）

| 方法 | 签名 |
|---|---|
| `is_enabled` | `is_enabled(self, pid)` |
| `get_config` | `get_config(self, pid)` |

### 3.4 插件发现规则（plugin_manager/discovery.py 原文）

- Plugin Manager - PluginDiscovery.
- Rules:
- 1. Must be plugins/<name>/ subdir
- 2. Must contain __init__.py
- 3. Must contain plugin.json (valid JSON)
- 4. Ignore dirs starting with _ or .

## 4. 业务插件目录（2 个）

| 插件 | 版本 | 角色 | 分类 | 路由 | LOC | BasePlugin | manifest |
|---|---|---|---|---:|---:|---|---|
| Bad-Id | 1.0 | — | — | 0 | 2 | ✗ | ✗ 缺少必填字段: description;缺少必填字段: author |
| demo | 1.0.0 | business | — | 2 | 17 | ✓ | ✓ |

## 5. 核心与插件的交互

### 5.1 插件 → 核心导入排名（哪些核心设施被插件依赖最多）

| 核心模块 | 插件侧导入次数 |
|---|---:|
| `plugin_manager` | 1 |

### 5.3 插件间依赖（manifest depends_on / 直接导入）

- `demo` → `shop`

### 5.4 核心边界检查

检查规则：核心模块不应直接 import 业务插件（plugin_manager 动态加载除外）；共检查核心侧 5 个 .py 文件。

未发现核心 → 业务插件的直接导入，边界清晰。

## 6. 开发规范摘要

**plugin.json 必填字段**（来源：docs/plugin-manifest.schema.json）：

```
identifier, name, version, description, author, min_app_version, agent_role, capabilities
```

**受控枚举**：

- `agent_role`：athena / content / business / builder
- `category`：system / shop / tools

**AGENTS.md 规则索引**（AGENTS.md，开发前必须完整阅读原文）：

- 路径最高铁律
- 流水线铁律

### 6.1 重点文档索引

| 文档 | 标题 |
|---|---|
| `AGENTS.md` | AGENTS.md |
| `docs/plugin-manifest.schema.json` | plugin-manifest.schema.json |

## 7. 新插件开发流程速览

1. 复制 `plugins/_templates/react_plugin`（或 vue_plugin）到 `plugins/<identifier>/`；
2. 编写 `plugin.json`：必填 identifier, name, version, description, author, min_app_version, agent_role, capabilities；`agent_role` 必须取 11 个核心角色之一，`capabilities` 非空；
3. 实现 BasePlugin 子类（`setup/activate/deactivate` 为抽象方法），运行时引用由 PluginManager 注入；
4. 用 Flask Blueprint 暴露路由（参考现有插件 `url_prefix=/admin/<identifier>` 惯例）；
5. 数据库统一走 `get_pooled_connection()` + 插件独立 schema，禁止私有连接池；
6. 文案走插件自带 `i18n/*.yml` + `self.t()`，与系统 i18n 完全隔离；
7. 运行本工具复核 manifest 校验与路由提取，再按 docs/plugin-standard 提交审核。

---

*本报告由本地工具生成，属于开发辅助产物，不进入 Git 版本控制。*
