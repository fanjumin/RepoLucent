# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
h = open(r"D:/projects/verorun-code/tools/dev_insight/out/verorun_dashboard.html", encoding="utf-8").read()
i = h.find("业务插件")
print("位置:", i)
print("切片:", repr(h[i:i + 120]))
