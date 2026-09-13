// RepoLucent · 图标系统（像素级移植自 V1 设计稿的 ic() + P 图标路径表）
// 22x22 viewBox 的线性图标；ic(name, size) 返回 inline SVG 字符串。
(function (global) {
  'use strict';
  const P = {
    grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    chart: '<path d="M4 20h16"/><path d="M7 20v-6M12 20V6M17 20v-9"/>',
    shield: '<path d="M12 3l7 2.8V11c0 4.8-3 7.9-7 10-4-2.1-7-5.2-7-10V5.8z"/><path d="M9.3 11.8l2 2 3.6-4"/>',
    puzzle: '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M12 8.5v7M8.5 12h7"/>',
    gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v3M12 18.2v3M21.2 12h-3M5.8 12h-3M18.5 5.5l-2.1 2.1M7.6 16.4l-2.1 2.1M18.5 18.5l-2.1-2.1M7.6 7.6L5.5 5.5"/>',
    server: '<rect x="3.5" y="4" width="17" height="6.5" rx="1.8"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="1.8"/><path d="M7 7.2h.01M7 16.8h.01" stroke-width="2.4"/><path d="M14 7.2h3.5M14 16.8h3.5"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3.5 3L7 15.5M12.5 15.5H17"/>',
    filter: '<path d="M4 5h16l-6.2 7.2V19l-3.6 2v-8.8z"/>',
    db: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.6 3.4 2.8 7.5 2.8s7.5-1.2 7.5-2.8v-13"/><path d="M4.5 12c0 1.6 3.4 2.8 7.5 2.8s7.5-1.2 7.5-2.8"/>',
    upload: '<path d="M12 15.5V4.5M7 9l5-4.5L17 9"/><path d="M4.5 15.5v3a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M9 2.5v3.5M15 2.5v3.5M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
    file: '<path d="M6.5 2.5h7l5 5v14h-12z"/><path d="M13.5 2.5v5h5"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    bell: '<path d="M6 9.5a6 6 0 0 1 12 0c0 4.6 1.8 5.8 1.8 5.8H4.2S6 14.1 6 9.5"/><path d="M10.4 19.5a1.8 1.8 0 0 0 3.2 0"/>',
    plus: '<path d="M12 5.5v13M5.5 12h13"/>',
    refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M20.5 3.5v5h-5"/>',
    send: '<path d="M21 3L10.5 13.5"/><path d="M21 3l-6.8 18-3.7-8.3L2.5 9z"/>',
    clip: '<path d="M8.5 12.5l6.2-6.2a3.1 3.1 0 0 1 4.4 4.4l-7.6 7.6a5.2 5.2 0 0 1-7.4-7.4L11.5 3.5"/>',
    mic: '<rect x="9.2" y="3" width="5.6" height="11" rx="2.8"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3.5"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9.5" r="1.6"/><path d="M4.5 17.5l5-5 4 4 3-3 3 3"/>',
    monitor: '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M9 20.5h6M12 16.5v4"/>',
    check: '<path d="M4.5 12.8l5 5L19.5 6.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    warn: '<path d="M12 3.5L22 20H2z"/><path d="M12 10v4.5M12 17.2h.01" stroke-width="2.2"/>',
    chevd: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
    chevr: '<path d="M9.5 6l6 6-6 6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M21.5 12H19M5 12H2.5M18.7 5.3l-1.8 1.8M7.1 16.9l-1.8 1.8M18.7 18.7l-1.8-1.8M7.1 7.1L5.3 5.3"/>',
    moon: '<path d="M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5a7 7 0 0 0 10 10z"/>',
    user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5c1.6-3.8 4.6-5.5 7.5-5.5s5.9 1.7 7.5 5.5"/>',
    more: '<circle cx="5.5" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="18.5" cy="12" r="1.1"/>',
    play: '<path d="M8 5.5l11 6.5-11 6.5z"/>',
    save: '<path d="M5 3.5h11l3.5 3.5v13.5h-14.5z"/><path d="M8 3.5v5h8M8 20.5v-6h8v6"/>',
    rows: '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
    zap: '<path d="M13 2.5L4.5 13.5H11l-1 8L18.5 10H12z"/>',
    pulse: '<path d="M3 12h4l2.5-6.5 4.5 13 2.5-6.5H21"/>',
    robot: '<rect x="4" y="7" width="16" height="12" rx="3"/><circle cx="9" cy="13" r="1.4"/><circle cx="15" cy="13" r="1.4"/><path d="M12 3.5v3.5M9 3.5h6"/>',
    branch: '<circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="12" r="2.4"/><path d="M6 8.4v7.2M8.4 6h3.6a3 3 0 0 1 3 3v.6"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 5.5V20.5"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    shieldok: '<path d="M12 3l7 2.8V11c0 4.8-3 7.9-7 10-4-2.1-7-5.2-7-10V5.8z"/><path d="M9.3 11.8l2 2 3.6-4"/>',
    chat: '<path d="M4 5.5h16a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4 3.5V16.5H4A1.5 1.5 0 0 1 2.5 15V7A1.5 1.5 0 0 1 4 5.5z"/>',
    filechk: '<path d="M6.5 2.5h7l5 5v14h-12z"/><path d="M13.5 2.5v5h5"/><path d="M8.5 14l2 2 3.5-4"/>',
    pencil: '<path d="M4 20l4-1 11-11-3-3L5 16z"/><path d="M14 5l3 3"/>',
    key: '<circle cx="8" cy="12" r="4"/><path d="M11.5 12H21M18 9.5v5"/>'
  };
  function ic(n, s) {
    s = s || 16;
    return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (P[n] || '') + '</svg>';
  }
  global.VRIcon = ic;
  if (typeof module !== 'undefined' && module.exports) module.exports = { ic: ic, P: P };
})(typeof window !== 'undefined' ? window : this);
