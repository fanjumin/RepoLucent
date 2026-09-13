# -*- coding: utf-8 -*-
"""归因 281,239 行代码行：按语言 + 按目录 + TOP 文件，找出超出用户预期(22-23万)的部分。"""
import json

d = json.load(open(r"D:\projects\verorun-code\tools\dev_insight\out\verorun_insight.json",
                   encoding="utf-8"))
ov = d["overview"]
print("=== 按语言/扩展名（代码行，全量）===")
py_code = 0
total = 0
for e in ov["by_language"]:
    total += e["code"]
    if e["ext"] == ".py":
        py_code = e["code"]
    print(f"  {e['ext'] or '(none)':8s}  代码行 {e['code']:>9,}   文件 {e['files']:>5,}")
print(f"  {'合计':8s}  代码行 {total:>9,}")
print(f"  .py 占比: {py_code/total*100:.1f}%")
print()
print("=== 按顶层目录（代码行 TOP 25）===")
for e in ov["by_top_dir"][:25]:
    print(f"  {e['dir']:18s} 代码行 {e['code']:>9,}  总行 {e['lines']:>9,}  文件 {e['files']:>5,}")
print()
print("=== 代码量 TOP 25 文件 ===")
for e in ov["top_files"][:25]:
    print(f"  {e['file']:70s} 代码行 {e['code']:>7,}")
