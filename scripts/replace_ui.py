# -*- coding: utf-8 -*-
"""用 tmp/ui_page.html 替换 server.py 的 _UI_PAGE 段（保留 handler 逻辑）。"""
from pathlib import Path

sp = Path(r"C:\Users\jumin\.openclaw-autoclaw\workspace\projects\verorun-dev-insight\vr_insight\server.py")
ui = Path(r"C:\Users\jumin\.openclaw-autoclaw\workspace\.openclaw\tmp\ui_page.html").read_text(encoding="utf-8")

text = sp.read_text(encoding="utf-8")
marker = '_UI_PAGE = r"""'
if marker not in text:
    raise SystemExit("未找到 _UI_PAGE 标记")
head = text.split(marker, 1)[0]
new = head + marker + ui + '"""' + "\n"
sp.write_text(new, encoding="utf-8")
print("server.py UI 已替换，新大小:", len(new), "字符")
