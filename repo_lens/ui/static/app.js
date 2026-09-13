// RepoLens · 控制台前端（P1a UI 侧栏化）
// 结构：V1 设计系统外壳（.scr > .tbar/.hdr/.sbody[.sider+.smain]/.sbar）
//       + 侧栏导航 + hash 路由 + VIEWS 视图注册表。
// 所有视图共享同一套 /api/* 端点（与命令行 / MCP 同源），仅作界面重组，行为不变。
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const ic = window.VRIcon || (() => '');

  // ---- 基础工具 ----
  function _authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (window.__REPOLENS_TOKEN) h['X-RepoLens-Token'] = window.__REPOLENS_TOKEN;
    return h;
  }
  async function api(path, opt) {
    const r = await fetch(path, Object.assign({}, opt || {}, { headers: _authHeaders(opt && opt.headers) }));
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) { j = null; }
    if (r.status === 401) { msg('令牌无效或已过期，请刷新页面重新获取'); throw new Error('令牌无效或已过期，请刷新页面'); }
    if (!r.ok) { throw new Error(j && j.error ? j.error.message : t); }
    return j;
  }
  function esc(x) { return String(x == null ? '' : x).replace(/</g, '&lt;'); }
  // 跨视图安全：按钮可能不在当前视图 DOM 中
  function busy(b) {
    const el = $('busy'); if (el) el.style.display = b ? 'inline' : 'none';
    ['btnAnalyze', 'btnSnapshot', 'btnDiff', 'btnGate', 'btnAi', 'btnSummary'].forEach(x => {
      const e = $(x); if (e) e.disabled = b;
    });
  }
  let _to = null;
  function msg(t, isErr = true) {
    const el = $('errBox'); if (!el) return;
    el.innerHTML = '<span class="' + (isErr ? 'err' : 'hint') + '">' + (isErr ? '⚠ ' : '✓ ') + esc(t) + '</span>';
    clearTimeout(_to); _to = setTimeout(() => { el.innerHTML = ''; }, 8000);
  }

  // ---- 侧栏与路由 ----
  const NAV = [
    ['分析', [['dashboard', '仪表盘', 'grid'], ['query', '结构化查询', 'filter'], ['gate', '架构门禁', 'shield']]],
    ['资产', [['scripts', '脚本库', 'puzzle'], ['git', '版本控制', 'git'], ['audit', '代码审计', 'shieldok']]],
    ['系统', [['settings', '设置', 'gear']]]
  ];

  function renderNav(active) {
    let out = '';
    NAV.forEach(g => {
      out += '<div class="ng">' + g[0] + '</div>';
      g[1].forEach(it => {
        out += '<div class="ni' + (it[0] === active ? ' on' : '') + '" data-view="' + it[0] + '">' +
          ic(it[2], 15) + '<span>' + it[1] + '</span></div>';
      });
    });
    out += '<div class="sfoot">' + ic('chevr', 11) + ' 收起导航</div>';
    const sider = $('sider');
    sider.innerHTML = out;
    sider.querySelectorAll('.ni').forEach(n => {
      n.addEventListener('click', () => { location.hash = '#/' + n.getAttribute('data-view'); });
    });
  }

  function currentView() {
    const h = (location.hash || '').replace(/^#\/?/, '');
    const ids = NAV.flatMap(g => g[1].map(i => i[0]));
    return ids.indexOf(h) >= 0 ? h : 'dashboard';
  }

  // ---- 视图注册表 ----
  const VIEWS = {
    dashboard() {
      return {
        html:
          '<section class="card"><div class="chead"><span class="ctitle">主仓库指标</span>' +
          '<span class="csub">实时</span></div><div class="cbody">' +
          '<div class="grid" id="metrics"><div class="metric"><b>—</b><span>等待数据</span></div></div>' +
          '<div class="toolbar">' +
          '<button class="btn btn-pri" id="btnAnalyze" onclick="VRApp.run()">运行完整分析</button>' +
          '<button class="btn" id="btnSnapshot" onclick="VRApp.snapshot()">生成快照</button>' +
          '<button class="btn" id="btnDiff" onclick="VRApp.diff()">与基线对比</button>' +
          '<button class="btn" id="btnGate" onclick="VRApp.gate()">架构门禁</button>' +
          '<button class="btn" id="btnAi" onclick="VRApp.ai()">AI 上下文</button>' +
          '<button class="btn" id="btnSummary" onclick="VRApp.summary()">刷新指标</button>' +
          '<label class="auto"><input type="checkbox" id="autoRefresh" onchange="VRApp.toggleAuto(this)">自动刷新 15s</label>' +
          '</div>' +
          '<div class="hint">基线：<select id="baseList"></select>（先「生成快照」产生基线，再「与基线对比」）· 代码变化后任意按钮都会重新取数</div>' +
          '</div></section>' +
          '<div id="panels"></div>' +
          '<div id="preview"></div>',
        mount() { summary(); loadData(); }
      };
    },
    query() {
      return {
        html:
          '<div class="phead"><div><div class="h1">结构化查询</div><div class="psub">对后端仓库：范围 plugins/core · 字段支持 a.b 与 list[].field · 条件 key=value,key!=value（逗号分隔取 AND）</div></div></div>' +
          '<section class="card"><div class="cbody"><div class="querybar">' +
          '<select id="qScope"><option value="plugins">plugins</option><option value="core">core</option></select>' +
          '<input type="text" id="qSelect" placeholder="identifier,version,agent_role,routes">' +
          '<input type="text" id="qWhere" placeholder="agent_role=business">' +
          '<button class="btn btn-pri" onclick="VRApp.query()">查询</button></div>' +
          '<div id="qOut"></div></div></section>',
        mount() {}
      };
    },
    git() {
      return {
        html:
          '<div class="phead"><div><div class="h1">版本控制</div><div class="psub">Git 操作默认 dry-run，需确认才执行真实操作</div></div></div>' +
          '<section class="card"><div class="cbody"><div class="toolbar">' +
          '<button class="btn" onclick="VRApp.gitLog()">提交历史</button>' +
          '<button class="btn" onclick="VRApp.gitRemoteDiff()">远程差异</button>' +
          '<button class="btn" onclick="VRApp.gitUntracked()">未跟踪文件</button>' +
          '<button class="btn" onclick="VRApp.gitCiCheck()">CI 配置检查</button>' +
          '<button class="btn warn" onclick="VRApp.gitPush()">推送</button>' +
          '<button class="btn warn" onclick="VRApp.gitPull()">拉取</button>' +
          '<button class="btn warn" onclick="VRApp.gitSync()">多仓库同步</button>' +
          '</div><div class="hint">所有结果展示在下方 · 推送/拉取/同步默认仅预检</div>' +
          '<div id="preview"></div></div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">仓库组</span>' +
          '<span class="cact"><button class="btn" onclick="VRApp.groupsLoad()">刷新组</button></span></div>' +
          '<div class="cbody">' +
          '<div class="toolbar">组名 <input type="text" id="grpName" placeholder="group" style="width:8rem"> ' +
          '仓库路径 <input type="text" id="grpRepo" placeholder="D:\\path\\to\\repo" style="width:20rem"> ' +
          '<button class="btn" onclick="VRApp.groupAdd(false)">添加预检</button>' +
          '<button class="btn warn" onclick="VRApp.groupAdd(true)">确认添加</button>' +
          '<button class="btn warn" onclick="VRApp.groupRemove(true)">移除</button></div>' +
          '<div class="toolbar">批量只读命令于组：<select id="batchCmd"><option value="log">log</option><option value="remote-diff">remote-diff</option><option value="untracked">untracked</option></select> ' +
          '组 <select id="batchGroup"></select> ' +
          '<button class="btn" onclick="VRApp.batchRun()">执行 batch</button></div>' +
          '<div id="groupsOut"></div></div></section>',
        mount() { groupsLoad(); }
      };
    },
    scripts() {
      return {
        html:
          '<div class="phead"><div><div class="h1">脚本资产库</div><div class="psub">只读展示 registry.json；active 且 kind=tool 的脚本可 dry-run / 受控执行</div></div>' +
          '<div class="pact"><button class="btn btn-pri" onclick="VRApp.loadScripts()">加载脚本库</button>' +
          '<button class="btn" onclick="VRApp.scriptDoctor()">自检 doctor</button>' +
          '<button class="btn" onclick="VRApp.scriptManual()">使用手册</button>' +
          '<button class="btn" onclick="VRApp.scriptBaseline()">存基线</button>' +
          '<button class="btn" onclick="VRApp.scriptBaselineDiff()">基线对比</button></div></div>' +
          '<div id="preview"></div>',
        mount() { loadScripts(); }
      };
    },
    audit() {
      return {
        html:
          '<div class="phead"><div><div class="h1">代码审计</div><div class="psub">只读分析当前 serve 指向的仓库；语义层为编排-判定分离</div></div></div>' +
          '<section class="card"><div class="cbody"><div class="toolbar">阈值 ' +
          '<select id="auditFailOn" style="padding:.3rem"><option value="blocking">blocking</option><option value="major">major</option><option value="minor">minor</option><option value="info">info</option></select>' +
          '<button class="btn" onclick="VRApp.auditPreview()">审计预览(dry-run)</button>' +
          '<button class="btn warn" onclick="VRApp.auditRun()">执行审计(confirm)</button>' +
          '<button class="btn" onclick="VRApp.auditSemPrompt()">导出语义任务</button></div>' +
          '<textarea id="semResults" rows="3" style="width:100%;padding:.4rem;font-family:monospace;font-size:.8rem;background:var(--spanel2);color:var(--sink);border:1px solid var(--sline2);border-radius:8px" placeholder=\'可选：回灌外部 AI Agent 的语义结果 JSON，如 {"findings":[{"rule_id":"AIB002","file":"...","line":17,"why":"..."}]}；留空则纯确定性审计\'></textarea>' +
          '<div class="toolbar" style="margin-top:.5rem"><label style="margin-right:.8rem"><input type="checkbox" id="semCanBlock"> 允许语义发现纳入阻断</label>' +
          '<label style="margin-right:.8rem"><input type="checkbox" id="withLlm"> 内置 LLM 语义通道（settings.models 配置，失败自动降级）</label>' +
          '<input type="text" id="llmModel" placeholder="模型名（默认 default_model）" style="width:14rem;margin-right:.6rem">' +
          '<label style="margin-right:.8rem"><input type="checkbox" id="llmNoTools"> 禁用工具回环</label>' +
          '<button class="btn" onclick="VRApp.auditRunWithSemantic()">带语义执行</button></div>' +
          '<div class="hint">confirm=false 仅预览；语义结果默认仅咨询、勾选才影响放行</div>' +
          '<div id="preview"></div></div></section>',
        mount() {}
      };
    },
    settings() {
      return {
        html:
          '<div class="phead"><div><div class="h1">设置</div><div class="psub">配置外部化（settings.json 四级搜索链合并；密钥仅来自环境变量，此处绝不回显）</div></div></div>' +
          '<section class="card"><div class="chead"><span class="ctitle">生效配置</span>' +
          '<span class="cact"><button class="btn" onclick="VRApp.loadSettings()">刷新配置</button></span></div>' +
          '<div class="cbody"><div id="cfgOut" class="hint">加载中…</div></div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">LLM 可视化配置（密钥本体永不落盘，只写环境变量名）</span></div>' +
          '<div class="cbody">' +
          '<div class="toolbar"><label><input type="checkbox" id="llmEnabled"> 启用 LLM 集成</label>' +
          '默认模型 <input type="text" id="llmDefault" placeholder="如 qwen-plus" style="width:10rem"></div>' +
          '<div class="toolbar" style="align-items:flex-start">models JSON <textarea id="llmModels" rows="5" style="width:60%;font-family:monospace;font-size:.78rem" placeholder=\'[{"name":"qwen-plus","provider":"openai_compat","model":"qwen-plus","key_env":"REPO_LENS_DASHSCOPE_KEY","base_url_env":"REPO_LENS_DASHSCOPE_BASE_URL"}]\'></textarea></div>' +
          '<div class="toolbar"><button class="btn" onclick="VRApp.llmSave(false)">预检</button>' +
          '<button class="btn warn" onclick="VRApp.llmSave(true)">保存到项目级 settings.json</button>' +
          '<span class="hint">写入前自动备份 .bak；密钥值请在系统环境变量中设置（下方显示状态）</span></div>' +
          '<div id="llmEnvOut" class="hint"></div>' +
          '<div id="llmOut"></div></div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">Outbound MCP（本工具作为客户端消费外部 Server）</span></div>' +
          '<div class="cbody">' +
          '<div class="toolbar"><button class="btn" onclick="VRApp.mcpOutServers()">列出 Server</button>' +
          '<button class="btn" onclick="VRApp.mcpOutTools()">发现工具</button></div>' +
          '<div class="toolbar">server <input type="text" id="moServer" placeholder="名称" style="width:8rem"> ' +
          'tool <input type="text" id="moTool" placeholder="工具名" style="width:14rem"> ' +
          'args JSON <input type="text" id="moArgs" placeholder=\'{"k":"v"}\' style="width:16rem"> ' +
          '<button class="btn" onclick="VRApp.mcpOutCall(false)">dry-run</button>' +
          '<button class="btn warn" onclick="VRApp.mcpOutCall(true)">调用</button></div>' +
          '<div class="hint">白名单来自 settings.mcp_servers，不接受任意命令；调用需显式确认</div>' +
          '<div id="moOut"></div></div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">多仓库管理（注册本地仓库并绑定分析预设）</span>' +
          '<span class="cact"><button class="btn" onclick="VRApp.reposLoad()">刷新仓库</button></span></div>' +
          '<div class="cbody">' +
          '<div id="reposOut" class="hint">加载中…</div>' +
          '<div class="toolbar" style="margin-top:.6rem">名称 <input type="text" id="repoName" placeholder="如 verorun" style="width:8rem"> ' +
          '路径 <input type="text" id="repoPath" placeholder="D:\\projects\\my-repo" style="width:22rem"> ' +
          '预设 <select id="repoProfile" style="width:10rem"></select> ' +
          '<button class="btn" onclick="VRApp.reposAdd(false)">预检</button>' +
          '<button class="btn warn" onclick="VRApp.reposAdd(true)">注册</button></div>' +
          '<div class="hint">预设：verorun=内置口径；generic-python 等来自 profiles/*.json。切换仓库后刷新 dashboard 重新分析</div>' +
          '</div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">产物归档（按日期）</span>' +
          '<span class="cact"><button class="btn" onclick="VRApp.artifactsLoad()">刷新</button></span></div>' +
          '<div class="cbody"><div id="artOut" class="hint">加载中…</div>' +
          '<div class="hint">每次分析的产物落在 &lt;输出根&gt;/&lt;YYYY-MM-DD&gt;/；AST 缓存与基线快照留在输出根，' +
          '跨日期复用与对比。关闭归档：<code>settings.output.date_dir=false</code> 或 CLI <code>--no-date-dir</code></div>' +
          '</div></section>' +
          '<section class="card"><div class="chead"><span class="ctitle">Agent 端点索引</span><span class="csub" id="setVer">—</span></div>' +
          '<div class="cbody"><p class="hint">Agent 可用 HTTP 直连以下端点：<br>' +
          '<code>/api/analyze</code> · <code>/api/summary</code> · <code>/api/snapshot</code> · <code>/api/diff</code> · <code>/api/query</code> · <code>/api/gate</code> · <code>/api/ai</code> · <code>/api/git/*</code> · <code>/api/scripts/*</code> · <code>/api/audit/*</code> · <code>/api/settings</code> · <code>/api/repos/*</code> · <code>/api/batch</code> · <code>/api/mcp-out/*</code> · <code>/mcp</code>（MCP over HTTP）</p>' +
          '</div></section>',
        mount() { loadSettings(); llmFormFill(); reposLoad(); artifactsLoad(); api('/api/state').then(s => { const e = $('setVer'); if (e) e.textContent = 'v' + (s.tool_version || '?'); }).catch(() => {}); }
      };
    }
  };

  function route() {
    const v = currentView();
    renderNav(v);
    const def = VIEWS[v] ? VIEWS[v]() : VIEWS.dashboard();
    const main = $('smain');
    main.innerHTML = def.html;
    if (def.mount) def.mount();
  }

  // ---- 业务函数（端口自原控制台，行为不变；跨视图安全加 null 守卫） ----
  async function state() {
    const s = await api('/api/state');
    const m = $('meta');
    if (m) m.innerHTML = '仓库 <b>' + esc(s.repo) + '</b>' +
      (s.frontends && s.frontends.length ? ' + <b>' + s.frontends.map(f => f.root).join(', ') + '</b>' : '') +
      ' · 输出 ' + esc(s.out_dir) + ' · v' + esc(s.tool_version);
    const bl = $('baseList');
    if (bl) bl.innerHTML = (s.baselines && s.baselines.length ? s.baselines.map(b => '<option>' + esc(b) + '</option>').join('') : '<option>（暂无）</option>');
  }
  async function showSummary(s) {
    const m = $('metrics');
    if (!m) return;
    const rows = [[s.repo, '主仓库'], [s.plugins, '插件'], [s.core_modules, '核心模块'], [s.lines_code, '后端代码行'],
      [s.frontend_code != null ? s.frontend_code : '—', '前端代码行'], [s.routes_total, '路由'],
      [s.boundary_violations, '边界观察项'], [s.plugins_manifest_invalid, 'manifest待修']];
    m.innerHTML = rows.map(x => '<div class="metric"><b>' + esc(x[0]) + '</b><span>' + esc(x[1]) + '</span></div>').join('');
  }
  async function summary() { busy(1); try { const s = await api('/api/summary'); await showSummary(s); await state(); } catch (e) { msg(e.message); } busy(0); }
  function renderPanels(d) {
    const P = []; const fe = d.frontend || [];
    const coreRows = (d.core.modules || []).map(m => '<tr><td><code>' + esc(m.name) + '</code></td><td>' + esc((m.description || '').slice(0, 42)) + '</td><td class="num">' + m.py_files + '</td><td class="num">' + m.loc.toLocaleString() + '</td><td class="num">' + m.route_count + '</td></tr>').join('');
    P.push('<h2>系统核心模块（' + d.core.module_count + '）</h2><table class="tb"><tr><th>模块</th><th>职责</th><th class="num">.py</th><th class="num">LOC</th><th class="num">路由</th></tr>' + coreRows + '</table>');
    const pRows = (d.plugins.items || []).map(p => '<tr><td><code>' + esc(p.identifier) + '</code></td><td>' + esc(p.version || '—') + '</td><td>' + esc(p.agent_role || '—') + '</td><td>' + esc(p.category || '—') + '</td><td class="num">' + p.route_count + '</td><td class="num">' + p.loc.toLocaleString() + '</td><td class="' + (p.manifest_valid ? 'ok' : 'bad') + '">' + (p.manifest_valid ? '✓' : '✗') + '</td></tr>').join('');
    P.push('<h2>业务插件（' + d.plugins.count + '）</h2><table class="tb"><tr><th>插件</th><th>版本</th><th>角色</th><th>分类</th><th class="num">路由</th><th class="num">LOC</th><th>清单</th></tr>' + pRows + '</table>');
    const vio = (d.interactions && d.interactions.boundary_observations && d.interactions.boundary_observations.violations) || [];
    let vioHtml = vio.length ? vio.slice(0, 25).map(v => '<li><code>' + esc(v.file) + '</code> → <code>' + esc(v.imports) + '</code></li>').join('') : '<li class="ok">未发现核心直连插件的导入</li>';
    const depRank = (d.interactions.plugins_import_core || []).slice(0, 6).map(x => '<code>' + esc(x.module) + '</code>(' + x.import_count + ')').join('、');
    P.push('<h2>边界与依赖</h2><ul class="plain"><li>插件最常依赖：' + depRank + '</li></ul><div class="hint">核心 → 插件直接导入（观察项 ' + vio.length + ' 处）：</div><ul class="plain">' + vioHtml + '</ul>');
    const lang = (d.overview.by_language || []).slice(0, 8).map(e => '<tr><td><code>' + esc(e.ext) + '</code></td><td class="num">' + e.code.toLocaleString() + '</td><td class="num">' + e.files + '</td></tr>').join('');
    const dirs = (d.overview.by_top_dir || []).slice(0, 12).map(e => '<tr><td><code>' + esc(e.dir) + '</code></td><td class="num">' + e.code.toLocaleString() + '</td><td class="num">' + e.files + '</td></tr>').join('');
    P.push('<h2>代码量统计（主仓 ' + ((d.overview.total_code_lines) || 0).toLocaleString() + ' 行）</h2><div class="grid"><div><table class="tb"><tr><th>语言</th><th class="num">代码行</th><th class="num">文件</th></tr>' + lang + '</table></div><div><table class="tb"><tr><th>目录</th><th class="num">代码行</th><th class="num">文件</th></tr>' + dirs + '</table></div></div>');
    fe.forEach(f => { P.push('<h2>桌面端：' + esc(f.root) + '（' + esc(f.kind) + '）</h2><ul class="plain"><li>栈：' + esc(f.stack.join('、')) + ' ｜ ' + f.overview.total_code_lines.toLocaleString() + ' 代码行</li><li>页面 ' + f.metrics.pages + ' · 组件 ' + f.metrics.components + ' · store ' + f.metrics.stores + ' · 主进程 TS ' + f.metrics.electron_main_ts + ' · 内嵌 Python ' + f.metrics.native_py + '</li><li>i18n ' + f.metrics.i18n_locales.join('/') + ' · 测试 ' + f.metrics.tests_unit + '+' + f.metrics.tests_e2e + ' · 构建版本 ' + esc(f.build_editions.join('/')) + '</li></ul>'); });
    const hs = d.hotspots || null;
    if (hs) {
      let hh = '<h2>变更热点（近 ' + hs.days + ' 天 churn × 体量）</h2><div class="hint">' + esc(hs.note) + '</div><table class="tb"><tr><th>文件</th><th class="num">churn</th><th class="num">LOC</th><th class="num">热度分</th><th>风险</th></tr>';
      (hs.files_top || []).slice(0, 10).forEach(r => { hh += '<tr><td><code>' + esc(r.file) + '</code></td><td class="num">' + r.churn + '</td><td class="num">' + r.loc.toLocaleString() + '</td><td class="num">' + r.score + '</td><td class="' + (r.high_risk ? 'bad' : '') + '">' + (r.high_risk ? '高风险' : '') + '</td></tr>'; });
      hh += '</table><div class="hint">插件级聚合：</div><table class="tb"><tr><th>组</th><th class="num">churn</th><th class="num">文件</th><th class="num">LOC</th><th class="num">高风险</th></tr>';
      (hs.groups_top || []).slice(0, 8).forEach(g => { hh += '<tr><td><code>' + esc(g.group) + '</code></td><td class="num">' + g.churn + '</td><td class="num">' + g.files + '</td><td class="num">' + g.loc.toLocaleString() + '</td><td class="num">' + g.high_risk_files + '</td></tr>'; });
      hh += '</table>'; P.push(hh);
    }
    P.push('<div class="hint">完整明细：<a href="/api/report" target="_blank">打开完整 HTML 报告</a>；或用上方查询精确取数。</div>');
    const pn = $('panels'); if (pn) pn.innerHTML = P.join('');
  }
  async function loadData() { try { const d = await api('/api/data'); renderPanels(d); } catch (e) { const pn = $('panels'); if (pn) pn.innerHTML = '<div class="hint">' + esc(e.message) + '</div>'; } }
  async function run() {
    busy(1);
    try {
      const r = await api('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      await showSummary(r.summary); await state(); await loadData();
      const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="chead"><span class="ctitle">完整报告预览</span></div><div class="cbody"><iframe src="/api/report"></iframe></div></section>';
      msg('分析完成，产物已落盘', false);
    } catch (e) { msg(e.message); } busy(0);
  }
  async function snapshot() { busy(1); try { const r = await api('/api/snapshot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); msg('快照已保存：' + r.name + '（' + r.lines_code.toLocaleString() + ' 代码行）', false); await state(); } catch (e) { msg(e.message); } busy(0); }
  async function diff() {
    busy(1); const b = $('baseList') ? $('baseList').value : '';
    if (!b || b.indexOf('暂无') >= 0) { msg('请先生成快照'); busy(0); return; }
    try {
      const d = await api('/api/diff?baseline=' + encodeURIComponent(b)); const df = d.diff; let h = '';
      h += '<h2>基线对比：' + esc(d.baseline) + ' → current</h2><table class="tb"><tr><th>指标</th><th>基线</th><th>当前</th><th>增量</th></tr>';
      Object.entries(df.totals).forEach(([k, v]) => { h += '<tr><td>' + k + '</td><td class="num">' + v.base + '</td><td class="num">' + v.cur + '</td><td class="num">' + (v.delta > 0 ? '+' : '') + v.delta + '</td></tr>'; });
      if (df.loc && df.loc.top && df.loc.top.length) { h += '<tr><th colspan="4">LOC 变动 Top</th></tr>'; df.loc.top.slice(0, 10).forEach(r => { h += '<tr><td>' + esc(r.target) + '</td><td class="num">' + r.base + '</td><td class="num">' + r.cur + '</td><td class="num">' + (r.delta > 0 ? '+' : '') + r.delta + '</td></tr>'; }); }
      ['plugins', 'core'].forEach(function (k) { (df[k].added || []).forEach(x => { h += '<tr><td colspan="4" class="ok">新增 ' + (k === 'plugins' ? esc(x.identifier || x) : esc(x)) + '</td></tr>'; }); (df[k].removed || []).forEach(x => { h += '<tr><td colspan="4" class="bad">移除 ' + (k === 'plugins' ? esc(x.identifier || x) : esc(x)) + '</td></tr>'; }); });
      h += '</table>';
      const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>';
      msg(df.has_changes ? '检测到变化' : '无差异', false);
    } catch (e) { msg(e.message); } busy(0);
  }
  async function gate() {
    busy(1);
    try {
      const r = await api('/api/gate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items: 'all' }) });
      let h = '<h2>架构门禁（--fail-on all 同源）</h2><table class="tb"><tr><th>检查项</th><th>结果</th><th class="num">命中</th></tr>';
      r.results.forEach(x => {
        h += '<tr><td><code>' + esc(x.name) + '</code></td><td class="' + (x.passed ? 'ok' : 'bad') + '">' + (x.passed ? 'PASS' : 'FAIL') + '</td><td class="num">' + x.hit_count + '</td></tr>';
        if (!x.passed && x.hits && x.hits.length) x.hits.slice(0, 4).forEach(hh => { h += '<tr><td colspan="3" class="hint">- ' + esc(hh.plugin || hh.file || '') + '：' + esc(hh.detail || '') + '</td></tr>'; });
      });
      h += '</table>';
      const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>';
    } catch (e) { msg(e.message); } busy(0);
  }
  async function ai() { busy(1); try { const t = await api('/api/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="chead"><span class="ctitle">AI 上下文（全局）</span></div><div class="cbody"><pre>' + t.replace(/</g, '&lt;') + '</pre></div></section>'; } catch (e) { msg(e.message); } busy(0); }
  async function query() {
    const b = { scope: $('qScope').value, select: $('qSelect').value, where: $('qWhere').value, format: 'table' };
    try { const t = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }); const qo = $('qOut'); if (qo) qo.innerHTML = '<pre>' + t.replace(/</g, '&lt;') + '</pre>'; }
    catch (e) { const qo = $('qOut'); if (qo) qo.innerHTML = '<span class="err">' + esc(e.message) + '</span>'; }
  }
  let _timer = null;
  function toggleAuto(cb) {
    if (cb.checked) { _timer = setInterval(async () => { try { const s = await api('/api/summary'); await showSummary(s); } catch (e) { } }, 15000); msg('自动刷新已开启：每 15 秒重算双仓指标', false); }
    else { clearInterval(_timer); _timer = null; msg('自动刷新已关闭', false); }
  }

  // ---- Git ----
  async function gitLog() { busy(1); try { const d = await api('/api/git/log?since=30'); let h = '<h2>提交历史（最近 30 天）</h2><table class="tb"><tr><th class="num">总计</th><th>作者分布</th></tr>'; h += '<tr><td class="num">' + d.total_commits + ' 次提交</td><td>'; Object.entries(d.authors).forEach(([a, c]) => { h += esc(a) + ' ' + c + ' 次<br>'; }); h += '</td></tr></table>'; if (d.top_churned_files && d.top_churned_files.length) { h += '<h3>热门文件</h3><ul class="plain">'; d.top_churned_files.slice(0, 10).forEach(f => { h += '<li><code>' + esc(f.file) + '</code> ' + f.commits + ' 次修改</li>'; }); h += '</ul>'; } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('加载成功', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitRemoteDiff() { busy(1); try { const d = await api('/api/git/remote-diff'); let st = d.status === 'synced' ? '✓' : d.status === 'ahead' ? '📤' : d.status === 'behind' ? '📥' : '⚠️'; let h = '<h2>远程差异 · ' + esc(d.remote) + '/' + esc(d.branch) + '</h2><p><b>' + st + ' 状态：</b>' + d.status + '（领先 ' + d.ahead + ' / 落后 ' + d.behind + '）</p>'; if (d.unpushed_commits && d.unpushed_commits.length) { h += '<h3>未推送的提交</h3><ul class="plain">'; d.unpushed_commits.forEach(c => { h += '<li>' + esc(c) + '</li>'; }); h += '</ul>'; } if (d.unpulled_commits && d.unpulled_commits.length) { h += '<h3>远程新增提交</h3><ul class="plain">'; d.unpulled_commits.forEach(c => { h += '<li>' + esc(c) + '</li>'; }); h += '</ul>'; } if (d.new_remote_tags && d.new_remote_tags.length) { h += '<h3>远程新标签</h3><ul class="plain">'; d.new_remote_tags.forEach(t => { h += '<li><code>' + esc(t) + '</code></li>'; }); h += '</ul>'; } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('加载成功', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitUntracked() { busy(1); try { const d = await api('/api/git/untracked'); let h = '<h2>未跟踪文件（共 ' + d.total_untracked + ' 个）</h2>'; if (d.total_untracked === 0) { h += '<p>✅ 没有未跟踪文件</p>'; } else { Object.entries(d.categories).forEach(([cat, files]) => { h += '<h3>' + esc(cat) + '</h3><ul class="plain">'; files.slice(0, 15).forEach(f => { h += '<li><code>' + esc(f) + '</code></li>'; }); if (files.length > 15) h += '<li>... 还有 ' + (files.length - 15) + ' 个</li>'; h += '</ul>'; }); if (d.ignore_suggestions && d.ignore_suggestions.length) { h += '<h3>建议添加到 .gitignore</h3><pre>'; d.ignore_suggestions.forEach(p => { h += esc(p) + '\n'; }); h += '</pre>'; } } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('扫描完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitCiCheck() { busy(1); try { const d = await api('/api/git/ci-check'); if (d.error) { const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody"><h2>CI 配置检查</h2><p class="err">' + esc(d.error) + '</p></div></section>'; busy(0); return; } let s = d.summary; let h = '<h2>CI/CD 配置分析 (' + esc(d.platform) + ')</h2><div class="grid"><div class="metric"><b>' + s.total + '</b><span>总问题数</span></div><div class="metric"><b class="bad">' + s.errors + '</b><span>错误</span></div><div class="metric"><b style="color:var(--swarn)">' + s.warnings + '</b><span>警告</span></div><div class="metric"><b>' + s.info + '</b><span>提示</span></div></div>'; if (d.findings && d.findings.length) { h += '<table class="tb"><tr><th>规则</th><th>级别</th><th>文件</th><th>问题</th><th>建议</th></tr>'; d.findings.forEach(f => { let sc = f.severity === 'error' ? 'bad' : f.severity === 'warning' ? '' : 'ok'; h += '<tr><td><code>' + esc(f.rule_id) + '</code></td><td class="' + sc + '">' + esc(f.severity) + '</td><td>' + esc(f.file) + (f.line ? ':' + f.line : '') + '</td><td>' + esc(f.message) + '</td><td>' + esc(f.suggestion || '') + '</td></tr>'; }); h += '</table>'; } else { h += '<p>✅ 未发现任何问题！</p>'; } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('检查完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitPush() { busy(1); try { const r = await api('/api/git/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: false }) }); let h = '<h2>推送预检结果</h2>'; if (r.error) { h += '<p class="err">❌ ' + esc(r.error) + '</p>'; if (r.suggestion) h += '<p class="hint">💡 ' + esc(r.suggestion) + '</p>'; } else { h += '<p class="ok">✅ ' + esc(r.message) + '</p>'; if (r.unpushed_commits) { h += '<h3>将推送的提交</h3><ul class="plain">'; r.unpushed_commits.forEach(c => { h += '<li>' + esc(c) + '</li>'; }); h += '</ul>'; } if (r.action_required) h += '<p class="hint">⚠️ ' + esc(r.action_required) + '</p>'; } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('预检完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitPull() { busy(1); try { const r = await api('/api/git/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: false, rebase: false }) }); let h = '<h2>拉取预检结果</h2>'; if (r.error) { h += '<p class="err">❌ ' + esc(r.error) + '</p>'; if (r.suggestion) h += '<p class="hint">💡 ' + esc(r.suggestion) + '</p>'; } else { h += '<p class="ok">✅ ' + esc(r.message) + '</p>'; if (r.unpulled_commits) { h += '<h3>将拉取的提交</h3><ul class="plain">'; r.unpulled_commits.forEach(c => { h += '<li>' + esc(c) + '</li>'; }); h += '</ul>'; } if (r.conflict_risk) h += '<p class="hint">冲突风险：<b>' + esc(r.conflict_risk) + '</b></p>'; if (r.action_required) h += '<p class="hint">⚠️ ' + esc(r.action_required) + '</p>'; } const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('预检完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function gitSync() { busy(1); try { const groups = await api('/api/git/groups'); const names = Object.keys(groups.groups); if (!names.length) { const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody"><h2>多仓库同步</h2><p class="err">未配置任何仓库组，请先用 CLI 创建：insight group add</p></div></section>'; busy(0); return; } let h = '<h2>多仓库同步</h2><p>选择要同步的仓库组：</p><select id="syncGroup" style="margin:.5rem 0;padding:.4rem">'; names.forEach(g => { h += '<option value="' + esc(g) + '">' + esc(g) + '</option>'; }); h += '</select><div style="margin-top:.5rem"><button class="btn btn-pri" onclick="VRApp.doGitSync()">开始同步（dry-run）</button></div>'; const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; } catch (e) { msg(e.message); } busy(0); }
  async function doGitSync() { const g = $('syncGroup'); if (!g) return; busy(1); try { const r = await api('/api/git/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group: g.value, confirm: false }) }); let h = '<h2>同步结果</h2>'; (r.results || []).forEach(res => { h += '<h3>' + esc(res.repo) + '</h3><ul class="plain">'; if (res.error) { h += '<li class="err">❌ ' + esc(res.error) + '</li>'; } else { ['github_to_gitee', 'gitee_to_github'].forEach(dir => { if (res[dir]) { let label = dir === 'github_to_gitee' ? 'GitHub → Gitee' : 'Gitee → GitHub'; h += '<li><b>' + label + '</b>: ' + esc(res[dir].message || res[dir].error || '—') + '</li>'; } }); } h += '</ul>'; }); const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('同步检查完成', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 仓库组管理 + 批量（OPEN-D 补齐，对应 CLI group/batch）----
  async function groupsLoad() { busy(1); try { const d = await api('/api/git/groups'); const names = Object.keys(d.groups || {}); const go = $('groupsOut'); if (go) { let h = names.length ? '<h3>现有组</h3><ul class="plain">' : '<p class="hint">（暂无仓库组；用上方表单添加）</p>'; names.forEach(g => { const repos = (d.groups[g].repos || []).map(r => '<code>' + esc(r.path) + '</code>').join('、'); h += '<li><b>' + esc(g) + '</b>：' + (repos || '（空）') + '</li>'; }); if (names.length) h += '</ul>'; go.innerHTML = h; } const bg = $('batchGroup'); if (bg) bg.innerHTML = names.map(g => '<option value="' + esc(g) + '">' + esc(g) + '</option>').join('') || '<option>（无组）</option>'; msg('组列表已刷新', false); } catch (e) { msg(e.message); } busy(0); }
  async function groupAdd(confirm) { const g = $('grpName'), r = $('grpRepo'); if (!g || !r || !g.value.trim() || !r.value.trim()) { msg('请填写组名与仓库路径'); return; } busy(1); try { const res = await api('/api/git/groups/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group: g.value.trim(), repo: r.value.trim(), confirm: confirm }) }); msg(confirm ? '已添加到组 ' + res.group : '预检通过：传确认添加生效', false); if (confirm) await groupsLoad(); } catch (e) { msg(e.message); } busy(0); }
  async function groupRemove(confirm) { const g = $('grpName'), r = $('grpRepo'); if (!g || !r || !g.value.trim() || !r.value.trim()) { msg('请填写组名与仓库路径'); return; } busy(1); try { const res = await api('/api/git/groups/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ group: g.value.trim(), repo: r.value.trim(), confirm: confirm }) }); msg('已从组 ' + res.group + ' 移除', false); await groupsLoad(); } catch (e) { msg(e.message); } busy(0); }
  async function batchRun() { const bg = $('batchGroup'); if (!bg) return; const g = bg.value; if (!g || g.indexOf('无组') >= 0) { msg('暂无仓库组'); return; } busy(1); try { const r = await api('/api/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: $('batchCmd').value, group: g }) }); let h = '<h2>batch ' + esc(r.command) + ' @ ' + esc(r.group) + '（' + r.count + ' 仓）</h2>'; (r.results || []).forEach(res => { h += '<h3>' + esc(res.repo) + '</h3><pre style="max-height:20rem;overflow:auto">' + esc(JSON.stringify(res.error || res.data, null, 1)) + '</pre>'; }); const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('batch 完成', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 脚本库 ----
  async function loadScripts() { busy(1); try { const d = await api('/api/scripts'); let h = '<h2>脚本资产库（' + d.count + ' 条 · registry v' + esc(d.registry_version) + '）</h2>'; h += '<p>脚本参数（空格分隔，可留空用默认）：<input id="scriptArgv" style="padding:.3rem;width:60%" placeholder="如 --root D:\\projects\\verorun-code"></p>'; h += '<table class="tb"><tr><th>ID</th><th>名称</th><th>类别</th><th>类型</th><th>状态</th><th>版本</th><th>操作</th></tr>'; d.scripts.forEach(s => { const runnable = (s.status === 'active' && s.kind === 'tool'); let ops = '<button class="btn" onclick="VRApp.scriptDetail(\'' + esc(s.id) + '\')">详情</button>'; if (runnable) { ops += ' <button class="btn" onclick="VRApp.scriptRun(\'' + esc(s.id) + '\',false)">dry-run</button> <button class="btn warn" onclick="VRApp.scriptRun(\'' + esc(s.id) + '\',true)">执行</button>'; } h += '<tr><td><code>' + esc(s.id) + '</code></td><td>' + esc(s.name) + '</td><td>' + esc(s.category) + '</td><td>' + esc(s.kind) + '</td><td>' + esc(s.status) + '</td><td>' + esc(s.version) + '</td><td>' + ops + '</td></tr>'; }); h += '</table><div id="scriptOut" style="margin-top:.8rem"></div>'; const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('脚本库已加载', false); } catch (e) { msg(e.message); } busy(0); }
  function _argv() { const e = $('scriptArgv'); return (e ? e.value : '').trim().split(/\s+/).filter(Boolean); }
  async function scriptDetail(id) { busy(1); try { const s = await api('/api/scripts/' + encodeURIComponent(id)); const pv = $('preview'); if (pv) pv.insertAdjacentHTML('beforeend', '<section class="card"><div class="chead"><span class="ctitle">' + esc(id) + ' 元数据</span></div><div class="cbody"><pre>' + esc(JSON.stringify(s, null, 2)) + '</pre></div></section>'); msg('已展开 ' + id, false); } catch (e) { msg(e.message); } busy(0); }
  async function scriptRun(id, confirm) { busy(1); try { const r = await api('/api/scripts/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id, argv: _argv(), confirm: confirm }) }); let h = '<h2>' + (confirm ? '执行' : 'dry-run') + ' · ' + esc(id) + '</h2>'; if (r.error) { h += '<p class="err">❌ ' + esc(r.error) + (r.detail ? '（' + esc(r.detail) + '）' : '') + '</p>'; if (r.hint) h += '<p class="hint">' + esc(r.hint) + '</p>'; } else { h += '<p class="hint">命令：<code>' + esc(r.command || '') + '</code></p>'; if (r.dry_run) { h += '<p class="ok">✅ 预检通过（未执行）。点“执行”并确认方可真正运行。</p>'; } else { h += '<p class="ok">✅ 已执行，退出码 ' + r.exit_code + '（详见 stdout/落盘）</p>'; } } const box = $('scriptOut'); if (box) box.innerHTML = h; else { const pv = $('preview'); if (pv) pv.insertAdjacentHTML('beforeend', '<section class="card"><div class="cbody">' + h + '</div></section>'); } msg(confirm ? '执行完成' : 'dry-run 完成', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 脚本库管理动作（OPEN-D 补齐，对应 CLI script doctor/manual/snapshot/diff）----
  async function scriptDoctor() { busy(1); try { const d = await api('/api/scripts/doctor'); let h = '<h2>脚本自检（' + d.checked + ' 条）</h2><table class="tb"><tr><th>ID</th><th>结果</th><th>错误</th></tr>'; (d.results || []).forEach(r => { h += '<tr><td><code>' + esc(r.id) + '</code></td><td class="' + (r.ok ? 'ok' : 'bad') + '">' + (r.ok ? '✓ import+main' : '✗') + '</td><td>' + esc(r.error || '—') + '</td></tr>'; }); h += '</table>'; if (!d.checked) h += '<p class="hint">（无可自检的 active 脚本）</p>'; const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('自检完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function scriptManual() { busy(1); try { const r = await fetch('/api/scripts/manual', { headers: _authHeaders() }); const t = await r.text(); const w = window.open('', '_blank'); if (w) { w.document.title = '脚本库使用手册'; w.document.body.style.whiteSpace = 'pre-wrap'; w.document.body.style.fontFamily = 'monospace'; w.document.body.textContent = t; } msg('使用手册已在新窗口生成', false); } catch (e) { msg(e.message); } busy(0); }
  async function scriptBaseline() { busy(1); try { const r = await api('/api/scripts/baseline', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); msg('脚本库基线已保存（' + r.count + ' 条）', false); } catch (e) { msg(e.message); } busy(0); }
  async function scriptBaselineDiff() { busy(1); try { const st = await api('/api/state'); const bl = (st.baselines || []); const name = prompt('基线名（先「存基线」产生）：'); if (!name) { busy(0); return; } const d = await api('/api/scripts/baseline-diff?name=' + encodeURIComponent(name)); let h = '<h2>脚本库变更（vs ' + esc(d.baseline) + '）</h2><ul class="plain"><li>新增 ' + d.added.length + '：' + (d.added.join('、') || '—') + '</li><li>删除 ' + d.removed.length + '：' + (d.removed.join('、') || '—') + '</li><li>变更 ' + d.changed.length + '</li></ul>'; (d.changed || []).forEach(c => { h += '<p class="hint">~ ' + esc(c.id) + '：' + esc(JSON.stringify(c.before)) + ' → ' + esc(JSON.stringify(c.after)) + '</p>'; }); if (!(d.added.length + d.removed.length + d.changed.length)) h += '<p class="ok">（无变更）</p>'; const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('基线对比完成', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 审计 ----
  async function auditPreview() { busy(1); try { const r = await api('/api/audit/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: false, fail_on_severity: $('auditFailOn').value }) }); let h = '<h2>审计预览（dry-run）</h2><p class="hint">仓库：<code>' + esc(r.target) + '</code><br>维度：' + esc((r.dims || []).join('、')) + ' ｜ 阈值 ' + esc(r.fail_on_severity) + '</p><p>' + esc(r.hint || '') + '</p>'; const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>'; msg('预览完成，点“执行审计”运行', false); } catch (e) { msg(e.message); } busy(0); }
  function _llmBody() {
    const wl = $('withLlm'); if (!wl) return {};
    return { with_llm: wl.checked,
             llm_model: ($('llmModel') && $('llmModel').value.trim()) || null,
             llm_no_tools: !!( $('llmNoTools') && $('llmNoTools').checked ) };
  }
  async function auditRun() { busy(1); try { const rd = await api('/api/audit/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ confirm: true, fail_on_severity: $('auditFailOn').value }, _llmBody())) }); renderAudit(rd); msg('审计完成：' + (rd.verdict && rd.verdict.decision), false); } catch (e) { msg(e.message); } busy(0); }
  function renderAudit(rd) {
    const v = rd.verdict || {}; const dec = v.decision === 'releasable'; let h = '<h2>代码审计结论</h2>';
    h += '<p class="' + (dec ? 'ok' : 'err') + '" style="font-size:1.1rem">' + (dec ? '✅ 放行' : '⛔ 不放行') + ' ｜ 阻断 ' + (v.blocking || 0) + '</p>';
    (v.reasons || []).forEach(r => { h += '<p class="hint">· ' + esc(r) + '</p>'; });
    h += '<h3>覆盖度矩阵</h3><table class="tb"><tr><th>维度</th><th>规则通道</th><th>语义通道</th></tr>';
    Object.entries(rd.coverage_matrix || {}).forEach(([d, c]) => { h += '<tr><td>' + esc(d) + '</td><td>' + (c.rule_only.ran ? '✅ 已审 / 命中 ' + c.rule_only.findings : '❌ ' + esc(c.rule_only.reason || '')) + '</td><td>' + (c.semantic.ran ? '✅' : '— ' + esc(c.semantic.reason || '')) + '</td></tr>'; });
    h += '</table><h3>发现（' + ((rd.summary && rd.summary.total) || 0) + ' 条）</h3><ul class="plain">';
    if (rd.llm_channel) {
      const lc = rd.llm_channel;
      h += '<p class="hint">内置 LLM 通道：' + (lc.ran ? '✅ ran=true model=' + esc(lc.model || '') + ' findings=' + lc.findings + ' tool_calls=' + lc.tool_calls : '⚠️ 已降级（' + esc(lc.reason || '') + '）——不影响审计主流程') + '</p>';
    }
    (rd.findings || []).forEach(f => { const e = (f.evidence && f.evidence[0]) || {}; h += '<li><b>' + esc(f.rule_id) + '</b> [' + esc(f.severity) + '] ' + esc(f.title) + ' ' + (e.file ? ('<code>' + esc(e.file) + (e.line ? (':' + e.line) : '') + '</code>') : '') + '</li>'; });
    if (!(rd.findings || []).length) h += '<li>（规则通道无命中）</li>';
    h += '</ul>';
    const pv = $('preview'); if (pv) pv.innerHTML = '<section class="card"><div class="cbody">' + h + '</div></section>';
  }
  async function auditSemPrompt() { busy(1); try { const r = await fetch('/api/audit/semantic-prompt', { headers: _authHeaders() }); const t = await r.text(); const w = window.open('', '_blank'); if (w) { w.document.title = '语义审计任务书'; w.document.body.style.whiteSpace = 'pre-wrap'; w.document.body.style.fontFamily = 'monospace'; w.document.body.textContent = t; } msg('语义任务书已在新窗口生成，可交 AI Agent 判定', false); } catch (e) { msg(e.message); } busy(0); }
  async function auditRunWithSemantic() { const txt = ($('semResults') ? $('semResults').value : '').trim(); let sem = null; if (txt) { try { sem = JSON.parse(txt); } catch (e) { msg('语义结果 JSON 解析失败：' + e.message); return; } } busy(1); try { const rd = await api('/api/audit/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ confirm: true, fail_on_severity: $('auditFailOn').value, semantic_results: sem, semantic_can_block: $('semCanBlock').checked }, _llmBody())) }); renderAudit(rd); msg('审计完成：' + (rd.verdict && rd.verdict.decision) + (sem ? '（含语义 ' + ((sem.findings || sem).length) + ' 条输入）' : ''), false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 设置 / outbound MCP（OPEN-D 补齐）----
  async function loadSettings() { busy(1); try { const s = await api('/api/settings'); const c = s.config || {}; const prof = s.profile || {}; const co = $('cfgOut'); if (co) { let h = '<ul class="plain">' + '<li>profile：<b>' + esc(s.profile_name) + '</b>（分析口径，详见 settings.json profile 段）</li>' + '<li>MCP 服务：' + (c.mcp_enabled !== false ? '✅ 开启' : '❌ 关闭') + ' ｜ outbound：' + (c.mcp_outbound_enabled !== false ? '✅ 开启' : '❌ 关闭') + '（外部 Server ' + ((c.mcp_servers || []).length) + ' 个）</li>' + '<li>LLM：' + (c.llm_enabled ? '✅ 开启（默认 ' + esc(c.default_model || '未设') + '，模型 ' + ((c.models || []).length) + ' 个）' : '❌ 关闭（audit --with-llm 可显式启用，失败自动降级）') + '</li>' + '<li>上限：max_plugins_in_ai_context=' + esc(c.max_plugins_in_ai_context) + ' · max_tree_depth=' + esc(c.max_tree_depth) + '</li>' + '<li>组件：component.dir=<code>' + esc((prof.component || {}).dir || '（未设）') + '</code> · manifest=<code>' + esc((prof.component || {}).manifest || '（未设）') + '</code></li>' + '<li>加载的配置文件：<code>' + ((s.loaded_files || []).map(esc).join('</code> → <code>') || '（仅包内默认）') + '</code></li></ul>'; co.innerHTML = h; } msg('配置已加载（密钥永不回显）', false); } catch (e) { msg(e.message); } busy(0); }
  async function mcpOutServers() { busy(1); try { const d = await api('/api/mcp-out/servers'); const o = $('moOut'); if (o) { let h = '<h3>外部 MCP Server（' + d.count + '）</h3>' + (d.count ? '<table class="tb"><tr><th>名称</th><th>传输</th><th>启用</th><th>目标</th></tr>' + d.servers.map(s => '<tr><td><code>' + esc(s.name) + '</code></td><td>' + esc(s.transport) + '</td><td>' + (s.enabled ? '✅' : '—') + '</td><td class="hint">' + esc(s.target || '') + '</td></tr>').join('') + '</table>' : '<p class="hint">（未配置；在 settings.json 的 mcp_servers 中声明）</p>'); o.innerHTML = h; } msg('已列出', false); } catch (e) { msg(e.message); } busy(0); }
  async function mcpOutTools() { busy(1); try { const sv = $('moServer'); const d = await api('/api/mcp-out/tools' + (sv && sv.value.trim() ? ('?server=' + encodeURIComponent(sv.value.trim())) : '')); const o = $('moOut'); if (o) { let h = '<h3>外部工具（' + d.tools.length + '）</h3>'; if (d.tools.length) { h += '<table class="tb"><tr><th>Server</th><th>工具</th><th>说明</th></tr>' + d.tools.map(t => '<tr><td><code>' + esc(t.server) + '</code></td><td><code>' + esc(t.name) + '</code></td><td class="hint">' + esc(t.description || '') + '</td></tr>').join('') + '</table>'; } (d.errors || []).forEach(e => { h += '<p class="err">' + esc(e) + '</p>'; }); if (!d.tools.length && !(d.errors || []).length) h += '<p class="hint">（无工具；检查 mcp_servers 配置）</p>'; o.innerHTML = h; } msg('发现完成', false); } catch (e) { msg(e.message); } busy(0); }
  async function mcpOutCall(confirm) { const g = id => $(id); const sv = g('moServer'), tl = g('moTool'), ar = g('moArgs'); if (!sv || !sv.value.trim() || !tl || !tl.value.trim()) { msg('请填写 server 与 tool'); return; } let args = {}; if (ar && ar.value.trim()) { try { args = JSON.parse(ar.value); } catch (e) { msg('args 不是合法 JSON：' + e.message); return; } } busy(1); try { const r = await api('/api/mcp-out/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server: sv.value.trim(), tool: tl.value.trim(), args: args, confirm: confirm }) }); const o = $('moOut'); if (o) o.innerHTML = '<h3>' + (confirm ? '调用' : 'dry-run') + ' · ' + esc(r.server || '') + '.' + esc(r.tool || '') + '</h3><pre style="max-height:24rem;overflow:auto">' + esc(JSON.stringify(r, null, 2)) + '</pre>'; msg(confirm ? '调用完成' : 'dry-run 完成', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- LLM 可视化配置（settings 视图）----
  async function llmFormFill() { try { const s = await api('/api/settings'); const c = s.config || {}; const e = $('llmEnabled'); if (e) e.checked = !!c.llm_enabled; const dm = $('llmDefault'); if (dm) dm.value = c.default_model || ''; const ta = $('llmModels'); if (ta) ta.value = JSON.stringify(c.models || [], null, 2); const eo = $('llmEnvOut'); if (eo) { const st = s.env_status || {}; const ks = Object.keys(st); eo.innerHTML = ks.length ? '<p>密钥环境变量状态：</p><ul class="plain">' + ks.map(k => '<li><b>' + esc(k) + '</b>：key_env <code>' + esc(st[k].key_env || '（未设）') + '</code> ' + (st[k].key_set ? '✅ 已设置' : '❌ 未设置') + (st[k].base_url_env ? ' ｜ base_url_env <code>' + esc(st[k].base_url_env) + '</code> ' + (st[k].base_url_set ? '✅' : '❌') : '') + '</li>').join('') + '</ul>' : '（尚未配置模型）'; } } catch (e) { /* 静默：表单保持空 */ } }
  async function llmSave(confirm) { const g = id => $(id); const en = g('llmEnabled'), dm = g('llmDefault'), ta = g('llmModels'); let models = []; if (ta && ta.value.trim()) { try { models = JSON.parse(ta.value); } catch (e) { msg('models JSON 解析失败：' + e.message); return; } } busy(1); try { const r = await api('/api/settings/llm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ llm_enabled: !!(en && en.checked), default_model: (dm && dm.value.trim()) || null, models: models, confirm: confirm }) }); const o = $('llmOut'); if (o) o.innerHTML = '<pre style="max-height:12rem;overflow:auto">' + esc(JSON.stringify(r, null, 2)) + '</pre>'; if (confirm) { await llmFormFill(); await loadSettings(); } msg(confirm ? '已写回 settings.json（旧文件备份为 .bak）' : '预检通过（再点「保存」写入）', false); } catch (e) { msg(e.message); } busy(0); }

  // ---- 多仓库管理（settings 视图）----
  async function reposLoad() { busy(1); try { const d = await api('/api/repos'); const o = $('reposOut'); const sel = $('repoProfile'); if (sel) { const cur = sel.value; sel.innerHTML = (d.profiles || []).map(p => '<option' + (p === cur ? ' selected' : '') + '>' + esc(p) + '</option>').join(''); } if (o) { const c = d.current || {}; let h = '<p>当前：<b>' + esc(c.name || '') + '</b> <code>' + esc(c.path || '') + '</code> · 预设 <b>' + esc(c.profile || 'verorun') + '</b></p>'; h += '<table class="tb"><tr><th>名称</th><th>预设</th><th>路径</th><th>操作</th></tr>'; (d.repos || []).forEach(r => { h += '<tr><td><code>' + esc(r.name) + '</code></td><td>' + esc(r.profile || 'verorun') + '</td><td class="hint">' + esc(r.path || '') + '</td><td>' + '<button class="btn" onclick="VRApp.reposSwitch(\'' + esc(r.name) + '\')">切换</button> ' + '<button class="btn warn" onclick="VRApp.reposRemove(\'' + esc(r.name) + '\')">移除</button></td></tr>'; }); h += '</table>'; if (!(d.repos || []).length) h += '<p class="hint">（注册表为空；用下方表单或 CLI：repolens repos add）</p>'; o.innerHTML = h; } msg('仓库注册表已加载', false); } catch (e) { msg(e.message); } busy(0); }
  async function artifactsLoad() { const o = $('artOut'); if (!o) return; try { const d = await api('/api/artifacts'); const runs = d.runs || []; let h = '<p>输出根：<code>' + esc(d.artifact_root || '') + '</code><br>当前产物目录：<code>' + esc(d.current || '') + '</code></p>'; if (runs.length) { h += '<table class="tb"><tr><th>日期目录</th><th>产物数</th><th>文件</th></tr>'; runs.forEach(r => { h += '<tr><td><code>' + esc(r.name) + '</code>' + (r.name === String(d.latest || '').split(/[\\/]/).pop() ? ' <b>（最近）</b>' : '') + '</td><td>' + esc(r.file_count) + '</td><td class="hint">' + esc((r.files || []).slice(0, 6).join(' · ')) + '</td></tr>'; }); h += '</table>'; } else { h += '<p class="hint">（暂无历史产物；运行一次分析后按日期归档）</p>'; } o.innerHTML = h; } catch (e) { if (o) o.textContent = '加载失败：' + e.message; } }
  async function reposAdd(confirm) { const g = id => $(id); const nm = g('repoName'), pa = g('repoPath'), pf = g('repoProfile'); if (!nm || !nm.value.trim() || !pa || !pa.value.trim()) { msg('请填写名称与路径'); return; } busy(1); try { const r = await api('/api/repos/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: nm.value.trim(), path: pa.value.trim(), profile: (pf && pf.value !== 'verorun' ? pf.value : null), confirm: confirm }) }); const o = $('reposOut'); if (o && !confirm) o.insertAdjacentHTML('beforeend', '<p class="hint">预检通过（confirm=true 才写入）：</p><pre style="max-height:10rem;overflow:auto">' + esc(JSON.stringify(r, null, 2)) + '</pre>'); if (confirm) { nm.value = ''; pa.value = ''; await reposLoad(); } msg(confirm ? '已注册' : '预检完成（再点「注册」写入）', false); } catch (e) { msg(e.message); } busy(0); }
  async function reposSwitch(name) { busy(1); try { const r = await api('/api/repos/switch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) }); msg('已切换到 ' + name + '（预设 ' + (r.profile || 'verorun') + '）；请到仪表盘点「刷新」重新分析', false); await reposLoad(); } catch (e) { msg(e.message); } busy(0); }
  async function reposRemove(name) { if (!window.confirm('移除注册项 ' + name + '？（不影响磁盘文件）')) return; busy(1); try { await api('/api/repos/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, confirm: true }) }); msg('已移除 ' + name, false); await reposLoad(); } catch (e) { msg(e.message); } busy(0); }

  // ---- 暴露给 onclick 的属性 ----
  window.VRApp = {
    run, snapshot, diff, gate, ai, query, toggleAuto,
    gitLog, gitRemoteDiff, gitUntracked, gitCiCheck, gitPush, gitPull, gitSync, doGitSync,
    groupsLoad, groupAdd, groupRemove, batchRun,
    loadScripts, scriptDetail, scriptRun, scriptDoctor, scriptManual, scriptBaseline, scriptBaselineDiff,
    auditPreview, auditRun, auditSemPrompt, auditRunWithSemantic,
    loadSettings, mcpOutServers, mcpOutTools, mcpOutCall, llmFormFill, llmSave,
    reposLoad, reposAdd, reposSwitch, reposRemove, summary, artifactsLoad
  };

  // ---- 启动 ----
  window.addEventListener('hashchange', route);
  state();
  route();
})();
