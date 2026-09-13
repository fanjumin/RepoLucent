# -*- coding: utf-8 -*-
"""验证 dashboard HTML 完整性（兼容单/双引号属性）。"""
from pathlib import Path

p = Path(r"D:\projects\verorun-code\tools\dev_insight\out\verorun_dashboard.html")
h = p.read_text(encoding="utf-8")
met_count = h.count("class='met'") + h.count('class="met"') + h.count("class='met hl'") + h.count('class="met hl"')
checks = {
    "文件生成": p.exists() and p.stat().st_size > 10000,
    "标题 v1.5.0": "v1.5.0" in h,
    "指标卡 8 项": met_count == 8,
    "核心模块 17 行": "系统核心（平台引擎）" in h and h.count("class='m'") + h.count('class="m"') >= 17 + 30,
    "插件 38 行": "业务插件" in h and "38" in h[h.find("业务插件"):h.find("业务插件") + 120],
    "热点区块": "变更热点" in h and "高风险" in h,
    "桌面端卡片": "verorun-workplace" in h,
    "边界红线": "核心直连插件" in h,
    "依赖排名": "plugins_import_core" not in h and "plugin_manager" in h[h.find("依赖排名"):h.find("依赖排名") + 200] if "依赖排名" in h else "plugin_manager" in h,
    "lang=zh-CN": 'lang="zh-CN"' in h,
    "无模板残留": all(x not in h for x in ("{esc", "{ov[", "{hs[", "{fe[", "{core[", "{plugins[", "{inter[")),
}
ok = True
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
    ok = ok and bool(v)
print("整体:", "通过" if ok else "存在失败项")
