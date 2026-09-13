# -*- coding: utf-8 -*-
"""导出开发文档需要的规范细节到 UTF-8 文本（供撰写《开发者必读》引用真实数据）。"""
import json
from pathlib import Path

d = json.load(open(Path(__file__).resolve().parent.parent / "out_real" / "verorun_insight.json",
                   encoding="utf-8"))
s = d["standards"]
ps = d["core"]["plugin_system"]

lines = []
def w(x=""):
    lines.append(x)

w("== agent_role 枚举 ==")
w(", ".join(s["manifest_enums"].get("agent_role", [])))
w("")
w("== category 枚举 ==")
w(", ".join(s["manifest_enums"].get("category", [])))
w("")
w("== price_type 枚举 ==")
w(", ".join(s["manifest_enums"].get("price_type", [])))
w("")
w("== plugin-standard sections ==")
for x in s["plugin_standard"]["sections"]:
    w("- " + x)
w("")
w("== key_docs ==")
for x in s["key_docs"]:
    w(f"- `{x['file']}` — {x['title']}")
w("")
w("== BasePlugin 全部方法(含私有) ==")
for m in ps["base_plugin"]["methods"]:
    tag = " [abstract]" if m["abstract"] else ""
    w(f"- {m['signature']}{tag}  // {m['docstring']}")
w("")
w("== manager_api methods ==")
for m in ps["manager_api"]["methods"][:20]:
    w(f"- {m['signature']}  // {m['docstring']}")
w("")
w("== routes per top plugin ==")
for x in sorted(d["plugins"]["items"], key=lambda a: -a["route_count"])[:8]:
    w(f"- {x['identifier']}: {x['route_count']} 路由, {x['loc']} loc")
w("")
w("== entry files ==")
for e in d["core"]["entry_files"][:12]:
    w(f"- {e['file']} ({e['loc']} loc): {e['docstring']}")
w("")
w("== support_modules ==")
for x in ps["support_modules"]:
    w(f"- {x['module']}: {x['docstring']}")

Path(__file__).resolve().parent / "doc_facts.txt"
open(Path(__file__).resolve().parent / "doc_facts.txt", "w", encoding="utf-8").write("\n".join(lines))
print("wrote", len(lines), "lines")
