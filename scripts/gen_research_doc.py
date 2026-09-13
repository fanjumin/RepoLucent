# -*- coding: utf-8 -*-
"""生成《同类工具调研与 v1.5 增强》交付文档（md + html）。"""
from pathlib import Path

OUT = Path(r"D:\projects\verorun-code\tools\dev_insight\out")

MD = """# GitHub 同类工具调研与 dev-insight v1.5 增强

> 2026-09-09 · 调研范围：GitHub 上代码架构分析 / 依赖可视化 / LLM 上下文打包 / 代码历史挖掘四类工具

## 一、调研结论：没有"同款"，但有四个可借鉴的强方向

dev-insight 的定位（仓库级架构洞察 + AI 上下文 + 变更追踪 + 门禁）没有现成同款；
真正成熟且值得借鉴的工具分布在四个方向，各有独门绝技：

| 方向 | 代表工具 | 独门绝技 | 我们的对应/差距 |
|---|---|---|---|
| 依赖图可视化 | [madge](https://github.com/pahen/madge)（JS 生态）· [dependency-cruiser](https://github.com/sverweij/dependency-cruiser)（规则化依赖校验）· skott | 文件级依赖图 + 环检测 + 可视化；cruiser 还能用规则禁止"不该有的依赖" | 已有插件级环检测（门禁 plugin-cycle）；**缺可视化出图** → v1.5 已补 Mermaid 导出 |
| 代码历史挖掘 | [code-maat](https://github.com/adamtornhill/code-maat)（Adam Tornhill/CodeScene 作者）· scc | **hotspot 分析法**：churn（改动次数）× 体量 → 找"最该重构"的文件；比纯 LOC 更有行动价值 | **缺历史维度** → v1.5 已补「变更热点」分析 |
| 复杂度/规模 | [scc](https://github.com/boyter/scc)（Rust 超快）· [lizard](https://github.com/terryyin/lizard)（15 语言圈复杂度）· radon | 圈复杂度逐函数定位 + COCOMO 估算 | 有 file-too-large 门禁；**缺函数级复杂度** → 阶段 F 候选 |
| LLM 上下文打包 | [repomix](https://github.com/yamadashy/repomix)（打包整仓为单文件喂 LLM）· gitingest · aider repo-map（tree-sitter 生成符号地图） | 整仓打包 + token 估算 + 提示词模板；repo-map 只发符号索引省 90% token | 已有 AI_CONTEXT.md + context 切片 + query；**缺"单插件源码整包"与 token 估算** → 阶段 F 候选 |

## 二、v1.5.0 新增功能（已实测）

### 1. 变更热点分析（借鉴 code-maat / CodeScene 方法论）

近 90 天 git churn × 当前体量 → 高风险文件带 + 插件/模块聚合。

真实仓库实测（你机器上现在就能看）：

| 高风险文件（churn≥5 且 LOC≥800） | churn | LOC |
|---|---:|---:|
| admin/app.py | 129 | 1,123 |
| auth-center/models/database.py | 111 | 2,476 |
| deploy/lib/common.sh | 82 | 2,132 |
| plugin_manager/routes.py | 64 | 3,867 |
| plugin_manager/manager.py | 52 | 1,739 |

组级 churn TOP：admin 777 · auth-center 556 · deploy 371 · main_site 322 · plugin_manager 302。
结论与之前的膨胀归因互相印证：**重构优先级 = 上面这 5 个文件**。

### 2. 依赖图 Mermaid 导出（借鉴 madge / dependency-cruiser）

插件→核心（组级聚合边）与插件→插件（真实依赖边）自动出图，
报告 MD 中以 ```mermaid 代码块呈现，GitHub / Typora / VS Code 均可直接渲染。

## 三、下一步候选（按 ROI 排序）

1. 函数级圈复杂度（lizard 思路）→ 定位到"具体哪个函数最该拆"；
2. `pack --plugin shop` 单插件源码整包 + token 估算（repomix 思路）→ 给 AI 改代码用；
3. 依赖规则引擎（dependency-cruiser 思路）→ "禁止 payment 依赖 analytics"这类架构纪律可执行化；
4. 热点图（scc 的 churn-LOC 散点/热力图）→ 控制台可视化。

*本地工具产物，不进入 Git。*
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>同类工具调研与 v1.5 增强</title>
<style>
body{margin:0;background:#fff;color:#2b2b2b;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.7;font-size:15px}
.wrap{max-width:46rem;margin:0 auto;padding:2rem 1.25rem 4rem}
h1{font-size:1.6rem;margin:0 0 .2rem;color:#111}
.sub{color:#666;font-size:.85rem;margin-bottom:1.4rem}
h2{font-size:1.15rem;margin:2rem 0 .6rem;padding-top:.8rem;border-top:1px solid #ddd}
h3{font-size:1rem;margin:1.2rem 0 .4rem}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin:.6rem 0}
th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid #eee;vertical-align:top}
th{color:#111}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{background:#f4f4f4;padding:.1em .35em;border-radius:3px;font-size:.85em}
a{color:#0000ee}
.note{background:#fff8e6;border-left:3px solid #e6a817;padding:.6rem .9rem;margin:1rem 0;font-size:.88rem}
.muted{color:#666;font-size:.8rem}
</style></head><body><div class="wrap">
<h1>GitHub 同类工具调研与 v1.5 增强</h1>
<div class="sub">2026-09-09 · 四类工具方向 · 两个新能力已落地实测</div>

<h2>调研结论：没有"同款"，但有四个可借鉴的强方向</h2>
<table>
<tr><th>方向</th><th>代表工具</th><th>独门绝技</th><th>我们的差距 / 处理</th></tr>
<tr><td>依赖图</td><td><a href="https://github.com/pahen/madge">madge</a> · <a href="https://github.com/sverweij/dependency-cruiser">dependency-cruiser</a> · skott</td><td>文件级依赖图 + 规则化依赖校验</td><td>环检测已有；<b>缺可视化出图 → v1.5 已补 Mermaid</b></td></tr>
<tr><td>历史挖掘</td><td><a href="https://github.com/adamtornhill/code-maat">code-maat</a> · scc</td><td>hotspot：churn×体量找重构对象</td><td><b>缺历史维度 → v1.5 已补变更热点</b></td></tr>
<tr><td>复杂度</td><td><a href="https://github.com/boyter/scc">scc</a> · lizard · radon</td><td>圈复杂度逐函数 + COCOMO</td><td>file-too-large 已有；函数级复杂度 → 阶段 F</td></tr>
<tr><td>LLM 上下文</td><td><a href="https://github.com/yamadashy/repomix">repomix</a> · gitingest · aider repo-map</td><td>整仓打包 + token 估算 + 符号地图</td><td>AI_CONTEXT/切片/query 已有；源码整包 → 阶段 F</td></tr>
</table>

<h2>v1.5.0 新增（已实测）</h2>
<h3>1. 变更热点分析（code-maat 方法论）</h3>
<div class="note"><b>高风险文件带</b>（近 90 天 churn≥5 且 LOC≥800）：admin/app.py（129 次/1,123 行）· auth-center/models/database.py（111/2,476）· deploy/lib/common.sh（82/2,132）· plugin_manager/routes.py（64/3,867）· plugin_manager/manager.py（52/1,739）——重构优先级即此清单，与膨胀归因互相印证。</div>
<p>组级 churn TOP：admin 777 · auth-center 556 · deploy 371 · main_site 322 · plugin_manager 302。</p>
<h3>2. 依赖图 Mermaid 导出（madge 风格）</h3>
<p>插件→核心（组级聚合边）与插件→插件真实依赖边自动出图，报告 MD 内嵌 <code>mermaid</code> 代码块，GitHub/Typora/VS Code 直接渲染。</p>

<h2>下一步候选（按 ROI）</h2>
<p>① 函数级圈复杂度（lizard 思路）→ 定位"具体哪个函数最该拆"；② <code>pack --plugin</code> 单插件源码整包 + token 估算（repomix 思路）；③ 依赖规则引擎（dependency-cruiser 思路，让"禁依赖"纪律可执行）；④ 热点散点图进控制台。</p>
<p class="muted">本地工具产物，不进入 Git。控制台：http://127.0.0.1:8788</p>
</div></body></html>"""

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "同类工具调研与v1.5增强.md").write_text(MD, encoding="utf-8")
(OUT / "同类工具调研与v1.5增强.html").write_text(HTML, encoding="utf-8")
print("调研交付文档已生成")
