// RepoLucent · insight.js —— 洞察层：架构依赖图 + 热点/门禁审计/符号/上下文 + 命令面板。
// 复用 app.js 暴露的 window.VRApp._core（api/esc/busy/msg/$/authHeaders/reroute）。
// 依赖图为静态启发式解析产物（/api/graph），零第三方库，自实现力导向布局（SVG）。
(function () {
  'use strict';
  var core = (window.VRApp && window.VRApp._core) || {};
  var $ = core.$ || function (id) { return document.getElementById(id); };
  var api = core.api || function () { return Promise.reject(new Error('core.api 未就绪')); };
  var busy = core.busy || function () {};
  var msg = core.msg || function () {};
  var authHeaders = core.authHeaders || function (x) { return x || {}; };

  function escA(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function escT(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }

  var _GRAPH = null, _DATA = null, _SEL = null, _CTX = '';
  var MAX_DRAW = 90, SVGNS = 'http://www.w3.org/2000/svg';

  function ownerOfPath(p) {
    if (_GRAPH && _GRAPH.files) { for (var i = 0; i < _GRAPH.files.length; i++) { if (_GRAPH.files[i].path === p) return _GRAPH.files[i].owner; } }
    var seg = String(p || '').split('/'); return seg.length > 1 ? seg[0] : '<root>';
  }
  function ownerClass(o) {
    if (!_DATA) return 'edge';
    var core1 = ((_DATA.core && _DATA.core.modules) || []).map(function (m) { return m.name; });
    if (core1.indexOf(o) >= 0) return 'core';
    var plg = ((_DATA.plugins && _DATA.plugins.items) || []).map(function (p) { return p.identifier; });
    if (plg.indexOf(o) >= 0) return 'biz';
    return 'edge';
  }
  function violMap() {
    var b = _DATA && _DATA.interactions && _DATA.interactions.boundary_observations, m = {};
    ((b && b.violations) || []).forEach(function (v) { m[v.file] = 1; });
    return m;
  }

  function finalize(nodes, links) {
    var deg = {};
    links.forEach(function (l) { deg[l.s] = (deg[l.s] || 0) + 1; deg[l.t] = (deg[l.t] || 0) + 1; });
    var maxdeg = 1;
    nodes.forEach(function (n) { var d = deg[n.id] || 0; if (d > maxdeg) maxdeg = d; });
    nodes.forEach(function (n) { n.r = Math.round(8 + 18 * Math.sqrt((deg[n.id] || 0) / maxdeg)); });
    var by = {}; nodes.forEach(function (n) { by[n.id] = n; });
    return { nodes: nodes, links: links, by: by };
  }
  function graphModel(scope) {
    if (!_GRAPH) return { nodes: [], links: [], by: {} };
    var FE = _GRAPH.file_edges || [], viol = violMap(), ownOf = {};
    (_GRAPH.files || []).forEach(function (f) { ownOf[f.path] = f.owner; });
    var owner = function (p) { return ownOf[p] || ownerOfPath(p); };
    if (scope === 'owner') {
      var oloc = {}, oviol = {};
      (_GRAPH.files || []).forEach(function (f) { oloc[f.owner] = (oloc[f.owner] || 0) + (f.loc || 0); if (viol[f.path]) oviol[f.owner] = 1; });
      var agg = {};
      FE.forEach(function (e) { var a = owner(e['from']), b = owner(e['to']); if (a === b) return; var k = a + '\u0001' + b; agg[k] = (agg[k] || 0) + (e.weight || 1); });
      var conn = {}, links = [];
      Object.keys(agg).forEach(function (k) { var ab = k.split('\u0001'); conn[ab[0]] = 1; conn[ab[1]] = 1; links.push({ s: ab[0], t: ab[1], w: agg[k] }); });
      var nodes = Object.keys(conn).map(function (o) { return { id: o, label: o, cls: ownerClass(o), loc: oloc[o] || 0, vilo: oviol[o] ? 1 : 0 }; });
      return finalize(nodes, links);
    }
    var deg = {};
    FE.forEach(function (e) { deg[e['from']] = (deg[e['from']] || 0) + (e.weight || 1); deg[e['to']] = (deg[e['to']] || 0) + (e.weight || 1); });
    var files = Object.keys(deg).sort(function (a, b) { return deg[b] - deg[a]; });
    if (files.length > MAX_DRAW) files = files.slice(0, MAX_DRAW);
    var keep = {}; files.forEach(function (p) { keep[p] = 1; });
    var nodes2 = files.map(function (p) { return { id: p, label: p.split('/').slice(-1)[0], cls: ownerClass(owner(p)), loc: (function () { var fs = _GRAPH.files || []; for (var i = 0; i < fs.length; i++) { if (fs[i].path === p) return fs[i].loc || 0; } return 0; })(), vilo: viol[p] ? 1 : 0 }; });
    var agg2 = {};
    FE.forEach(function (e) { if (!keep[e['from']] || !keep[e['to']]) return; var k = e['from'] + '\u0001' + e['to']; agg2[k] = (agg2[k] || 0) + (e.weight || 1); });
    var links2 = Object.keys(agg2).map(function (k) { var ab = k.split('\u0001'); return { s: ab[0], t: ab[1], w: agg2[k] }; });
    return finalize(nodes2, links2);
  }

  function layout(nodes, links, W, H) {
    var by = {}, n = nodes.length || 1, i, j;
    nodes.forEach(function (nd, k) { var a = (k / n) * 6.2832; nd.x = W / 2 + Math.cos(a) * Math.min(W, H) * 0.36; nd.y = H / 2 + Math.sin(a) * Math.min(W, H) * 0.36; by[nd.id] = nd; });
    var pairs = []; links.forEach(function (l) { var a = by[l.s], b = by[l.t]; if (a && b) pairs.push([a, b]); });
    var REP = 1700, SPR = 0.035, LEN = 74, GRAV = 0.03, IT = 160;
    for (var it = 0; it < IT; it++) {
      for (i = 0; i < nodes.length; i++) {
        var a = nodes[i]; a.vx = 0; a.vy = 0;
        for (j = 0; j < nodes.length; j++) {
          if (i === j) continue; var b = nodes[j];
          var dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy; if (d2 < 1) d2 = 1;
          var d = Math.sqrt(d2), f = REP / d2; a.vx += dx / d * f; a.vy += dy / d * f;
        }
        a.vx += (W / 2 - a.x) * GRAV; a.vy += (H / 2 - a.y) * GRAV;
      }
      pairs.forEach(function (p) { var x = p[0], y = p[1]; var dx = y.x - x.x, dy = y.y - x.y; var dd = Math.sqrt(dx * dx + dy * dy) || 1; var f = (dd - LEN) * SPR, ux = dx / dd, uy = dy / dd; x.vx += ux * f; x.vy += uy * f; y.vx -= ux * f; y.vy -= uy * f; });
      nodes.forEach(function (nd) { nd.x += (nd.vx || 0) * 0.85; nd.y += (nd.vy || 0) * 0.85; if (nd.x < 8) nd.x = 8; if (nd.y < 8) nd.y = 8; if (nd.x > W - 8) nd.x = W - 8; if (nd.y > H - 8) nd.y = H - 8; });
    }
  }

  function renderGraph() {
    var svg = $('graph'); if (!svg) return;
    var scope = ($('graphScope') || {}).value || 'file';
    var m = graphModel(scope);
    var W = svg.clientWidth || 820, H = svg.clientHeight || 540;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    layout(m.nodes, m.links, W, H);
    var out = '';
    m.links.forEach(function (l) {
      var a = m.by[l.s], b = m.by[l.t]; if (!a || !b) return;
      var op = Math.min(0.9, 0.12 + l.w * 0.06);
      out += '<line x1="' + a.x.toFixed(1) + '" y1="' + a.y.toFixed(1) + '" x2="' + b.x.toFixed(1) + '" y2="' + b.y.toFixed(1) + '" class="gedge" stroke-opacity="' + op.toFixed(2) + '"></line>';
    });
    var showLabel = m.nodes.length <= 46;
    m.nodes.forEach(function (nd) {
      var cls = 'gnode g-' + nd.cls + (nd.vilo ? ' g-viol' : '') + (nd.id === _SEL ? ' g-sel' : '');
      out += '<circle cx="' + nd.x.toFixed(1) + '" cy="' + nd.y.toFixed(1) + '" r="' + nd.r + '" class="' + cls + '" data-id="' + escA(nd.id) + '"></circle>';
      if (showLabel) out += '<text x="' + nd.x.toFixed(1) + '" y="' + (nd.y + nd.r + 12).toFixed(1) + '" class="glabel" text-anchor="middle">' + escT(nd.label) + '</text>';
    });
    svg.innerHTML = out;
    var cs = svg.querySelectorAll('circle');
    for (var i = 0; i < cs.length; i++) {
      (function (c) { c.addEventListener('click', function () { _SEL = c.getAttribute('data-id'); renderGraph(); showDetail(_SEL); }); })(cs[i]);
    }
  }

  function dl(list) {
    if (!list.length) return '<div class="hint">—</div>';
    return '<ul class="plain">' + list.slice(0, 24).map(function (x) { return '<li><code>' + escT(x.other) + '</code> ×' + x.w + '</li>'; }).join('') + '</ul>';
  }
  function _symbolDrill(fileId) {
    // 文件节点 → 展开到函数/符号级：本文件定义且参与调用边的符号，其跨文件调用出/入边。
    if (!_GRAPH) return '';
    var nodes = _GRAPH.nodes || [], edges = _GRAPH.edges || [];
    var inSet = {};
    nodes.forEach(function (n) { if (n.file === fileId) inSet[n.id] = 1; });
    if (!Object.keys(inSet).length) return '';
    var outMap = {}, inMap = {};
    edges.forEach(function (e) {
      if (inSet[e['from']] && !inSet[e['to']]) {
        (outMap[e['from']] = outMap[e['from']] || []).push({ to: e['to'], w: e['weight'], xfile: true });
      } else if (inSet[e['from']] && inSet[e['to']]) {
        (outMap[e['from']] = outMap[e['from']] || []).push({ to: e['to'], w: e['weight'], xfile: false });
      } else if (!inSet[e['from']] && inSet[e['to']]) {
        (inMap[e['to']] = inMap[e['to']] || []).push({ from: e['from'], w: e['weight'] });
      }
    });
    function symOf(id) { return id.indexOf('::') >= 0 ? id.split('::')[1] : id; }
    function fileOf(id) { return id.indexOf('::') >= 0 ? id.split('::')[0] : '?'; }
    function edgeRow(list, keyfn, mark) {
      var top = list.slice(0, 12);
      if (!top.length) return '<div class="hint">—</div>';
      return '<ul class="plain">' + top.map(function (x) {
        var other = keyfn(x), f = fileOf(other);
        return '<li><code>' + escT(symOf(other)) + '</code>'
          + (f === fileId ? '' : ' <span class="hint">← ' + escT(f.split('/').slice(-1)[0]) + '</span>')
          + (mark && x.xfile === false ? ' <span class="hint">(本文件内)</span>' : '')
          + ' <span class="hint">×' + (x.w || 1) + '</span></li>';
      }).join('') + '</ul>';
    }
    var defined = nodes.filter(function (n) { return inSet[n.id]; })
      .sort(function (a, b) { return (b.calls || 0) - (a.calls || 0) || (a.id < b.id ? -1 : 1); })
      .slice(0, 20);
    var h = '<div class="dsec"><b>函数级调用（' + defined.length + ' 个参与调用的符号）</b>';
    if (!defined.length) h += '<div class="hint">本文件无参与跨文件调用的符号（或仅 include 关系）。</div>';
    defined.forEach(function (n) {
      var o = outMap[n.id] || [], i = inMap[n.id] || [];
      h += '<div class="sym">' + escT(symOf(n.id)) + ' <span class="hint">' + escT(n.kind || '') + '</span></div>';
      h += '<div class="hint">→ 调用（' + o.length + '）</div>' + edgeRow(o, function (x) { return x.to; }, true);
      if (i.length) h += '<div class="hint">← 被调用（' + i.length + '）</div>' + edgeRow(i, function (x) { return x.from; }, false);
    });
    h += '</div>';
    return h;
  }

  function _includeDrill(fileId) {
    if (!_GRAPH || !_GRAPH.file_includes) return '';
    var list = _GRAPH.file_includes[fileId];
    if (!list || !list.length) return '';
    var h = '<div class="dsec"><b>#include（' + list.length + '）</b><ul class="plain">';
    list.forEach(function (x) {
      h += '<li><code>' + escT(x.target) + '</code>'
        + (x.resolved ? ' <span class="ok">→ ' + escT(x.resolved) + '</span>'
                       : ' <span class="hint">（未解析/系统/三方）</span>') + '</li>';
    });
    return h + '</ul></div>';
  }

  function showDetail(id) {
    var el = $('graphDetail'); if (!el) return;
    var scope = ($('graphScope') || {}).value || 'file';
    var m = graphModel(scope);
    var out = m.links.filter(function (l) { return l.s === id; }).map(function (l) { return { w: l.w, other: l.t }; }).sort(function (a, b) { return b.w - a.w; });
    var inn = m.links.filter(function (l) { return l.t === id; }).map(function (l) { return { w: l.w, other: l.s }; }).sort(function (a, b) { return b.w - a.w; });
    var nd = m.by[id] || {};
    var h = '<h3 class="mono">' + escT(id) + '</h3>';
    h += '<div class="hint">分类：' + (nd.cls || '—') + (nd.vilo ? ' · 含越权/边界依赖 ⚠' : '') + '</div>';
    h += '<div class="dsec"><b>依赖（出 ' + out.length + '）</b>' + dl(out) + '</div>';
    h += '<div class="dsec"><b>被依赖（入 ' + inn.length + '）</b>' + dl(inn) + '</div>';
    if (scope === 'file') {
      var drill = _symbolDrill(id) + _includeDrill(id);
      h += drill || '<div class="hint">本文件无函数级调用/include 明细可展开。</div>';
      h += '<div class="hint">函数级调用为静态启发式，未解析者已丢弃；include 解析仅认仓内本地包含。</div>';
    } else {
      h += '<div class="hint">切到「按文件」可下钻到函数级调用与 #include 明细。</div>';
    }
    el.innerHTML = h;
  }

  async function archLoad() {
    var g = $('graph'); if (!g) return;
    var sc = $('graphScope'); if (sc && !sc._bound) { sc._bound = 1; sc.addEventListener('change', function () { _SEL = null; renderGraph(); }); }
    busy(1);
    try {
      _GRAPH = await api('/api/graph');
      try { _DATA = await api('/api/data'); } catch (e) { _DATA = null; }
      _SEL = null; renderGraph();
      var st = _GRAPH.stats || {}, d = $('graphDetail');
      if (d) d.innerHTML = '<div class="hint">文件 ' + (st.files || 0) + ' · 文件边 ' + (st.file_edges || 0) + ' · 符号节点 ' + (st.nodes || 0) + ' · 高频符号边 ' + (st.edges_emitted || 0) + (st.truncated ? '（已截断）' : '') + '<br>点节点看依赖。越权/审计见「门禁与审计」。</div>';
    } catch (e) {
      var d2 = $('graphDetail'); if (d2) d2.innerHTML = '<span class="err">' + escT(e.message) + '</span><div class="hint">若提示未生成：先到「操作 › 运行与分析」点「运行完整分析」。</div>';
    }
    busy(0);
  }

  async function _data() { if (_DATA) return _DATA; _DATA = await api('/api/data'); return _DATA; }

  async function hotspotsLoad() {
    var o = $('hsOut'); if (!o) return; busy(1);
    try {
      var d = await _data(), hs = d.hotspots;
      if (!hs || !hs.files_top || !hs.files_top.length) { o.innerHTML = '<div class="hint">' + escT((hs && hs.note) || '无 git 热点数据（该仓非 git 或未取到 churn）。') + '</div>'; busy(0); return; }
      var h = '<div class="hint">' + escT(hs.note || '') + ' · 近 ' + (hs.days || 90) + ' 天</div>';
      h += '<table class="tb"><tr><th>文件</th><th class="num">churn</th><th class="num">LOC</th><th class="num">热度分</th><th>风险</th></tr>';
      hs.files_top.slice(0, 40).forEach(function (r) { h += '<tr><td><code>' + escT(r.file) + '</code></td><td class="num">' + (r.churn || 0) + '</td><td class="num">' + (r.loc || 0).toLocaleString() + '</td><td class="num">' + (r.score || 0) + '</td><td class="' + (r.high_risk ? 'bad' : '') + '">' + (r.high_risk ? '高风险' : '') + '</td></tr>'; });
      h += '</table>';
      if (hs.groups_top && hs.groups_top.length) {
        h += '<div class="hint" style="margin-top:.6rem">归属级聚合：</div><table class="tb"><tr><th>组</th><th class="num">churn</th><th class="num">文件</th><th class="num">LOC</th><th class="num">高风险</th></tr>';
        hs.groups_top.slice(0, 12).forEach(function (g) { h += '<tr><td><code>' + escT(g.group) + '</code></td><td class="num">' + g.churn + '</td><td class="num">' + g.files + '</td><td class="num">' + (g.loc || 0).toLocaleString() + '</td><td class="num">' + (g.high_risk_files || 0) + '</td></tr>'; });
        h += '</table>';
      }
      o.innerHTML = h;
    } catch (e) { o.innerHTML = '<span class="err">' + escT(e.message) + '</span>'; }
    busy(0);
  }

  async function findingsLoad() {
    var o = $('fdOut'); if (!o) return; busy(1);
    try {
      var d = await _data(), f = d.findings || {}, s = f.summary || {}, items = f.items || [];
      var errN = (s.error || 0), warnN = (s.warning || 0), infoN = (s.info || 0);
      var b = (d.interactions && d.interactions.boundary_observations && d.interactions.boundary_observations.violations) || [];
      var blocking = errN;   // 与 gate._check_findings 同口径：error 级即阻断
      var vClass = blocking > 0 ? 'bad' : (warnN > 0 ? 'warn' : 'ok');
      var vText = blocking > 0 ? ('⛔ 不放行 · 阻断 ' + blocking + ' 项（error 级发现）')
                : (warnN > 0 ? '✅ 可放行（无阻断；另有 ' + warnN + ' 条 warning）' : '✅ 可放行（未发现阻断项）');
      var h = '<div class="verdict ' + vClass + '"><div class="vtitle">' + vText + '</div>' +
              '<div class="vgrid"><span>error <b class="' + (errN ? 'bad' : '') + '">' + errN + '</b></span>' +
              '<span>warning <b>' + warnN + '</b></span><span>info <b>' + infoN + '</b></span>' +
              (b.length ? '<span>边界越权 <b class="bad">' + b.length + '</b></span>' : '') + '</div>' +
              '<div class="vn">口径：error 级发现即视为阻断（与 <code>--fail-on findings</code> 同源）。要带 LLM 语义审计或强制重跑，去「操作 › 代码审计」。</div></div>';
      h += '<div class="grid"><div class="metric"><b class="bad">' + errN + '</b><span>error</span></div><div class="metric"><b>' + warnN + '</b><span>warning</span></div><div class="metric"><b>' + infoN + '</b><span>info</span></div></div>';
      if (!items.length) h += '<div class="hint">（规则通道无命中）</div>';
      else {
        h += '<table class="tb"><tr><th>规则</th><th>级别</th><th>位置</th><th>说明</th></tr>';
        items.slice(0, 120).forEach(function (it) { var ev = (it.evidence && it.evidence[0]) || {}; h += '<tr><td><code>' + escT(it.rule_id || it.rule || '') + '</code></td><td class="' + (it.severity === 'error' ? 'bad' : '') + '">' + escT(it.severity || '') + '</td><td><code>' + escT(ev.file || '') + (ev.line ? (':' + ev.line) : '') + '</code></td><td>' + escT(it.title || it.message || '') + '</td></tr>'; });
        h += '</table>';
      }
      h += '<div class="hint">事实源里已固化的审计发现。要重跑门禁 / 带 LLM 语义审计，去「操作 › 架构门禁 / 代码审计」。</div>';
      o.innerHTML = h;
    } catch (e) { o.innerHTML = '<span class="err">' + escT(e.message) + '</span>'; }
    busy(0);
  }

  async function symbolsSearch() {
    var o = $('symOut'); if (!o) return; var q = ($('symQ') || {}).value || '';
    if (!q.trim()) { o.innerHTML = '<div class="hint">输入符号名。</div>'; return; }
    busy(1);
    try {
      var r = await api('/api/symbols?symbol=' + encodeURIComponent(q) + '&limit=120');
      var h = '<div class="hint">命中 ' + (r.count || 0) + ' 条（' + escT(r.match || '') + '）' + (r.truncated ? ' 已截断' : '') + '</div>';
      if (!r.hits || !r.hits.length) h += '<div class="hint">（未命中）</div>';
      else {
        h += '<table class="tb"><tr><th>符号</th><th>类型</th><th>位置</th><th>归属</th></tr>';
        r.hits.forEach(function (x) { h += '<tr><td><code>' + escT(x.symbol) + '</code></td><td>' + escT(x.kind) + '</td><td><code>' + escT(x.file) + ':' + x.line + '</code></td><td>' + escT(x.owner) + '</td></tr>'; });
        h += '</table>';
      }
      o.innerHTML = h;
    } catch (e) { o.innerHTML = '<span class="err">' + escT(e.message) + '</span>'; }
    busy(0);
  }

  async function contextLoad() {
    var o = $('ctxOut'); if (!o) return; busy(1);
    try {
      var r = await fetch('/api/ai', { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}' });
      var t = await r.text(); if (!r.ok) throw new Error(t);
      _CTX = t; o.textContent = t; o.classList.remove('hint');
    } catch (e) { o.textContent = '生成失败：' + e.message; }
    busy(0);
  }
  function contextCopy() {
    if (!_CTX) { msg('请先生成上下文'); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(_CTX).then(function () { msg('已复制', false); }, function () { msg('复制失败'); }); }
    else { var ta = document.createElement('textarea'); ta.value = _CTX; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); msg('已复制', false); } catch (e) { msg('复制失败'); } document.body.removeChild(ta); }
  }

  // ---- 顶栏命令面板（Ctrl/⌘K）----
  var _NAV = [
    ['arch', '架构与依赖'], ['hotspots', '变更热点'], ['findings', '门禁与审计'],
    ['symbols', '符号与检索'], ['context', 'AI 上下文'], ['dashboard', '运行与分析'],
    ['query', '结构化查询'], ['gate', '架构门禁'], ['git', '版本控制'], ['scripts', '脚本库'],
    ['audit', '代码审计'], ['projects', '项目'], ['settings', '设置']
  ];
  function palette() {
    var ov = $('palette');
    if (!ov) {
      document.body.insertAdjacentHTML('beforeend',
        '<div id="palette" class="pal-mask" style="display:none"><div class="pal-box">' +
        '<input id="palInput" class="pal-input" placeholder="跳转视图 / 输入符号检索（回车）" autocomplete="off">' +
        '<div id="palList" class="pal-list"></div></div></div>');
      $('palInput').addEventListener('input', palFilter);
      $('palette').addEventListener('click', function (e) { if (e.target === $('palette')) palClose(); });
    }
    $('palette').style.display = 'flex'; $('palInput').value = ''; palFilter(); $('palInput').focus();
  }
  function palClose() { var o = $('palette'); if (o) o.style.display = 'none'; }
  function palFilter() {
    var v = (($('palInput') || {}).value || '').trim().toLowerCase();
    var items = _NAV.filter(function (n) { return !v || n[1].toLowerCase().indexOf(v) >= 0 || n[0].indexOf(v) >= 0; })
      .map(function (n) { return '<div class="pal-item" data-go="' + n[0] + '">' + escT(n[1]) + ' <span class="hint">/' + n[0] + '</span></div>'; });
    var out = $('palList'); if (!out) return;
    if (v) items.push('<div class="pal-item" data-go="symbols:' + escA(v) + '">检索符号 “' + escT(v) + '”</div>');
    out.innerHTML = items.join('') || '<div class="hint" style="padding:.5rem">无匹配</div>';
    var els = out.querySelectorAll('.pal-item');
    for (var i = 0; i < els.length; i++) {
      (function (el) { el.addEventListener('click', function () {
        var g = el.getAttribute('data-go') || '';
        if (g.indexOf('symbols:') === 0) { location.hash = '#/symbols'; palClose(); setTimeout(function () { var q = $('symQ'); if (q) { q.value = g.slice(8); } if (window.VRApp.symbolsSearch) window.VRApp.symbolsSearch(); }, 60); }
        else { location.hash = '#/' + g; palClose(); }
      }); })(els[i]);
    }
  }
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); palette(); }
    else if (e.key === 'Escape') { palClose(); }
  });

  // 注册到 VRApp（onclick 用），并补挂到命令面板可点视图
  window.VRApp = window.VRApp || {};
  window.VRApp.archLoad = archLoad; window.VRApp.hotspotsLoad = hotspotsLoad;
  window.VRApp.findingsLoad = findingsLoad; window.VRApp.symbolsSearch = symbolsSearch;
  window.VRApp.contextLoad = contextLoad; window.VRApp.contextCopy = contextCopy;
  window.VRApp.palette = palette;

  // ================= 添加仓库（可一次多个）引导式弹窗 =================
  var _AR = [], _AR_PROF = ['directory', 'generic-python', 'embedded', 'verorun'];
  function arBase(p) { p = String(p || '').replace(/[\\/]+$/, ''); var m = p.split(/[\\/]/); return m[m.length - 1] || ''; }
  function arRowsHtml() {
    if (!_AR.length) return '<div class="hint" style="padding:.4rem 0">还没有条目，点「＋ 再加一个仓库」。</div>';
    return _AR.map(function (r, i) {
      return '<div class="arow">' +
        '<input id="arp' + i + '" class="arp" placeholder="仓库目录，如 F:\\Github\\MyRepo" value="' + escA(r.path) + '" oninput="VRApp.addRepoSetPath(' + i + ',this.value)">' +
        '<button class="btn btn-sm" onclick="VRApp.addRepoPick(' + i + ')">选目录</button>' +
        '<input class="arn" placeholder="名字（留空=用目录名）" value="' + escA(r.name) + '" oninput="VRApp.addRepoSet(' + i + ',\'name\',this.value)">' +
        '<select class="arf" onchange="VRApp.addRepoSet(' + i + ',\'profile\',this.value)">' +
        _AR_PROF.map(function (p) { return '<option' + (p === (r.profile || 'directory') ? ' selected' : '') + '>' + escT(p) + '</option>'; }).join('') +
        '</select>' +
        '<button class="btn btn-sm" title="移除这行" onclick="VRApp.addRepoDel(' + i + ')">✕</button>' +
        '</div>' + (r.result ? '<div class="arowres ' + (r.ok ? 'ok' : 'err') + '">' + escT(r.result) + '</div>' : '');
    }).join('');
  }
  function arRender() { var el = $('arRows'); if (el) el.innerHTML = arRowsHtml(); }
  function openAddRepo() {
    _AR = [{ path: '', name: '', profile: 'directory' }];
    var ov = $('addRepoModal');
    if (!ov) {
      document.body.insertAdjacentHTML('beforeend',
        '<div id="addRepoModal" class="ar-mask" style="display:none"><div class="ar-box">' +
        '<div class="ar-head"><b>添加仓库到本机注册表</b>' +
        '<button class="btn btn-sm" onclick="VRApp.closeAddRepo()">✕</button></div>' +
        '<div class="guide"><b>💡 加进来才能在这里分析/切换。</b>' +
        '<div class="gsub">填目录即可，名字留空自动用目录名；口径选不准就用 directory（接受任意目录）。可点「＋ 再加一个」一次加多个。</div></div>' +
        '<div id="arRows" class="arrows"></div>' +
        '<div class="ar-foot"><button class="btn btn-sm" onclick="VRApp.addRepoAddRow()">＋ 再加一个仓库</button>' +
        '<span class="ar-sp"></span>' +
        '<button class="btn" onclick="VRApp.closeAddRepo()">关闭</button>' +
        '<button class="btn btn-pri" onclick="VRApp.addRepoSave()">保存并切换</button></div>' +
        '</div></div>');
      ov = $('addRepoModal');
      ov.addEventListener('click', function (e) { if (e.target === ov) closeAddRepo(); });
    }
    ov.style.display = 'flex'; arRender();
    api('/api/repos').then(function (d) {
      if (d && d.profiles && d.profiles.length) {
        _AR_PROF = d.profiles.indexOf('directory') >= 0 ? d.profiles : ['directory'].concat(d.profiles);
        arRender();
      }
    }).catch(function () {});
  }
  function closeAddRepo() { var o = $('addRepoModal'); if (o) o.style.display = 'none'; }
  function addRepoAddRow() { _AR.push({ path: '', name: '', profile: 'directory' }); arRender(); }
  function addRepoDel(i) { _AR.splice(i, 1); arRender(); }
  function addRepoSet(i, k, v) { if (_AR[i]) _AR[i][k] = v; }
  function addRepoSetPath(i, v) { if (_AR[i]) _AR[i].path = v; }
  function addRepoPick(i) {
    // 复用 app.js 的目录选择器，选中后回写到该行 path 输入框
    window.VRApp.openPathPicker('arp' + i);
    // openPathPicker 直接把值写进目标 input；用户关闭选择器后同步到 _AR
    setTimeout(function () { var el = $('arp' + i); if (el && _AR[i]) _AR[i].path = el.value; arRender(); }, 500);
  }
  async function addRepoSave() {
    // 先把可能被选择器改写的 path 值收回
    for (var i = 0; i < _AR.length; i++) { var el = $('arp' + i); if (el) _AR[i].path = el.value; }
    var todo = _AR.filter(function (r) { return r.path && r.path.trim(); });
    if (!todo.length) { msg('请先填至少一个仓库目录'); return; }
    var added = 0, first = null;
    for (var j = 0; j < _AR.length; j++) {
      var r = _AR[j]; r.ok = false; r.result = '';
      if (!r.path || !r.path.trim()) continue;
      try {
        var name = (r.name || '').trim() || arBase(r.path);
        var res = await api('/api/repos/add', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, path: r.path.trim(), profile: r.profile || 'directory', confirm: true }) });
        r.ok = true; r.name = name;
        r.result = '已添加：' + name + (res.entry && res.entry.profile ? '（口径 ' + res.entry.profile + '）' : '');
        added++; first = first || name;
      } catch (e) { r.result = '失败：' + e.message; }
    }
    arRender();
    if (added) {
      msg('已添加 ' + added + ' 个仓库' + (first ? '，切换到 ' + first : ''), false);
      if (window.VRApp.headerSwitcher) window.VRApp.headerSwitcher();
      if (first) {
        setTimeout(function () {
          var sel = $('repoSel'); if (sel) sel.value = first;
          if (window.VRApp.headerSwitch) window.VRApp.headerSwitch();
        }, 120);
      }
      setTimeout(closeAddRepo, 900);
    } else {
      msg('没有成功添加（见红字）');
    }
  }

  window.VRApp.openAddRepo = openAddRepo; window.VRApp.closeAddRepo = closeAddRepo;
  window.VRApp.addRepoAddRow = addRepoAddRow; window.VRApp.addRepoDel = addRepoDel;
  window.VRApp.addRepoSet = addRepoSet; window.VRApp.addRepoSetPath = addRepoSetPath;
  window.VRApp.addRepoPick = addRepoPick; window.VRApp.addRepoSave = addRepoSave;

  // insight 注册完成后重跑一次路由，确保默认“架构”视图即时渲染
  if (core.reroute) { try { core.reroute(); } catch (e) {} }
})();
