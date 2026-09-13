# -*- coding: utf-8 -*-
"""AI 上下文渲染：生成 token 友好的 AI_CONTEXT.md。

用途：把本文件直接作为 AI 助手的上下文输入，替代"每次开发前通读 VeroRun 源码"，
显著降低 AI 资源消耗。内容为报告中"AI 开发真正需要"的最小集合。

另提供 `render_target_context()`：按单个插件/核心模块输出聚焦上下文（2~8KB），
用于"改某个插件之前先给我它的路由、manifest 与依赖"，避免为了一个插件读全量 JSON。
"""
from __future__ import annotations

from . import TOOL_VERSION

#: 各颗粒度下路由表的最大条数（brief 的目标是塞得进一次对话，故限制最紧）
_ROUTE_CAP = {"brief": 20, "normal": 60, "full": 10 ** 6}
_CLASS_CAP = {"brief": 5, "normal": 8, "full": 10 ** 6}
#: 各颗粒度下公开函数的最大条数（brief 仅列少量，避免撑爆上下文）
_FUNC_CAP = {"brief": 8, "normal": 20, "full": 10 ** 6}


def render_ai_context(data: dict, max_plugins: int = 60) -> str:
    core, plugins = data["core"], data["plugins"]
    inter = data["interactions"]
    std = data["standards"]
    L: list[str] = []
    w = L.append

    w("# 仓库开发上下文（RepoLucent 自动生成）")
    w("")
    w(f"> 生成：RepoLucent v{TOOL_VERSION} · {data['meta']['generated_at']} · "
      f"仓库 `{data['meta']['repo_root']}`")
    w("> 用途：把本文件作为 AI 助手的上下文，代替通读仓库源码。开发前只需提供本文件。")
    w("")

    w("## 1. 系统架构（系统核心 = 平台引擎；业务插件 = 可插拔能力）")
    w("")
    w("| 核心模块 | 职责 |")
    w("|---|---|")
    for m in core["modules"]:
        w(f"| {m['name']} | {m['description'][:50]} |")
    w("")

    fe = data.get("frontend") or []
    if fe:
        w("桌面端/前端仓库：" + "；".join(
            f"{f['root']}（{f['kind']} v{f['package']['version']}，"
            f"{f['overview']['total_code_lines']:,} 代码行，页面 {f['metrics']['pages']}，"
            f"组件 {f['metrics']['components']}，i18n {'/'.join(f['metrics']['i18n_locales'])}）"
            for f in fe))
        w("")
    ps = core.get("plugin_system") or {}
    bp = ps.get("base_plugin")
    w("## 2. 插件系统契约")
    w("")
    w("发现规则（plugin_manager/discovery.py）：")
    w("")
    w("1. 插件位于 `plugins/<identifier>/`；2. 必须含 `__init__.py`；"
      "3. 必须含合法 `plugin.json`；4. `_`/`.` 开头目录为框架资源，不是插件。")
    w("")
    if bp:
        w(f"所有插件必须继承 `BasePlugin`（{bp['file']}）：")
        w("")
        for m in bp["methods"]:
            tag = "**[必须实现]**" if m["abstract"] else "可选"
            doc = (m["docstring"] or "").split("。")[0][:60]
            w(f"- `{m['signature'].split('(')[0]}{m['signature'][m['signature'].find('('):][:50]}` — {tag} {doc}")
        w("")
        w("运行时注入：`self.manager`（PluginManager）、`self.app`（Flask app）、"
          "`self.plugin_info`、`self._log`；插件文案用 `self.t(text)`（插件自带 i18n/{locale}.yml）。")
        w("")

    req = std["manifest_required"]
    w("## 3. plugin.json 必填字段")
    w("")
    w("```")
    w(", ".join(req))
    w("```")
    if std.get("manifest_enums"):
        for k, v in std["manifest_enums"].items():
            w(f"- `{k}` 枚举：{' / '.join(map(str, v))}")
    w("")
    w("版本号必须为 X.Y.Z 语义化版本；identifier 必须匹配 ^[a-z0-9_]+$。")
    w("")

    w("## 4. 现有插件清单（identifier | 版本 | 角色 | 路由数）")
    w("")
    w("| identifier | 版本 | 角色 | 路由 |")
    w("|---|---|---|---:|")
    for p in plugins["items"][:max_plugins]:
        w(f"| {p['identifier']} | {p['version'] or '—'} | {p['agent_role'] or '—'} | {p['route_count']} |")
    if len(plugins["items"]) > max_plugins:
        w(f"| …（其余 {len(plugins['items']) - max_plugins} 个见完整报告） | | | |")
    w("")

    w("## 5. 与核心交互的固定姿势")
    w("")
    w("- 路由：Flask Blueprint，惯例 `url_prefix=/admin/<identifier>`；")
    w("- 数据库：统一 `get_pooled_connection()` 共享连接池 + 插件独立 schema，禁止私有连接池；")
    w("- Agent 注册：能力聚合到 `agent_role` 指定的系统核心角色（is_system=1），不新建独立 Agent；")
    w("- 依赖其他插件：写在 manifest `depends_on`；")
    w("- 辅助设施：`plugins/_base/`（db / embeddings / ratelimit），直接导入使用。")
    w("")
    top_core = inter["plugins_import_core"][:6]
    if top_core:
        w("插件最常依赖的核心设施：" + "、".join(f"`{x['module']}`({x['import_count']})" for x in top_core))
        w("")

    bo = inter["boundary_observations"]
    w("## 6. 架构红线")
    w("")
    w(f"- {bo['rule']}；")
    w("- 新增文件必须使用项目现有目录结构与技术栈，禁止私建数据库/配置/连接池；")
    w("- 禁止手动 push 分发仓库，唯一合法来源是 CI 流水线；")
    w("- 操作前先方案后执行；对比/分析类指令只输出报告。")
    w("")
    if std.get("plugin_standard"):
        s = std["plugin_standard"]
        w(f"## 7. 规范文档索引")
        w("")
        w(f"- 插件标准：`{s['file']}`（{s['title']}，{len(s['sections'])} 章）")
        for d in std.get("key_docs", [])[:8]:
            w(f"- `{d['file']}`：{d['title']}")
        w("")
    w("---")
    w("*本地工具生成物，不进入 Git。完整信息见 repo_lucent_report.md / repo_lucent.json。*")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 按需切片

def render_target_context(data: dict, dd: dict, depth: str = "brief") -> str:
    """渲染单个插件 / 核心模块的聚焦上下文。

    depth=brief   —— 标识、manifest 校验、路由、依赖（约 2-4KB，一次对话可容纳）
    depth=normal  —— 追加 BasePlugin 契约、关键类与方法签名、文件清单
    depth=full    —— 追加全部类/函数签名与导入聚合
    """
    if depth not in _ROUTE_CAP:
        depth = "brief"
    is_plugin = dd["target_type"] == "plugin"
    meta = dd.get("manifest") or {}
    loc = dd.get("locations") or {}
    L: list[str] = []
    w = L.append

    w(f"# Context · {'插件' if is_plugin else '核心模块'} `{dd['name']}`")
    w("")
    w(f"> RepoLucent v{TOOL_VERSION} · 仓库 `{data['meta']['repo_root']}` · "
      f"depth={depth}")
    w("")

    # ---- 1. 标识 ----
    w("## 标识")
    w("")
    w("| 字段 | 值 |")
    w("|---|---|")
    w(f"| 路径 | `{dd.get('path', dd['name'])}` |")
    if is_plugin:
        w(f"| identifier | `{meta.get('identifier', dd['name'])}` |")
        w(f"| 版本 | {meta.get('version') or '—'} |")
        w(f"| 角色 / 分类 | {meta.get('agent_role') or '—'} / {meta.get('category') or '—'} |")
        w(f"| 描述 | {meta.get('description') or '—'} |")
    else:
        w(f"| 说明 | {_module_desc(data, dd['name'])} |")
    w(f"| 规模 | {dd['file_count']} 文件 · {loc.get('loc_total', 0):,} 行"
      f"（代码 {loc.get('loc_code', 0):,}） |")
    w(f"| 路由 | {dd['routes_count']} 条 |")
    if is_plugin:
        ok = meta.get("manifest_valid")
        w(f"| manifest | {'✅ 合法' if ok else '❌ 有问题'} |")
    w("")

    # ---- 2. manifest 校验（有问题就必须在 brief 里出现）----
    if is_plugin:
        errs = meta.get("manifest_errors") or []
        if errs:
            w("## manifest 问题")
            w("")
            for e in errs:
                w(f"- ❌ {e}")
            req = (data.get("standards") or {}).get("manifest_required") or []
            if req:
                w("")
                w(f"必填字段：`{', '.join(req)}`")
            w("")
        elif depth != "brief":
            w("## manifest")
            w("")
            req = (data.get("standards") or {}).get("manifest_required") or []
            w(f"- 校验：✅ 合法；必填字段 `{', '.join(req)}`")
            caps = meta.get("capabilities") or []
            if caps:
                w(f"- capabilities：{', '.join(map(str, caps))}")
            w("")

    # ---- 3. 插件契约（normal 起）----
    if is_plugin and depth != "brief":
        bp = (data["core"].get("plugin_system") or {}).get("base_plugin")
        if bp:
            must = [m for m in bp.get("methods", []) if m.get("abstract")]
            w("## 必须实现的契约")
            w("")
            w(f"继承 `BasePlugin`（`{bp.get('file')}`），必须实现：")
            w("")
            for m in must:
                sig = m.get("signature", "")
                doc = (m.get("docstring") or "").split("。")[0][:60]
                w(f"- `{sig}` — {doc}")
            w("")

    # ---- 4. 路由 ----
    routes = dd.get("routes") or []
    cap = _ROUTE_CAP[depth]
    if routes:
        w(f"## 路由（{len(routes)} 条）")
        w("")
        w("| 端点 | 前缀 | 路径 | Method | 文件 |")
        w("|---|---|---|---|---|")
        for r in routes[:cap]:
            w(f"| `{r.get('endpoint','')}` | {r.get('url_prefix') or '—'} | "
              f"`{r.get('rule','')}` | {','.join(r.get('methods') or [])} | "
              f"`{r.get('file','')}` |")
        if len(routes) > cap:
            w(f"| … | | 另有 {len(routes) - cap} 条 | | |")
        w("")
    elif dd.get("blueprints"):
        w("## 路由")
        w("")
        w("无 `@bp.route` 注册的路由。已声明 Blueprint："
          + "、".join(f"`{b.get('var')}`({b.get('url_prefix') or '无前缀'})"
                     for b in dd["blueprints"]))
        w("")

    # ---- 5. 依赖 ----
    if is_plugin:
        deps = sorted((meta.get("depends_on") or {}).keys())
        depended_by = sorted(
            e["from"] for e in data["interactions"].get("plugin_to_plugin", [])
            if dd["name"] in (e.get("to") or []))
        if deps or depended_by:
            w("## 依赖")
            w("")
            w(f"- 依赖（manifest.depends_on）：{', '.join(deps) if deps else '无'}")
            w(f"- 被依赖（其他插件 import 它）：{', '.join(depended_by) if depended_by else '无'}")
            w("")
    else:
        users = [x["plugin"] for x in (dd.get("depended_by_plugins") or [])]
        if users:
            w("## 被依赖")
            w("")
            w("- 引用此模块的插件：" + "、".join(f"`{u}`" for u in sorted(set(users))))
            w("")

    # ---- 6. 关键类（normal 起）----
    cls_cap = _CLASS_CAP[depth]
    if cls_cap:
        classes = (dd.get("plugin_classes") or []) if is_plugin else []
        rest = [c for c in (dd.get("classes") or []) if c not in classes]
        picked = (classes + rest)[:cls_cap]
        if picked:
            w("## 关键类")
            w("")
            for c in picked:
                bases = ", ".join(c.get("bases") or [])
                w(f"- `{c['name']}`（{bases}）— `{(c.get('file') or '').split(chr(92))[-1]}`"
                  + (f" — {c['docstring']}" if c.get("docstring") else ""))
                for m in (c.get("methods") or [])[:6]:
                    if m.get("name", "").startswith("_"):
                        continue
                    tag = " **[必须实现]**" if m.get("abstract") else ""
                    w(f"    - `{m.get('signature','')}`{tag}")
            w("")
        if is_plugin and not (dd.get("plugin_classes") or []):
            w("## 关键类")
            w("")
            w("- ⚠️ 未发现 `BasePlugin` 子类——该插件可能未遵循插件基类约定。")
            w("")

    # ---- 6b. 模块公开函数（brief 起，避免函数型模块切片空荡）----
    if not is_plugin and depth != "full":
        funcs = dd.get("functions") or []
        fcap = _FUNC_CAP[depth]
        if funcs:
            w("## 公开函数")
            w("")
            for fn in funcs[:fcap]:
                doc = (fn.get("docstring") or "").split("。")[0][:50]
                w(f"- `{fn.get('signature', '')}` — {doc}")
            if len(funcs) > fcap:
                w(f"- … 另有 {len(funcs) - fcap} 个函数")
            w("")

    # ---- 7. 文件清单（normal 起）----
    if depth != "brief" and dd.get("files"):
        w("## 文件清单")
        w("")
        w("| 文件 | 行数 | 代码行 | 类 | 函数 | 路由 |")
        w("|---|---:|---:|---:|---:|---:|")
        for f in dd["files"]:
            w(f"| `{f['file']}` | {f['loc']} | {f['loc_code']} | {f['classes']} | "
              f"{f['functions']} | {f['routes']} |")
        w("")

    # ---- 8. full 专属 ----
    if depth == "full":
        funcs = (dd.get("functions") or [])[:_FUNC_CAP["full"]]
        if funcs:
            w("## 公开函数")
            w("")
            for f in funcs:
                doc = (f.get("docstring") or "").split("。")[0][:60]
                w(f"- `{f.get('signature','')}` — {doc}")
            w("")
        imp = dd.get("imports_aggregated") or {}
        if imp:
            w("## 导入聚合（Top）")
            w("")
            w("、".join(f"`{k}`×{v}" for k, v in list(imp.items())[:15]))
            w("")

    w("---")
    w(f"*切片由 `repolucent context --for "
      f"{'plugin' if is_plugin else 'module'}:{dd['name']} --depth {depth}` 生成。"
      f"完整信息见 repo_lucent.json。*")
    return "\n".join(L) + "\n"


def _module_desc(data: dict, name: str) -> str:
    for m in data["core"]["modules"]:
        if m["name"] == name:
            return (m.get("description") or "—").replace("\n", " ")[:120]
    return "—"
