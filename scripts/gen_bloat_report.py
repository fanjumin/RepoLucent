# -*- coding: utf-8 -*-
"""生成《代码膨胀归因报告》md + html 到工具 out/ 目录。"""
from pathlib import Path

OUT = Path(r"D:\projects\verorun-code\tools\dev_insight\out")

MD = """# VeroRun 代码膨胀归因报告（2026-09-09）

> 分析口径：代码类扩展名 .py/.js/.ts/.vue/.html/.css/.sh/.sql；排除 docs/、本地调试脚本、临时文件、md/yml/json 资产。
> 数据来源：git 历史（HEAD=d62e74e5 之前 30 天对比）+ 当前代码构成扫描。

## 0. 结论

1. **膨胀是真实的**：当前代码总量 218,227 行中，**近 30 天（8/10→9/9）净增 88,313 行（+40%）**，新增 107,121 / 删除 18,808。
2. **高度集中**：增量 Top 6 来源 = stock_analysis(+14,770)、site_builder(+10,501)、plugin_manager(+9,536)、veroscholar(+6,906)、mini_app_builder(+5,237)、im_gateway(+4,809)，合计约 **+5.2 万行，占近 30 天净增 59%**。
3. **结构成因**（为什么总量/HTML 这么大）：
   - 巨型单文件聚集：plugin_manager/routes.py 3,867 行、auth-center/models/database.py 2,476、deploy/lib/common.sh 2,132、agent_matrix/routes.py 1,814；
   - HTML 模板体积大且内联 JS/CSS：HTML 共 44,145 行（占 20%），admin 53 个模板文件 + main_site 35 个 + 各插件 templates，单文件可达 1,700+ 行；
   - 第三方/整页静态资源入库：static/lib/quill.snow.css（近 30 天 +839）、static/css/landing.css（+820）；
   - 核心框架同步扩张：plugin_manager 近 30 天 +9,536（routes.py +3,251、manager.py +908、subscription.py +569）。

## 1. 近 30 天净增（按目录/插件，Top 20）

| 来源 | 净增行 | 说明 |
|---|---:|---|
| plugins/stock_analysis | +14,770 | 股票分析：routes.py +1,464 / models_sa +952 / stock_skill +829 |
| plugins/site_builder | +10,501 | 建站：admin html +885 / block_schemas +664 / sb-blocks.js +583 |
| plugin_manager（核心框架） | +9,536 | routes.py +3,251 / manager.py +908 / subscription.py +569 |
| plugins/veroscholar | +6,906 | 学习插件：routes +845 / models +683 / workflow +592 |
| plugins/mini_app_builder | +5,237 | 小程序生成 |
| plugins/im_gateway | +4,809 | 网关：admin_imgateway.html +818 |
| plugins/risk_control | +4,254 | 风控：routes.py +779 |
| plugins/ros_bridge | +3,558 | 新插件：runtime +660 / routes +577 / models +571 |
| deploy | +3,757 | common.sh +1,122 |
| plugins/iot_hub | +3,233 | 新插件：broker.py +619 |
| plugins/payment | +2,789 | |
| plugins/project_workspace | +2,581 | |
| static | +1,781 | quill.snow.css +839（第三方）/ landing.css +820 |
| plugins/visitor_profile | +2,519 | |
| agent_matrix | +1,608 | |
| sdks | +1,425 | |
| plugins/memory_engine | +2,263 | |
| plugins/social_push | +1,883 | |
| plugins/cogevolution_substrate | +1,846 | |
| plugins/two_factor_auth | +1,771 | |

## 2. 当前代码构成（总量 218,227 行）

| 语言 | 行数 | 占比 | 主要位置 |
|---|---:|---:|---|
| Python | 141,308 | 64.8% | plugins 93k / auth-center 19.9k / plugin_manager 17.4k |
| HTML 模板 | 44,145 | 20.2% | admin 20.7k / main_site 15.9k / plugins templates |
| JavaScript | 15,301 | 7.0% | plugins 静态 js / admin |
| CSS | 11,144 | 5.1% | static / admin / main_site |
| Shell | 4,217 | 1.9% | deploy |
| SQL/其他 | 2,112 | 1.0% | — |

## 3. 巨型文件 TOP 10（可维护性风险点）

| 文件 | 行数 |
|---|---:|
| plugins/../i18n/en.yml（资产，不计） | 4,220（资产） |
| plugin_manager/routes.py | 3,867 |
| auth-center/models/database.py | 2,476 |
| deploy/lib/common.sh | 2,132 |
| agent_matrix/routes.py | 1,814 |
| plugin_manager/manager.py | 1,739 |
| main_site/templates/index.html | 1,723 |
| plugins/analytics/analytics-dashboard.js | 1,649 |
| plugins/shop/routes/admin.py | 1,586 |
| plugins/health_check/checkers.py | 1,576 |

## 4. 解读与建议

- **业务驱动为主，失控为辅**：新插件（ros_bridge / iot_hub / memory_engine / veroscholar）成批落地 + 大插件迭代（stock_analysis / site_builder），属平台扩张的正常产物；但**单文件巨型化**与**HTML 内联膨胀**是需要治理的结构问题。
- 建议 1：拆分巨型文件——plugin_manager/routes.py(3.9k)、auth-center/database.py(2.5k)、manager.py(1.7k) 按域拆包；
- 建议 2：HTML 模板瘦身——admin/main_site 模板体系将内联 JS/CSS 外提为静态资源，复用 partial；
- 建议 3：第三方/整页静态资源（quill 等）改为包管理引入并明确 vendor 目录，统计口径单独核算；
- 建议 4：新插件设行数红线与复用门槛（plugins/_base 辅助、模板库），每月用本工具 --plugin/--module + 全量报告做一次增量体检。

*本地工具产物，不进入 Git 版本控制。*
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VeroRun 代码膨胀归因报告</title>
<style>
body{margin:0;background:#fff;color:#2b2b2b;font-family:-apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.7;font-size:15px}
.wrap{max-width:46rem;margin:0 auto;padding:2rem 1.25rem 4rem}
h1{font-size:1.7rem;margin:0 0 .2rem;color:#111}
.sub{color:#666;font-size:.85rem;margin-bottom:1.6rem}
h2{font-size:1.15rem;margin:2.2rem 0 .6rem;padding-top:.8rem;border-top:1px solid #ddd}
table{width:100%;border-collapse:collapse;font-size:.85rem;margin:.6rem 0}
th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid #eee;vertical-align:top}
th{color:#111}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{background:#f4f4f4;padding:.1em .35em;border-radius:3px;font-size:.85em}
.note{background:#fff8e6;border-left:3px solid #e6a817;padding:.6rem .9rem;margin:1rem 0;font-size:.88rem}
.muted{color:#666;font-size:.8rem}
</style></head><body><div class="wrap">
<h1>VeroRun 代码膨胀归因报告</h1>
<div class="sub">2026-09-09 · 口径：代码类扩展名（py/js/ts/vue/html/css/sh/sql）· 排除 docs/、本地脚本、资产文件</div>

<h2>核心结论</h2>
<div class="note"><b>膨胀是真实的，且高度集中。</b>当前 218,227 行中，<b>近 30 天净增 88,313 行（+40%）</b>（新增 107,121 − 删除 18,808）。
Top 6 来源（stock_analysis +14,770 · site_builder +10,501 · plugin_manager +9,536 · veroscholar +6,906 · mini_app_builder +5,237 · im_gateway +4,809）合计约占 59%。</div>

<h2>近 30 天净增 Top 15</h2>
<table><tr><th>来源</th><th class="num">净增行</th></tr>
<tr><td>plugins/stock_analysis</td><td class="num">+14,770</td></tr>
<tr><td>plugins/site_builder</td><td class="num">+10,501</td></tr>
<tr><td>plugin_manager（核心框架）</td><td class="num">+9,536</td></tr>
<tr><td>plugins/veroscholar</td><td class="num">+6,906</td></tr>
<tr><td>plugins/mini_app_builder</td><td class="num">+5,237</td></tr>
<tr><td>plugins/im_gateway</td><td class="num">+4,809</td></tr>
<tr><td>plugins/risk_control</td><td class="num">+4,254</td></tr>
<tr><td>deploy（common.sh 等）</td><td class="num">+3,757</td></tr>
<tr><td>plugins/ros_bridge（新插件）</td><td class="num">+3,558</td></tr>
<tr><td>plugins/iot_hub（新插件）</td><td class="num">+3,233</td></tr>
<tr><td>plugins/payment</td><td class="num">+2,789</td></tr>
<tr><td>plugins/project_workspace</td><td class="num">+2,581</td></tr>
<tr><td>plugins/visitor_profile</td><td class="num">+2,519</td></tr>
<tr><td>plugins/memory_engine</td><td class="num">+2,263</td></tr>
<tr><td>static（quill/landing css）</td><td class="num">+1,781</td></tr>
</table>

<h2>当前构成（总量 218,227）</h2>
<table><tr><th>语言</th><th class="num">行数</th><th class="num">占比</th></tr>
<tr><td>Python</td><td class="num">141,308</td><td class="num">64.8%</td></tr>
<tr><td>HTML 模板（内联 JS/CSS 多）</td><td class="num">44,145</td><td class="num">20.2%</td></tr>
<tr><td>JavaScript</td><td class="num">15,301</td><td class="num">7.0%</td></tr>
<tr><td>CSS（含第三方 quill 等）</td><td class="num">11,144</td><td class="num">5.1%</td></tr>
<tr><td>Shell / SQL / 其他</td><td class="num">6,329</td><td class="num">2.9%</td></tr>
</table>

<h2>巨型文件 TOP（治理对象）</h2>
<table><tr><th>文件</th><th class="num">行数</th></tr>
<tr><td><code>plugin_manager/routes.py</code></td><td class="num">3,867</td></tr>
<tr><td><code>auth-center/models/database.py</code></td><td class="num">2,476</td></tr>
<tr><td><code>deploy/lib/common.sh</code></td><td class="num">2,132</td></tr>
<tr><td><code>agent_matrix/routes.py</code></td><td class="num">1,814</td></tr>
<tr><td><code>plugin_manager/manager.py</code></td><td class="num">1,739</td></tr>
<tr><td><code>main_site/templates/index.html</code></td><td class="num">1,723</td></tr>
<tr><td><code>plugins/analytics/analytics-dashboard.js</code></td><td class="num">1,649</td></tr>
<tr><td><code>plugins/shop/routes/admin.py</code></td><td class="num">1,586</td></tr>
</table>

<h2>治理建议</h2>
<p>① 拆分巨型文件（routes.py 3.9k / database.py 2.5k / manager.py 1.7k 按域拆包）；
② HTML 模板瘦身：内联 JS/CSS 外提，partial 复用（admin 53 个模板、单文件 1.7k 行）；
③ 第三方静态资源（quill 等）改包管理引入，vendor 单独核算；
④ 新插件设行数红线 + 复用 <code>plugins/_base</code>；每月用 dev-insight 做一次增量体检。</p>

<p class="muted">本地工具产物，不进入 Git 版本控制。</p>
</div></body></html>"""

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "代码膨胀归因报告.md").write_text(MD, encoding="utf-8")
(OUT / "代码膨胀归因报告.html").write_text(HTML, encoding="utf-8")
print("written:", OUT)
