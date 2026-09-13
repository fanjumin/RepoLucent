# -*- coding: utf-8 -*-
"""把 gen_dashboard.py 的渲染逻辑自动转换为 dashboard_view.py 函数模块。"""
import re
from pathlib import Path

src = Path(r"C:\Users\jumin\.openclaw-autoclaw\workspace\projects\RepoLucent\tests\gen_dashboard.py")
text = src.read_text(encoding="utf-8")

# 去掉头部：SRC/DST 常量与 d = json.loads(...)
cut = text.index('ov, core, plugins = d["overview"]')
head_keep = text[:text.index('SRC = Path')]
body = text[cut:]

# 去掉尾部写盘与打印
tail_marker = 'DST.write_text(html, encoding="utf-8")'
body = body[:body.index(tail_marker)]

# 组装函数：缩进 4 空格，末尾 return html
indented = "\n".join(("    " + ln if ln.strip() else ln) for ln in body.rstrip().splitlines())
fn = ('# -*- coding: utf-8 -*-\n'
      '"""动态仪表盘渲染（由 tests/gen_dashboard.py 自动转换生成，勿手改）。"""\n'
      'from __future__ import annotations\n'
      'from collections import Counter\n\n\n'
      'def render_dashboard(d: dict) -> str:\n'
      + indented + '\n    return html\n')
out = Path(r"C:\Users\jumin\.openclaw-autoclaw\workspace\projects\RepoLucent\vr_insight\dashboard_view.py")
out.write_text(fn, encoding="utf-8")
print("dashboard_view.py 已生成:", out.stat().st_size, "字节")
