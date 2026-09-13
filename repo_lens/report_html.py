# -*- coding: utf-8 -*-
"""HTML 报告渲染：自包含 CSS 的可视化架构洞察报告（可直接打开浏览/分享）。

采用"先拼片段变量、再单模板插值"的方式，避免巨型 f-string 内嵌套表达式。
"""
from __future__ import annotations

import html as _h

from . import TOOL_VERSION


def _e(s) -> str:
    return _h.escape(str(s if s is not None else ""))


def render_html(data: dict) -> str:
    meta, ov = data["meta"], data["overview"]
    core, plugins = data["core"], data["plugins"]
    inter, std = data["interactions"], data["standards"]

    route_total = sum(p["route_count"] for p in plugins["items"])
    invalid = sum(1 for p in plugins["items"] if not p["manifest_valid"])
    vio = inter["boundary_observations"]["violations"]

    def row(cells):
        return "<tr>" + "".join(cells) + "</tr>"

    core_rows = "".join(
        row([f"<td><code>{_e(m['name'])}</code></td>",
             f"<td>{_e((m['description'] or '')[:46])}</td>",
             f"<td class='num'>{m['py_files']}</td>",
             f"<td class='num'>{m['loc']:,}</td>",
             f"<td>{_e('、'.join(c['name'] for c in m['classes'][:3]) or '—')}</td>",
             f"<td class='num'>{m['route_count']}</td>"])
        for m in core["modules"])

    plugin_rows = "".join(
        row([f"<td><code>{_e(p['identifier'])}</code></td>",
             f"<td>{_e(p['version'] or '—')}</td>",
             f"<td>{_e(p['agent_role'] or '—')}</td>",
             f"<td>{_e(p['category'] or '—')}</td>",
             f"<td class='num'>{p['route_count']}</td>",
             f"<td class='num'>{p['loc']:,}</td>",
             f"<td class='{'ok' if p['plugin_classes'] else 'bad'}'>{'✓' if p['plugin_classes'] else '✗'}</td>",
             f"<td class='{'ok' if p['manifest_valid'] else 'bad'}' "
             f"title='{_e('; '.join(p['manifest_errors']))}'>{'✓' if p['manifest_valid'] else '✗'}</td>"])
        for p in plugins["items"])

    bp = (core.get("plugin_system") or {}).get("base_plugin")
    bp_rows = ""
    if bp:
        bp_rows = "".join(
            row([f"<td><code>{_e(m['name'])}</code></td>",
                 f"<td><code>{_e(m['signature'][:60])}</code></td>",
                 f"<td class='{'warn' if m['abstract'] else 'ok'}'>"
                 f"{'必须实现' if m['abstract'] else '可选'}</td>",
                 f"<td>{_e((m['docstring'] or '—')[:60])}</td>"])
            for m in bp["methods"][:20])

    dep_html = ""
    if inter["plugins_import_core"]:
        dep_html = ("<h3>插件 → 核心导入排名</h3><table><tr><th>核心模块</th><th>导入次数</th></tr>"
                    + "".join(row([f"<td><code>{_e(x['module'])}</code></td>",
                                    f"<td class='num'>{x['import_count']}</td>"])
                              for x in inter["plugins_import_core"][:10])
                    + "</table>")

    vio_html = ("<h3>边界观察项（核心直接导入业务插件）</h3><table>"
                "<tr><th>文件</th><th>导入</th></tr>"
                + "".join(row([f"<td><code>{_e(v['file'])}</code></td>",
                               f"<td><code>{_e(v['imports'])}</code></td>"])
                          for v in vio[:30])
                + "</table>") if vio else "<p class='ok'>未发现核心 → 业务插件的直接导入，边界清晰。</p>"

    enums_html = "".join(f"<li><code>{_e(k)}</code>：{_e(' / '.join(map(str, v)))}</li>"
                         for k, v in (std.get("manifest_enums") or {}).items())

    # ---- 代码量统计片段 ----
    code_html = ["<h2>代码量统计</h2>"]
    code_html.append("<h3>按语言 / 扩展名（代码量 TOP）</h3><table><tr><th>类型</th>"
                     "<th class='num'>代码行</th><th class='num'>文件</th></tr>")
    for e in (ov.get("by_language") or [])[:8]:
        code_html.append(row([f"<td><code>{_e(e['ext']) or '(无)'}</code></td>",
                              f"<td class='num'>{e['code']:,}</td>",
                              f"<td class='num'>{e['files']}</td>"]))
    code_html.append("</table>")
    code_html.append("<h3>按顶层目录（代码量 TOP）</h3><table><tr><th>目录</th>"
                     "<th class='num'>代码行</th><th class='num'>总行</th><th class='num'>文件</th></tr>")
    for e in (ov.get("by_top_dir") or [])[:12]:
        code_html.append(row([f"<td><code>{_e(e['dir'])}</code></td>",
                              f"<td class='num'>{e['code']:,}</td>",
                              f"<td class='num'>{e['lines']:,}</td>",
                              f"<td class='num'>{e['files']}</td>"]))
    code_html.append("</table>")
    code_html.append("<h3>代码量最大文件 TOP 10</h3><table><tr><th>文件</th>"
                     "<th class='num'>代码行</th><th class='num'>总行</th></tr>")
    for e in (ov.get("top_files") or [])[:10]:
        code_html.append(row([f"<td><code>{_e(e['file'])}</code></td>",
                              f"<td class='num'>{e['code']:,}</td>",
                              f"<td class='num'>{e['lines']:,}</td>"]))
    code_html.append("</table>")
    code_stats_html = "".join(code_html)

    # ---- 指定目标深度分析片段 ----
    deep_html = ""
    dd = data.get("deep_dive")
    if dd:
        loc = dd.get("locations", {})
        dd_meta = dd.get("manifest") or {}
        t = "插件" if dd["target_type"] == "plugin" else "核心模块"
        deep_html = (f"<h2>指定目标深度分析：{t} <code>{_e(dd['name'])}</code></h2>"
                     f"<p><b>规模</b>：{dd['file_count']} 文件 · {loc.get('loc_total')} 行 · "
                     f"{loc.get('loc_code')} 代码行 · {dd.get('routes_count')} 路由"
                     + (f" ｜ <b>identifier</b> <code>{_e(dd_meta.get('identifier'))}</code> · "
                        f"<b>版本</b> {_e(dd_meta.get('version'))} · <b>角色</b> {_e(dd_meta.get('agent_role'))}"
                        if dd_meta else "") + "</p>"
                     + "<table><tr><th>文件</th><th class='num'>行数</th>"
                     "<th class='num'>代码行</th><th class='num'>类</th>"
                     "<th class='num'>函数</th><th class='num'>路由</th></tr>"
                     + "".join(row([f"<td><code>{_e(f['file'])}</code></td>",
                                    f"<td class='num'>{f['loc']}</td>",
                                    f"<td class='num'>{f['loc_code']}</td>",
                                    f"<td class='num'>{f['classes']}</td>",
                                    f"<td class='num'>{f['functions']}</td>",
                                    f"<td class='num'>{f['routes']}</td>"])
                               for f in dd.get("files", [])[:8])
                     + "</table><p class='muted'>完整明细见 "
                     f"<code>repolens_deep_{_e(dd['name'])}.md / .json</code>。</p>")

    # ---- 规则引擎 findings 片段（阶段 F / 2.0.0）----
    findings_html = ""
    findings = data.get("findings") or {}
    fd_items = findings.get("items") or []
    if fd_items:
        _sev_cls = {"error": "bad", "warning": "warn", "info": "muted"}
        fd_summary = findings.get("summary") or {}
        fd_rows = "".join(
            row([f"<td class='{_sev_cls.get(f['severity'], 'muted')}'>{_e(f['severity'])}</td>",
                 f"<td><code>{_e(f['rule_id'])}</code></td>",
                 f"<td><code>{_e(f['target'])}</code></td>",
                 f"<td>{_e(f.get('file') or '')}</td>",
                 f"<td>{_e(f['message'])}</td>"])
            for f in fd_items)
        findings_html = (f"<h2>规则检查结果（阶段 F，error={fd_summary.get('error', 0)} "
                         f"warning={fd_summary.get('warning', 0)} "
                         f"info={fd_summary.get('info', 0)}）</h2>"
                         "<table><tr><th>级别</th><th>规则</th><th>目标</th>"
                         "<th>文件</th><th>说明</th></tr>"
                         + fd_rows + "</table>")

    # ---- 桌面端 / 前端仓库片段 ----
    fe_html = ""
    for fe in (data.get("frontend") or []):
        p_, m_, ov_ = fe["package"], fe["metrics"], fe["overview"]
        fe_html += (f"<h2>桌面端仓库：{_e(fe['root'])}（{_e(fe['kind'])}）</h2>"
                    f"<p><b>{_e(p_['name'])} v{_e(p_['version'])}</b> — {_e(p_['description'])}<br>"
                    f"技术栈：{_e('、'.join(fe['stack']))} ｜ 构建：{_e('、'.join(fe['build_editions']))}<br>"
                    f"规模：{ov_['total_files']} 文件 · {ov_['total_code_lines']:,} 代码行 ｜ "
                    f"页面 {m_['pages']} · 组件 {m_['components']} · store {m_['stores']} · "
                    f"主进程 TS {m_['electron_main_ts']} · 内嵌 Python {m_['native_py']} · "
                    f"i18n {_e('、'.join(m_['i18n_locales']))} · 测试 {m_['tests_unit']}+{m_['tests_e2e']}</p>")

    # ---- 变更热点片段（v1.5）----
    hs_html = ""
    hs = data.get("hotspots")
    if hs:
        high_rows = "".join(
            row([f"<td><code>{_e(r['file'])}</code></td>",
                 f"<td class='num'>{r['churn']}</td>",
                 f"<td class='num'>{r['loc']:,}</td>",
                 f"<td class='num'>{r['score']}</td>"])
            for r in hs["files_top"] if r.get("high_risk"))
        top_rows = "".join(
            row([f"<td><code>{_e(r['file'])}</code></td>",
                 f"<td class='num'>{r['churn']}</td>",
                 f"<td class='num'>{r['added_lines']:,}</td>",
                 f"<td class='num'>{r['loc']:,}</td>",
                 f"<td>{_e(r['group'])}</td>"])
            for r in hs["files_top"][:10])
        hs_html = (f"<h2>变更热点（近 {hs['days']} 天 churn × 体量）</h2>"
                   f"<p class='muted'>{_e(hs['note'])}</p>")
        if high_rows:
            hs_html += ("<h3>高风险文件带（churn≥5 且 LOC≥800）</h3>"
                        "<table><tr><th>文件</th><th class='num'>churn</th>"
                        "<th class='num'>LOC</th><th class='num'>热度分</th></tr>"
                        + high_rows + "</table>")
        hs_html += ("<h3>改动最频繁 TOP 10</h3><table><tr><th>文件</th>"
                    "<th class='num'>churn</th><th class='num'>新增行</th>"
                    "<th class='num'>LOC</th><th>所属</th></tr>"
                    + top_rows + "</table>")
        hs_html += (f"<details><summary>插件依赖图（Mermaid）</summary>"
                    f"<pre>{_e(hs['mermaid'])}</pre></details>")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoLens 仓库架构洞察报告</title>
<style>
:root {{ --ink:#1c2430; --muted:#6b7684; --line:#e3e8ee; --blue:#2563eb; --bg:#f6f8fb; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
       color:var(--ink); background:var(--bg); line-height:1.6; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }}
h2 {{ font-size:20px; margin:36px 0 12px; border-left:4px solid var(--blue); padding-left:10px; }}
h3 {{ font-size:16px; margin:22px 0 8px; }}
.meta {{ color:var(--muted); font-size:13px; margin-bottom:24px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
            gap:12px; margin:18px 0; }}
.metric {{ background:#fff; border:1px solid var(--line); border-radius:10px;
           padding:14px 16px; }}
.metric b {{ display:block; font-size:24px; }}
.metric span {{ color:var(--muted); font-size:12px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line);
         border-radius:8px; overflow:hidden; font-size:13px; margin:10px 0 6px; }}
th {{ background:#eef2f7; text-align:left; padding:8px 10px; white-space:nowrap; }}
td {{ padding:7px 10px; border-top:1px solid var(--line); vertical-align:top; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
code {{ background:#eef2f7; padding:1px 5px; border-radius:4px; font-size:12px; }}
pre {{ background:#0f172a; color:#dbe4f0; padding:14px 16px; border-radius:10px;
       overflow:auto; font-size:12px; }}
.ok {{ color:#15803d; }} .bad {{ color:#b91c1c; }} .warn {{ color:#b45309; }}
.muted {{ color:var(--muted); }}
.note {{ background:#fffbeb; border:1px solid #fde68a; border-radius:8px;
         padding:10px 14px; font-size:13px; }}
</style>
</head>
<body><div class="wrap">
<h1>仓库架构洞察报告（RepoLens）</h1>
<div class="meta">由 RepoLens v{TOOL_VERSION} 自动生成 · {_e(meta['generated_at'])} ·
仓库 {_e(meta['repo_root'])} · 耗时 {_e(meta['duration_ms'])} ms · 本地工具产物，不入 Git</div>
<div class="note"><b>统计口径</b>：{_e(ov.get('code_scope_note', ''))}</div>

<div class="metrics">
<div class="metric"><b>{core['module_count']}</b><span>系统核心模块</span></div>
<div class="metric"><b>{plugins['count']}</b><span>业务插件</span></div>
<div class="metric"><b>{ov['total_files']}</b><span>文件总数（含资产）</span></div>
<div class="metric"><b>{ov['total_code_lines']:,}</b><span>代码行</span></div>
<div class="metric"><b>{route_total}</b><span>插件路由</span></div>
<div class="metric"><b>{invalid}</b><span>manifest 待修复</span></div>
</div>

{code_stats_html}

{fe_html}

<h2>目录结构（深度 {_e(meta['tree_depth'])}）</h2>
<pre>{_e(meta['tree'])}</pre>

<h2>系统核心模块</h2>
<table><tr><th>模块</th><th>职责</th><th>.py</th><th>LOC</th><th>关键类</th><th>路由</th></tr>
{core_rows}</table>

<h2>插件开发契约 BasePlugin</h2>
<p class="muted">所有插件必须继承 plugin_manager.base.BasePlugin；运行时引用
（self.manager / self.app / self.plugin_info / self._log）由 PluginManager 注入。</p>
{f"<table><tr><th>方法</th><th>签名</th><th>要求</th><th>说明</th></tr>{bp_rows}</table>" if bp_rows else "<p>未检出 BasePlugin。</p>"}

<h2>业务插件目录（{plugins['count']} 个）</h2>
<table><tr><th>插件</th><th>版本</th><th>角色</th><th>分类</th><th>路由</th><th>LOC</th><th>BasePlugin</th><th>manifest</th></tr>
{plugin_rows}</table>
<p class="muted">✗ manifest 列悬停可查看缺失字段；✗ BasePlugin 列表示未检出继承 BasePlugin 的类。</p>

<h2>核心与插件的交互</h2>
{dep_html}
{vio_html}

{hs_html}

<h2>开发规范摘要</h2>
<div class="note"><b>plugin.json 必填字段</b>：{_e(', '.join(std['manifest_required']))}
<br>来源：{_e(std['manifest_schema_file'] or '内置回退清单')}</div>
{f"<ul>{enums_html}</ul>" if enums_html else ""}
{f"<p><b>插件标准</b>：{_e(std['plugin_standard']['title'])}（{len(std['plugin_standard']['sections'])} 章，详见 docs/{_e(std['plugin_standard']['file'].split('/')[-1])}）</p>" if std.get('plugin_standard') else ""}

{deep_html}

{findings_html}

<h2>新插件开发流程</h2>
<ol>
<li>复制 <code>plugins/_templates/react_plugin</code>（或 vue）到 <code>plugins/&lt;identifier&gt;/</code></li>
<li>编写 <code>plugin.json</code>（必填字段 + agent_role 枚举 + capabilities 非空）</li>
<li>实现 BasePlugin 子类（setup / activate / deactivate 抽象方法）</li>
<li>Flask Blueprint 暴露路由，惯例 url_prefix=/admin/&lt;identifier&gt;</li>
<li>数据库走 get_pooled_connection() + 独立 schema；文案走插件 i18n + self.t()</li>
<li>运行本工具复核校验结果，再按 plugin-standard 提交审核</li>
</ol>
</div></body></html>"""
