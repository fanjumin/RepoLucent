# 仓库开发上下文（RepoLucent 自动生成）

> 生成：RepoLucent v2.0.0 · None · 仓库 `fixture_repo`
> 用途：把本文件作为 AI 助手的上下文，代替通读仓库源码。开发前只需提供本文件。

## 1. 系统架构（系统核心 = 平台引擎；业务插件 = 可插拔能力）

| 核心模块 | 职责 |
|---|---|
| i18n | 国际化：系统级翻译函数 _() 与语言管理 |
| plugin_manager | 插件框架：发现/加载/生命周期/Hooks/事件总线/技能注册/商店 |
| shared | 共享基础设施：HTTP 客户端、日志、可观测性 |

## 2. 插件系统契约

发现规则（plugin_manager/discovery.py）：

1. 插件位于 `plugins/<identifier>/`；2. 必须含 `__init__.py`；3. 必须含合法 `plugin.json`；4. `_`/`.` 开头目录为框架资源，不是插件。

所有插件必须继承 `BasePlugin`（plugin_manager/base.py）：

- `setup(self)` — **[必须实现]** Called once before activate.
- `activate(self)` — **[必须实现]** Enable plugin features.
- `deactivate(self)` — 可选 Tear down.
- `t(self, text, locale=None)` — 可选 Translate text via plugin i18n.

运行时注入：`self.manager`（PluginManager）、`self.app`（Flask app）、`self.plugin_info`、`self._log`；插件文案用 `self.t(text)`（插件自带 i18n/{locale}.yml）。

## 3. plugin.json 必填字段

```
identifier, name, version, description, author, min_app_version, agent_role, capabilities
```
- `agent_role` 枚举：athena / content / business / builder
- `category` 枚举：system / shop / tools

版本号必须为 X.Y.Z 语义化版本；identifier 必须匹配 ^[a-z0-9_]+$。

## 4. 现有插件清单（identifier | 版本 | 角色 | 路由数）

| identifier | 版本 | 角色 | 路由 |
|---|---|---|---:|
| Bad-Id | 1.0 | — | 0 |
| demo | 1.0.0 | business | 2 |

## 5. 与核心交互的固定姿势

- 路由：Flask Blueprint，惯例 `url_prefix=/admin/<identifier>`；
- 数据库：统一 `get_pooled_connection()` 共享连接池 + 插件独立 schema，禁止私有连接池；
- Agent 注册：能力聚合到 `agent_role` 指定的系统核心角色（is_system=1），不新建独立 Agent；
- 依赖其他插件：写在 manifest `depends_on`；
- 辅助设施：`plugins/_base/`（db / embeddings / ratelimit），直接导入使用。

插件最常依赖的核心设施：`plugin_manager`(1)

## 6. 架构红线

- 核心模块不应直接 import 业务插件（plugin_manager 动态加载除外）；
- 新增文件必须使用项目现有目录结构与技术栈，禁止私建数据库/配置/连接池；
- 禁止手动 push 分发仓库，唯一合法来源是 CI 流水线；
- 操作前先方案后执行；对比/分析类指令只输出报告。

---
*本地工具生成物，不进入 Git。完整信息见 repo_lucent_report.md / repo_lucent.json。*
