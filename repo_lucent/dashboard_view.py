# -*- coding: utf-8 -*-
"""动态仪表盘渲染（由 tests/gen_dashboard.py 自动转换生成，勿手改）。"""
from __future__ import annotations
from collections import Counter


def render_dashboard(d: dict) -> str:
    ov, core, plugins = d["overview"], d["core"], d["plugins"]
    inter, hs = d["interactions"], d.get("hotspots")
    fe = (d.get("frontend") or [])[0] if d.get("frontend") else None
    ver = d["meta"]["tool_version"]
    gen = d["meta"]["generated_at"]

    ROLE_COLORS = {
        "athena": "#1677ff", "content": "#722ed1", "business": "#fa8c16",
        "builder": "#13c2c2", "finance": "#52c41a", "ops": "#eb2f96",
        "service": "#2f54eb", "vision": "#a0d911", "creative": "#f5222d",
        "veroscholar": "#08979c", "stock_analyst": "#d4b106",
    }

    def esc(s):
        return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")

    # ---------- 指标 ----------
    plugs_sorted = sorted(plugins["items"], key=lambda p: -p["loc"])
    invalid = sum(1 for p in plugins["items"] if not p["manifest_valid"])
    high_risk = sum(1 for r in (hs["files_top"] if hs else []) if r["high_risk"])
    stats = [
        ("后端代码行", f"{ov['total_code_lines']:,}", "#1677ff", "lines"),
        ("前端代码行", f"{fe['overview']['total_code_lines']:,}" if fe else "—", "#13c2c2", "lines"),
        ("核心模块", str(core["module_count"]), "#722ed1", "box"),
        ("业务插件", str(plugins["count"]), "#fa8c16", "box"),
        ("插件路由", str(sum(p["route_count"] for p in plugins["items"])), "#52c41a", "route"),
        ("边界观察项", str(len(inter["boundary_observations"]["violations"])), "#ff4d4f", "warn"),
        ("manifest 待修", str(invalid), "#52c41a" if invalid == 0 else "#ff4d4f", "ok"),
        ("高风险文件", str(high_risk), "#ff4d4f", "fire"),
    ]
    ICONS = {
        "lines": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19L10 13l4 4 6-8"/></svg>',
        "box": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>',
        "route": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h8a2 2 0 012 2v8"/></svg>',
        "warn": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3L2 21h20L12 3z"/><path d="M12 9v5"/><circle cx="12" cy="17" r="1"/></svg>',
        "ok": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>',
        "fire": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2s5 4 5 9a5 5 0 01-10 0c0-1.5.6-3 1.5-4.5C9 8 12 10 12 10s-1-6 0-8z"/></svg>',
    }

    def stat_card(label, val, color, icon):
        return (f'<div class="stat"><div class="st-ic" style="color:{color};background:{color}18">{ICONS[icon]}</div>'
                f'<div><div class="st-val">{val}</div><div class="st-lab">{label}</div></div></div>')

    # ---------- 环形图：agent_role 分布 ----------
    role_cnt = Counter(p.get("agent_role") or "未标注" for p in plugins["items"])
    ROLE_LABELS = {"athena": "Athena", "content": "内容", "business": "业务", "builder": "构建器",
                   "finance": "金融", "ops": "运维", "service": "服务", "vision": "视觉",
                   "creative": "创意", "veroscholar": "学习", "stock_analyst": "投研"}
    def donut():
        total = sum(role_cnt.values())
        r, cx, cy, w = 54, 70, 70, 20
        circ = 2 * 3.14159 * r
        parts, acc = [], 0
        items = sorted(role_cnt.items(), key=lambda kv: -kv[1])
        for role, n in items:
            frac = n / total
            dash = frac * circ
            color = ROLE_COLORS.get(role, "#8c8c8c")
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
                f'stroke-width="{w}" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" '
                f'stroke-dashoffset="{-acc:.2f}" transform="rotate(-90 {cx} {cy})"/>')
            acc += dash
        segs = "".join(parts)
        legend = "".join(
            f'<div class="lg"><i style="background:{ROLE_COLORS.get(k, "#8c8c8c")}"></i>'
            f'<span>{esc(ROLE_LABELS.get(k, k))}</span><b>{v}</b></div>'
            for k, v in items)
        return (f'<div class="donut-box"><svg width="140" height="140" viewBox="0 0 140 140">{segs}'
                f'<text x="70" y="66" text-anchor="middle" font-size="22" font-weight="700">{total}</text>'
                f'<text x="70" y="84" text-anchor="middle" font-size="10" fill="#8c8c8c">插件</text>'
                f'</svg><div class="lg-wrap">{legend}</div></div>')

    # ---------- 热点条形 ----------
    def hbars(rows, valkey="churn", label=""):
        mx = max((r[valkey] for r in rows), default=1)
        out = []
        for r in rows:
            pct = round(r[valkey] / mx * 100)
            risk = r.get("high_risk")
            name = r["file"].split("\\")[-1]
            bar = f'<div class="h-bar" style="width:{pct}%"></div>'
            out.append(
                f'<div class="hrow"><div class="h-name" title="{esc(r["file"])}">{esc(name)}'
                f'{"<span class=pill-red>风险</span>" if risk else ""}</div>'
                f'<div class="h-track">{bar}</div>'
                f'<div class="h-val">{r[valkey]:,}</div></div>')
        return "".join(out)

    # ---------- 核心模块 ----------
    core_rows = "".join(
        f'<tr><td class="mono">{esc(m["name"])}</td><td>{esc((m["description"] or "")[:40])}</td>'
        f'<td class="n">{m["loc"]:,}</td><td class="n">{m["route_count"]}</td><td class="n">{m["py_files"]}</td></tr>'
        for m in core["modules"])

    # ---------- 插件表（角色徽章）----------
    def role_badge(r):
        c = ROLE_COLORS.get(r, "#8c8c8c")
        return f'<span class="rb" style="color:{c};border-color:{c}44;background:{c}14">{esc(r or "—")}</span>'
    plug_rows = "".join(
        f'<tr><td class="mono">{esc(p["identifier"])}</td>'
        f'<td class="dim">{esc(p["version"] or "—")}</td>'
        f'<td>{role_badge(p["agent_role"])}</td>'
        f'<td class="dim">{esc(p["category"] or "—")}</td>'
        f'<td class="n">{p["route_count"]}</td><td class="n">{p["loc"]:,}</td>'
        f'<td class="{"st-ok" if p["manifest_valid"] else "st-bad"}">{p["manifest_valid"] and "合规" or "异常"}</td></tr>'
        for p in plugs_sorted)

    # ---------- 语言 ----------
    lang_mx = max((e["code"] for e in ov["by_language"][:9]), default=1)
    lang_rows = "".join(
        f'<div class="hrow"><div class="h-name">{esc(e["ext"] or "none")}</div>'
        f'<div class="h-track"><div class="h-bar g1" style="width:{round(e["code"] / lang_mx * 100)}%"></div></div>'
        f'<div class="h-val">{e["code"]:,}</div></div>'
        for e in ov["by_language"][:9])

    # ---------- 目录 ----------
    dir_mx = max((e["code"] for e in ov["by_top_dir"][:10]), default=1)
    dir_rows = "".join(
        f'<div class="hrow"><div class="h-name">{esc(e["dir"])}</div>'
        f'<div class="h-track"><div class="h-bar g2" style="width:{round(e["code"] / dir_mx * 100)}%"></div></div>'
        f'<div class="h-val">{e["code"]:,}</div></div>'
        for e in ov["by_top_dir"][:10])

    # ---------- 桌面端 ----------
    fe_blocks = ""
    if fe:
        m = fe["metrics"]
        fe_blocks = (
            f'<div class="card"><div class="c-head"><b>{esc(fe["root"])}</b>'
            f'<span class="rb" style="color:#13c2c2;border-color:#13c2c244;background:#13c2c214">{esc(fe["kind"])}</span></div>'
            f'<p class="dim">{esc(fe["package"].get("description", ""))}</p>'
            f'<div class="fe-g">{fe["overview"]["total_code_lines"]:,} 代码行</div>'
            f'<div class="fe-grid">'
            f'<div class="fe-m"><b>{m["pages"]}</b><span>页面</span></div>'
            f'<div class="fe-m"><b>{m["components"]}</b><span>组件</span></div>'
            f'<div class="fe-m"><b>{m["stores"]}</b><span>store</span></div>'
            f'<div class="fe-m"><b>{m["electron_main_ts"]}</b><span>主进程 TS</span></div>'
            f'<div class="fe-m"><b>{m["tests_unit"] + m["tests_e2e"]}</b><span>测试</span></div>'
            f'<div class="fe-m"><b>{esc("/".join(m["i18n_locales"]))}</b><span>i18n</span></div></div>'
            f'<div class="dim" style="margin-top:.6rem">栈：{esc(" · ".join(fe["stack"][:7]))}</div></div>')

    role_dist = donut()
    dep_mx = max((x["import_count"] for x in inter["plugins_import_core"][:8]), default=1)
    dep_rows = "".join(
        f'<div class="hrow"><div class="h-name mono">{esc(x["module"])}</div>'
        f'<div class="h-track"><div class="h-bar g3" style="width:{round(x["import_count"] / dep_mx * 100)}%"></div></div>'
        f'<div class="h-val">{x["import_count"]}</div></div>'
        for x in inter["plugins_import_core"][:8])
    viol = inter["boundary_observations"]["violations"]
    viol_rows = "".join(
        f'<div class="vio"><code>{esc(v["file"])}</code> → <code>{esc(v["imports"])}</code></div>'
        for v in viol[:8])

    html = f"""<!DOCTYPE html>
    <html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>VeroRun AI 系统仪表盘 · v{ver}</title>
    <style>
    :root{{--bg:#f0f2f5;--card:#fff;--txt:#1f2329;--mut:#646a73;--line:#e5e6eb;--brand:#1677ff}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}}
    .layout{{display:grid;grid-template-columns:216px 1fr;min-height:100vh}}
    .side{{background:#001529;color:rgba(255,255,255,.72);padding:1.1rem .9rem;position:sticky;top:0;height:100vh;overflow:auto}}
    .logo{{color:#fff;font-size:1.02rem;font-weight:700;padding:.3rem .5rem 1rem;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:.8rem}}
    .logo i{{display:inline-block;width:10px;height:10px;background:var(--brand);border-radius:2px;margin-right:.45rem}}
    .nav a{{display:block;color:rgba(255,255,255,.7);text-decoration:none;padding:.42rem .7rem;border-radius:6px;font-size:.86rem;margin:.1rem 0}}
    .nav a:hover{{background:rgba(255,255,255,.09);color:#fff}}
    .nav a.on{{background:var(--brand);color:#fff}}
    .main{{padding:1.3rem 1.5rem 4rem;min-width:0}}
    .head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.1rem}}
    .head h1{{font-size:1.3rem;margin:0}}
    .head .sub{{color:var(--mut);font-size:.76rem}}
    .badge{{font-size:.66rem;background:var(--brand);color:#fff;padding:.08rem .5rem;border-radius:99px;vertical-align:2px;margin-left:.4rem}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.8rem;margin-bottom:1.1rem}}
    .stat{{background:var(--card);border-radius:10px;padding:.8rem .95rem;display:flex;gap:.8rem;align-items:center;box-shadow:0 1px 2px rgba(0,0,0,.04),0 2px 8px rgba(0,0,0,.03)}}
    .st-ic{{width:42px;height:42px;border-radius:10px;display:flex;align-items:center;justify-content:center;flex:none}}
    .st-val{{font-size:1.42rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}}
    .st-lab{{font-size:.74rem;color:var(--mut)}}
    .grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:.9rem;margin-bottom:.9rem}}
    .card{{background:var(--card);border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.04);padding:.95rem 1.05rem;min-width:0}}
    .c-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:.55rem}}
    .c-head b{{font-size:.98rem}}
    .c-sub{{color:var(--mut);font-size:.72rem}}
    .col-12{{grid-column:span 12}}.col-8{{grid-column:span 8}}.col-6{{grid-column:span 6}}.col-4{{grid-column:span 4}}
    @media(max-width:1100px){{.col-8,.col-6,.col-4{{grid-column:span 12}}}}
    @media(max-width:860px){{.layout{{grid-template-columns:1fr}}.side{{position:static;height:auto}}}}
    table{{width:100%;border-collapse:collapse;font-size:.78rem}}
    th{{text-align:left;font-size:.68rem;color:var(--mut);background:#fafafa;padding:.45rem .55rem;border-bottom:1px solid var(--line);font-weight:600}}
    td{{padding:.4rem .55rem;border-bottom:1px solid #f0f1f3;vertical-align:middle}}
    tbody tr:hover{{background:#f7faff}}
    td.mono,.mono{{font-family:Consolas,"SF Mono",monospace;font-size:.76rem;font-weight:600}}
    td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
    .dim{{color:var(--mut)}}
    .rb{{display:inline-block;font-size:.68rem;padding:.02rem .5rem;border-radius:99px;border:1px solid}}
    .st-ok{{color:#00b42a;font-weight:600}}.st-bad{{color:#f53f3f;font-weight:600}}
    .hrow{{display:grid;grid-template-columns:minmax(110px,1.2fr) 2.4fr 64px;gap:.55rem;align-items:center;padding:.33rem 0;border-bottom:1px solid #f4f5f7;font-size:.78rem}}
    .h-name{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .h-track{{background:#f2f3f5;height:12px;border-radius:6px;overflow:hidden}}
    .h-bar{{height:100%;background:linear-gradient(90deg,#3b82f6,#60a5fa);border-radius:6px}}
    .h-bar.g1{{background:linear-gradient(90deg,#00b42a,#7be188)}}
    .h-bar.g2{{background:linear-gradient(90deg,#f59e0b,#fbbf24)}}
    .h-bar.g3{{background:linear-gradient(90deg,#8b5cf6,#a78bfa)}}
    .h-val{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
    .pill-red{{display:inline-block;font-size:.58rem;color:#f53f3f;background:#f53f3f14;border:1px solid #f53f3f33;border-radius:99px;padding:0 .32rem;margin-left:.35rem;vertical-align:1px}}
    .donut-box{{display:flex;gap:1.2rem;align-items:center;flex-wrap:wrap}}
    .lg-wrap{{display:flex;flex-direction:column;gap:.22rem;font-size:.74rem}}
    .lg{{display:grid;grid-template-columns:10px 1fr auto;gap:.45rem;align-items:center}}
    .lg i{{width:8px;height:8px;border-radius:2px;display:inline-block}}
    .lg b{{font-variant-numeric:tabular-nums;color:var(--mut)}}
    .fe-grid{{display:flex;flex-wrap:wrap;gap:.9rem;margin-top:.7rem}}
    .fe-m{{min-width:64px;background:#fafafa;border:1px solid var(--line);border-radius:8px;padding:.35rem .6rem;text-align:center}}
    .fe-m b{{display:block;font-size:1.1rem}}
    .fe-m span{{font-size:.66rem;color:var(--mut)}}
    .vio{{font-size:.74rem;padding:.28rem 0;border-bottom:1px dashed #f0f1f3}}
    .vio code{{color:#f53f3f}}
    code{{background:#f2f3f5;padding:.05rem .35rem;border-radius:4px;font-family:Consolas,monospace;font-size:.92em}}
    .foot{{color:var(--mut);font-size:.72rem;margin-top:1.4rem;display:flex;justify-content:space-between}}
    </style></head><body>
    <div class="layout">
    <aside class="side">
    <div class="logo"><i></i>RepoLucent</div>
    <div class="nav">
    <a class="on" href="#top">概览</a><a href="#core">系统核心</a><a href="#plugins">业务插件</a>
    <a href="#hot">变更热点</a><a href="#stat">代码量统计</a><a href="#arch">架构边界</a><a href="#fe">桌面端</a>
    </div>
    <div style="margin-top:1rem;font-size:.68rem;opacity:.5;padding:0 .5rem">RepoLucent v{ver}<br>{esc(gen)}</div>
    </aside>
    <main class="main" id="top">
    <div class="head"><div><h1>仓库架构仪表盘<span class="badge">v{ver}</span></h1>
    <div class="sub">主仓库 ＋ 前端/扩展仓库 · 数据由 RepoLucent 自动采集 · 本地辅助工具，不入 Git</div></div></div>

    <div class="stats">{''.join(stat_card(l, v, c, ic) for l, v, c, ic in stats)}</div>

    <div class="grid">
    <section class="card col-4"><div class="c-head"><b>插件角色分布</b><span class="c-sub">{plugins['count']} 插件 · agent_role</span></div>{role_dist}</section>
    <section class="card col-8"><div class="c-head"><b>核心模块</b><span class="c-sub">平台引擎 · 代码行/路由/.py</span></div>
    <div style="max-height:320px;overflow:auto"><table><thead><tr><th>模块</th><th>职责</th><th class="n">代码行</th><th class="n">路由</th><th class="n">.py</th></tr></thead><tbody>{core_rows}</tbody></table></div></section>
    </div>

    <section class="card" style="margin-bottom:.9rem" id="plugins"><div class="c-head"><b>业务插件全览</b><span class="c-sub">{plugins['count']} 个 · 按代码量降序 · 点击角色徽章区分能力域</span></div>
    <div style="max-height:420px;overflow:auto"><table><thead><tr><th>插件</th><th>版本</th><th>角色</th><th>分类</th><th class="n">路由</th><th class="n">LOC</th><th>清单</th></tr></thead><tbody>{plug_rows}</tbody></table></div></section>

    <div class="grid" id="hot">
    <section class="card col-12"><div class="c-head"><b>变更热点</b><span class="c-sub">近 {hs['days']} 天 · 改动次数（红色=高风险带，重构优先）</span></div>
    {hbars(hs['files_top'][:12]) if hs else '<div class="dim">git 不可用</div>'}</section>
    </div>

    <div class="grid" id="stat">
    <section class="card col-6"><div class="c-head"><b>代码量 · 顶层目录</b></div>{dir_rows}</section>
    <section class="card col-6"><div class="c-head"><b>代码量 · 语言</b></div>{lang_rows}</section>
    </div>

    <div class="grid" id="arch">
    <section class="card col-6"><div class="c-head"><b>插件依赖核心排名</b><span class="c-sub">import 次数</span></div>{dep_rows}
    <div class="c-sub" style="margin-top:.7rem">核心直连插件（边界观察项 {len(viol)} 处）</div>{viol_rows}</section>
    <section class="card col-6"><div class="c-head"><b>健康状态</b></div>
    <div style="display:grid;gap:.5rem">
    <div class="hrow"><div class="h-name">manifest 校验</div><div class="h-track"><div class="h-bar g1" style="width:100%"></div></div><div class="h-val" style="color:#00b42a">{plugins['count'] - invalid}/{plugins['count']} 合规</div></div>
    <div class="hrow"><div class="h-name">边界红线</div><div class="h-track"><div class="h-bar" style="width:{min(100, len(viol) * 4)}%;background:linear-gradient(90deg,#f53f3f,#ff7875)"></div></div><div class="h-val" style="color:#f53f3f">{len(viol)} 处</div></div>
    <div class="hrow"><div class="h-name">单文件超 2000 行</div><div class="h-track"><div class="h-bar" style="width:{min(100, high_risk * 8)}%;background:linear-gradient(90deg,#f59e0b,#fbbf24)"></div></div><div class="h-val" style="color:#f59e0b">{high_risk}</div></div>
    </div>
    <div class="dim" style="margin-top:.7rem;font-size:.74rem">门禁建议：先用 --fail-on-warn 灰度，修复到基线后再转硬门禁。</div></section>
    </div>

    <div class="grid" id="fe">
    <section class="card col-12"><div class="c-head"><b>桌面端仓库</b><span class="c-sub">Electron · React · TS</span></div>
    {fe_blocks if fe_blocks else '<div class="dim">未发现前端仓库</div>'}</section>
    </div>

    <div class="foot"><span>完整能力：控制台 http://127.0.0.1:8788 · 报告 repo_lucent_report.html · AI 上下文 AI_CONTEXT.md</span><span>RepoLucent v{ver} · schema {d['meta']['schema_version']}</span></div>
    </main></div></body></html>"""
    return html
