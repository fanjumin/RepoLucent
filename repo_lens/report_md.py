# -*- coding: utf-8 -*-
"""Markdown 报告渲染：人类可读的架构洞察报告。"""
from __future__ import annotations

from . import TOOL_VERSION


def _bar(n: int, width: int = 24) -> str:
    return "█" * max(0, min(width, round(n / 40))) if n else ""


def render_md(data: dict) -> str:
    meta = data["meta"]
    ov = data["overview"]
    core = data["core"]
    plugins = data["plugins"]
    inter = data["interactions"]
    std = data["standards"]

    L: list[str] = []
    w = L.append

    w(f"# 仓库架构洞察报告（RepoLens）")
    w("")
    w(f"> 由 RepoLens v{TOOL_VERSION} 自动生成 · {meta['generated_at']} · "
      f"仓库 `{meta['repo_root']}` · 耗时 {meta['duration_ms']} ms")
    w("")
    w("## 1. 总览")
    w("")
    w("| 指标 | 数值 |")
    w("|---|---|")
    w(f"| 系统核心模块 | {core['module_count']} 个 |")
    w(f"| 业务插件（已识别） | {plugins['count']} 个 |")
    w(f"| Python 文件 | {ov['total_files']} 个（全仓文本文件口径） |")
    w(f"| 代码行 | {ov['total_code_lines']:,} 行 / 总 {ov['total_lines']:,} 行 |")
    w(f"| 插件路由总数 | {sum(p['route_count'] for p in plugins['items'])} 条 |")
    w(f"| manifest 校验失败插件 | {sum(1 for p in plugins['items'] if not p['manifest_valid'])} 个 |")
    w(f"| 核心边界观察项 | {len(inter['boundary_observations']['violations'])} 个 |")
    w(f"| 资产文件（md/yml/json 等，不计代码行） | {ov.get('total_asset_files', 0)} 个 |")
    w("")
    w(f"> 统计口径：{ov.get('code_scope_note', '')}")
    w("")
    w("### 1.1 代码量统计")
    w("")
    w("**按语言/扩展名（代码量 TOP）**")
    w("")
    w("| 类型 | 代码行 | 文件数 |")
    w("|---|---:|---:|")
    for e in ov.get("by_language", [])[:10]:
        w(f"| `{e['ext'] or '(无)'}` | {e['code']:,} | {e['files']} |")
    w("")
    w("**按顶层目录（代码量 TOP）**")
    w("")
    w("| 目录 | 代码行 | 总行 | 文件 |")
    w("|---|---:|---:|---:|")
    for e in ov.get("by_top_dir", [])[:14]:
        w(f"| `{e['dir']}` | {e['code']:,} | {e['lines']:,} | {e['files']} |")
    w("")
    w("**代码量最大文件 TOP 10**")
    w("")
    w("| 文件 | 代码行 | 总行 |")
    w("|---|---:|---:|")
    for e in ov.get("top_files", [])[:10]:
        w(f"| `{e['file']}` | {e['code']:,} | {e['lines']:,} |")
    w("")

    w("## 2. 目录结构（深度 {}）".format(meta["tree_depth"]))
    w("")
    w("```")
    w(meta["tree"])
    w("```")
    w("")

    fe_list = data.get("frontend") or []
    if fe_list:
        w("## 2-b. 桌面端 / 前端仓库")
        w("")
        for fe in fe_list:
            p_, m_, ov_ = fe["package"], fe["metrics"], fe["overview"]
            w(f"**{fe['root']}**（{fe['kind']}，v{p_['version']}）—— {p_['description']}")
            w("")
            w(f"- 技术栈：{('、'.join(fe['stack'])) or '—'}；构建版本：{('、'.join(fe['build_editions'])) or '—'}")
            w(f"- 规模：{ov_['total_files']} 文件 · {ov_['total_code_lines']:,} 代码行（同一口径，主仓为 {data['overview']['total_code_lines']:,}）")
            w(f"- 结构：页面 {m_['pages']} · 组件 {m_['components']} · store {m_['stores']} · 服务模块 {m_['services']} · 主进程 TS {m_['electron_main_ts']} · 内嵌 Python {m_['native_py']}")
            w(f"- i18n：{('、'.join(m_['i18n_locales'])) or '—'} · 测试：单测 {m_['tests_unit']} / E2E {m_['tests_e2e']} · 工具脚本 {m_['tool_scripts']} 个")
            w(f"- 依赖：运行时 {p_['deps_count']} / 开发 {p_['dev_deps_count']} · npm scripts {p_['scripts_count']} 个"
              + (f"（禁用占位：{'、'.join(p_['scripts_disabled'])}）" if p_['scripts_disabled'] else ""))
            w("")

    w("## 3. 系统核心模块")
    w("")
    w("| 模块 | 职责 | .py | LOC | 关键类 | 路由 |")
    w("|---|---|---:|---:|---|---:|")
    for m in core["modules"]:
        key_classes = "、".join(c["name"] for c in m["classes"][:3]) or "—"
        w(f"| {m['name']} | {m['description'][:40]} | {m['py_files']} | "
          f"{m['loc']:,} | {key_classes} | {m['route_count']} |")
    w("")

    # 根目录入口脚本
    if core["entry_files"]:
        w("### 3.1 根目录入口脚本")
        w("")
        w("| 文件 | 说明 | LOC |")
        w("|---|---|---:|")
        for e in core["entry_files"][:15]:
            w(f"| `{e['file']}` | {e['docstring'] or '—'} | {e['loc']} |")
        w("")

    ps = core["plugin_system"]
    if ps.get("base_plugin"):
        bp = ps["base_plugin"]
        w("### 3.2 插件开发契约：BasePlugin（{}）".format(bp["file"]))
        w("")
        if bp.get("docstring"):
            w(f"> {bp['docstring']}")
            w("")
        w("所有插件必须继承 `plugin_manager.base.BasePlugin`：")
        w("")
        w("| 方法 | 签名 | 必须 | 说明 |")
        w("|---|---|---|---|")
        for m in bp["methods"]:
            must = "**是**" if m["abstract"] else ""
            w(f"| `{m['name']}` | `{m['signature'][:70]}` | {must} | {m['docstring'] or '—'} |")
        w("")
        w("运行时由 PluginManager 注入：`self.manager`（管理器）、`self.app`（Flask 应用）、"
          "`self.plugin_info`（清单信息）、`self._log`（独立日志器）。")
        w("")

    if ps.get("manager_api"):
        ma = ps["manager_api"]
        w("### 3.3 PluginManager 关键 API（{}）".format(ma["file"]))
        w("")
        w("| 方法 | 签名 |")
        w("|---|---|")
        for m in ma["methods"][:20]:
            w(f"| `{m['name']}` | `{m['signature'][:80]}` |")
        w("")

    if ps.get("discovery_rules"):
        w("### 3.4 插件发现规则（plugin_manager/discovery.py 原文）")
        w("")
        for ln in ps["discovery_rules"]:
            if ln.strip():
                w(f"- {ln.strip()}")
        w("")

    if ps.get("support_modules"):
        w("### 3.5 插件框架支撑模块")
        w("")
        w("| 模块 | 说明 |")
        w("|---|---|")
        for s in ps["support_modules"]:
            w(f"| `{s['module']}` | {s['docstring'] or '—'} |")
        w("")

    w("## 4. 业务插件目录（{} 个）".format(plugins["count"]))
    w("")
    w("| 插件 | 版本 | 角色 | 分类 | 路由 | LOC | BasePlugin | manifest |")
    w("|---|---|---|---|---:|---:|---|---|")
    for p in plugins["items"]:
        has_cls = "✓" if p["plugin_classes"] else "✗"
        ok = "✓" if p["manifest_valid"] else "✗ " + ";".join(p["manifest_errors"][:2])
        w(f"| {p['identifier']} | {p['version'] or '—'} | {p['agent_role'] or '—'} | "
          f"{p['category'] or '—'} | {p['route_count']} | {p['loc']:,} | {has_cls} | {ok} |")
    w("")

    if plugins["rejected_dirs"]:
        w("### 4.1 未通过发现规则的目录")
        w("")
        for r in plugins["rejected_dirs"]:
            w(f"- `{r['dir']}`：{'；'.join(r['reasons'])}")
        w("")

    w("## 5. 核心与插件的交互")
    w("")
    w("### 5.1 插件 → 核心导入排名（哪些核心设施被插件依赖最多）")
    w("")
    if inter["plugins_import_core"]:
        w("| 核心模块 | 插件侧导入次数 |")
        w("|---|---:|")
        for e in inter["plugins_import_core"]:
            w(f"| `{e['module']}` | {e['import_count']} |")
    else:
        w("（未检出）")
    w("")

    if inter["plugins_use_base_helpers"]:
        w("### 5.2 插件辅助库使用情况（plugins/_base/*）")
        w("")
        w("| 辅助库 | 导入次数 |")
        w("|---|---:|")
        for e in inter["plugins_use_base_helpers"]:
            w(f"| `plugins._base.{e['helper']}` | {e['import_count']} |")
        w("")

    if inter["plugin_to_plugin"]:
        w("### 5.3 插件间依赖（manifest depends_on / 直接导入）")
        w("")
        for e in inter["plugin_to_plugin"][:30]:
            w(f"- `{e['from']}` → {', '.join('`' + t + '`' for t in e['to'])}")
        w("")

    bo = inter["boundary_observations"]
    w("### 5.4 核心边界检查")
    w("")
    w(f"检查规则：{bo['rule']}；共检查核心侧 {bo['checked_files']} 个 .py 文件。")
    w("")
    if bo["violations"]:
        w("| 文件 | 直接导入 |")
        w("|---|---|")
        for v in bo["violations"][:30]:
            w(f"| `{v['file']}` | `{v['imports']}` |")
    else:
        w("未发现核心 → 业务插件的直接导入，边界清晰。")
    w("")

    hs = data.get("hotspots")
    if hs:
        w("### 5.5 变更热点（近 %d 天 churn × 体量，code-maat 方法论）" % hs["days"])
        w("")
        w("> " + hs["note"])
        w("")
        w("**高风险文件带（churn≥%d 且 LOC≥%d）**" % (5, 800))
        w("")
        high = [r for r in hs["files_top"] if r["high_risk"]]
        if high:
            w("| 文件 | churn | 代码行 | 热度分 |")
            w("|---|---:|---:|---:|")
            for r in high:
                w(f"| `{r['file']}` | {r['churn']} | {r['loc']:,} | {r['score']} |")
        else:
            w("（近 %d 天无同时满足高改动+大体量的文件）" % hs["days"])
        w("")
        w("**改动最频繁 TOP 10（无论体量）**")
        w("")
        w("| 文件 | churn | 新增行 | 代码行 | 所属 |")
        w("|---|---:|---:|---:|---|")
        for r in hs["files_top"][:10]:
            w(f"| `{r['file']}` | {r['churn']} | {r['added_lines']:,} | {r['loc']:,} | {r['group']} |")
        w("")
        w("**按插件/模块聚合（churn TOP）**")
        w("")
        w("| 组 | churn | 文件数 | 代码行 | 高风险文件 |")
        w("|---|---:|---:|---:|---:|")
        for g in hs["groups_top"][:10]:
            w(f"| `{g['group']}` | {g['churn']} | {g['files']} | {g['loc']:,} | {g['high_risk_files']} |")
        w("")
        w("<details><summary>插件依赖图（Mermaid，渲染后可看）</summary>")
        w("")
        w("```mermaid")
        w(hs["mermaid"].rstrip())
        w("```")
        w("")
        w("</details>")
        w("")

    w("## 6. 开发规范摘要")
    w("")
    mr = std["manifest_required"]
    w(f"**plugin.json 必填字段**（来源：{std['manifest_schema_file'] or '内置回退清单'}）：")
    w("")
    w("```")
    w(", ".join(mr))
    w("```")
    w("")
    if std.get("manifest_enums"):
        w("**受控枚举**：")
        w("")
        for field, values in std["manifest_enums"].items():
            w(f"- `{field}`：{' / '.join(map(str, values))}")
        w("")
    if std.get("plugin_standard"):
        s = std["plugin_standard"]
        w(f"**插件标准**：[{s['title']}](docs/{s['file'].split('/')[-1]})，共 {len(s['sections'])} 章：")
        w("")
        for sec in s["sections"][:20]:
            w(f"- {sec}")
        w("")
    if std.get("agents_md"):
        a = std["agents_md"]
        w(f"**AGENTS.md 规则索引**（{a['file']}，开发前必须完整阅读原文）：")
        w("")
        for h in a["headings"][:15]:
            w(f"- {h}")
        w("")

    if std.get("key_docs"):
        w("### 6.1 重点文档索引")
        w("")
        w("| 文档 | 标题 |")
        w("|---|---|")
        for d in std["key_docs"]:
            w(f"| `{d['file']}` | {d['title']} |")
        w("")

    if std.get("plugin_templates"):
        w("### 6.2 官方插件模板（plugins/_templates/）")
        w("")
        w("| 模板 | 版本 | 说明 |")
        w("|---|---|---|")
        for t in std["plugin_templates"]:
            w(f"| `{t['dir'].split('/')[-1]}` | {t['version'] or '—'} | {t['description'] or '—'} |")
        w("")

    w("## 7. 新插件开发流程速览")
    w("")
    w("1. 复制 `plugins/_templates/react_plugin`（或 vue_plugin）到 `plugins/<identifier>/`；")
    w(f"2. 编写 `plugin.json`：必填 {', '.join(mr)}；`agent_role` 必须取 11 个核心角色之一，`capabilities` 非空；")
    w("3. 实现 BasePlugin 子类（`setup/activate/deactivate` 为抽象方法），运行时引用由 PluginManager 注入；")
    w("4. 用 Flask Blueprint 暴露路由（参考现有插件 `url_prefix=/admin/<identifier>` 惯例）；")
    w("5. 数据库统一走 `get_pooled_connection()` + 插件独立 schema，禁止私有连接池；")
    w("6. 文案走插件自带 `i18n/*.yml` + `self.t()`，与系统 i18n 完全隔离；")
    w("7. 运行本工具复核 manifest 校验与路由提取，再按 docs/plugin-standard 提交审核。")
    w("")

    if data.get("deep_dive"):
        dd = data["deep_dive"]
        t = "插件" if dd["target_type"] == "plugin" else "核心模块"
        w(f"## 8. 指定目标深度分析：{t} `{dd['name']}`（--{'plugin' if dd['target_type'] == 'plugin' else 'module'}）")
        w("")
        loc = dd.get("locations", {})
        meta = dd.get("manifest") or {}
        w(f"**规模**：{dd['file_count']} 文件 · {loc.get('loc_total')} 行 · "
          f"{loc.get('loc_code')} 代码行 · {dd['routes_count']} 路由")
        if meta:
            w(f" ｜ **identifier** `{meta.get('identifier')}` · **版本** {meta.get('version')} · "
              f"**角色** {meta.get('agent_role')} · **分类** {meta.get('category')}")
        w("")
        w("| 文件 | 行数 | 代码行 | 类 | 函数 | 路由 |")
        w("|---|---:|---:|---:|---:|---:|")
        for f in dd["files"][:6]:
            w(f"| `{f['file']}` | {f['loc']} | {f['loc_code']} | {f['classes']} | "
              f"{f['functions']} | {f['routes']} |")
        if dd.get("routes") and dd["routes_count"] > 0:
            w("")
            w(f"路由明细（前 20/{dd['routes_count']}）：`" + "`, `".join(
                r['endpoint'] for r in dd['routes'][:20]) + "`")
        w("")

    findings = data.get("findings") or {}
    if findings.get("items"):
        summary = findings.get("summary") or {}
        w("## 9. 规则检查结果（阶段 F / 2.0.0）")
        w("")
        w("| 级别 | 数量 |")
        w("| --- | --- |")
        w(f"| error | {summary.get('error', 0)} |")
        w(f"| warning | {summary.get('warning', 0)} |")
        w(f"| info | {summary.get('info', 0)} |")
        w("")
        for f in findings["items"]:
            loc = f" @{f['file']}" if f.get("file") else ""
            w(f"- **[{f['severity']}] {f['rule_id']}** `{f['target']}`{loc}: {f['message']}")
            if f.get("evidence"):
                w(f"  - 证据: {f['evidence']}")
        w("")

    w("---")
    w("")
    w("*本报告由本地工具生成，属于开发辅助产物，不进入 Git 版本控制。*")

    # ---- 报告模板化（最小版）：settings.report_template.md = 段 id 有序列表 ----
    # 段 id：overview/tree/frontend/core/plugins/interactions/standards/workflow/deep_dive。
    # 只做段的过滤与重排，不改任何数据结构（SCHEMA_VERSION 1.2 契约不动）；
    # 未配置或全部 id 未命中时输出与旧版逐字节一致。
    try:
        from .settings import get_setting
        tpl = get_setting("report_template", None)
        if isinstance(tpl, dict):
            tpl = tpl.get("md")
        return apply_md_template("\n".join(L) + "\n", tpl)
    except Exception:  # noqa: BLE001 —— 模板机制故障不得影响报告生成
        return "\n".join(L) + "\n"


#: `## <编号>.` 头 → 段 id（与 render_md 的章节编号一一对应）
_MD_SECTION_IDS = (
    ("1.", "overview"), ("2.", "tree"), ("2-b.", "frontend"), ("3.", "core"),
    ("4.", "plugins"), ("5.", "interactions"), ("6.", "standards"),
    ("7.", "workflow"), ("8.", "deep_dive"), ("9.", "findings"),
)


def _md_section_id(header: str) -> str | None:
    for prefix, sid in _MD_SECTION_IDS:
        if header.startswith("## " + prefix):
            return sid
    return None


def apply_md_template(md_text: str, sections) -> str:
    """按模板（段 id 有序列表）过滤/重排 Markdown 报告。

    - 报告标题块（首个 `## ` 之前）与结尾落款（--- 及其后）恒保留；
    - sections 为空/非 list → 原文返回（零行为变化）；
    - 模板中未出现的段被丢弃；未知 id 忽略；顺序按模板；
    - 模板命中段数为 0 → 回退原文（防误配成空报告）。
    """
    if not sections or not isinstance(sections, list):
        return md_text
    lines = md_text.splitlines(keepends=True)
    # 尾部落款：最后一个独立 "---" 行（若无则为空）——从正文剥离，最后恒补回
    footer = ""
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].rstrip("\r\n") == "---" and i > 0:
            footer = "".join(lines[i:])
            lines = lines[:i]
            break
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not heads:
        return md_text
    blocks: dict[str, str] = {}
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        sid = _md_section_id(lines[i])
        if sid:
            blocks.setdefault(sid, "".join(lines[i:end]))
    preamble = "".join(lines[:heads[0]])
    picked = [blocks[sid] for sid in sections if sid in blocks]
    if not picked:
        return md_text
    return preamble + "".join(picked) + footer
    return "\n".join(L) + "\n"
