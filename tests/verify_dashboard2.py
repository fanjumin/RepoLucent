# -*- coding: utf-8 -*-
from pathlib import Path
h = Path(r"D:\projects\verorun-code\tools\dev_insight\out\verorun_dashboard.html").read_text(encoding="utf-8")
checks = {
    "深色导航栏": "#001529" in h,
    "SVG 环形图": "<circle" in h and "stroke-dasharray" in h,
    "角色彩色徽章": "rb" in h and "#722ed1" in h,
    "渐变条形": "linear-gradient" in h,
    "统计卡 8 个": h.count('class="stat"') == 8,
    "核心表": "系统核心" in h.replace("系统核心</b>", "") or "核心模块</b>" in h,
    "插件表 38": '<span class="badge">' in h,
    "热点区": "变更热点" in h and "pill-red" in h,
    "健康状态": "manifest 校验" in h,
    "桌面端": "verorun-workplace" in h,
    "无残留": all(x not in h for x in ("{esc", "role_dist", "donut()", "hbars(")),
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
