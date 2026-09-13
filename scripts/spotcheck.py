# -*- coding: utf-8 -*-
"""抽查 verorun_insight.json 的关键字段，验证分析质量与交付完整性。"""
import json
from collections import Counter
from pathlib import Path

d = json.load(open(Path(__file__).resolve().parent.parent / "out_real" / "verorun_insight.json",
                   encoding="utf-8"))

print("=== meta ===")
print("tool", d["meta"]["tool_version"], "| repo", d["meta"]["repo_root"],
      "| ms", d["meta"]["duration_ms"])
print("=== overview ===")
print("files", d["overview"]["total_files"], "| code_loc", d["overview"]["total_code_lines"])
print("=== core modules (21 expected) ===")
for m in d["core"]["modules"]:
    tag = "known" if m["known"] else "AUTO"
    print(f"  [{tag}] {m['name']:20s} py={m['py_files']:<4d} loc={m['loc']:>7,d} routes={m['route_count']:<3d} cls={m['class_total']:<3d} | {m['description'][:38]}")
print("=== plugin_system ===")
ps = d["core"]["plugin_system"]
bp = (ps["base_plugin"] or {}).get("methods", [])
print("base_plugin.file:", (ps["base_plugin"] or {}).get("file"))
print("base_plugin methods:", [m["name"] + "*" if m["abstract"] else m["name"] for m in bp])
print("manager_api.class:", (ps["manager_api"] or {}).get("class"),
      "| methods:", len((ps["manager_api"] or {}).get("methods", [])))
print("discovery_rules lines:", len(ps["discovery_rules"]))
print("support_modules:", len(ps["support_modules"]),
      [s["module"].split(".")[-1] for s in ps["support_modules"][:10]])
print("hooks_events counts:", {k: len(v) for k, v in ps["hooks_events"].items()})
print("=== plugins (37 expected) ===")
p = d["plugins"]
print("count:", p["count"], "| rejected:", p["rejected_dirs"])
print("agent_role dist:", dict(Counter(x["agent_role"] or "NONE" for x in p["items"])))
print("top by loc:", [(x["identifier"], x["loc"]) for x in sorted(p["items"], key=lambda x: -x["loc"])[:6]])
print("manifest enums keys:", list((p["manifest_enums"] or {}).keys()))
print("templates:", [(t["dir"].split("/")[-1], t["name"]) for t in p["framework"]["templates"]])
print("base_helpers:", [x["module"] for x in p["framework"]["base_helpers"]])
print("=== interactions ===")
i = d["interactions"]
print("plugins_import_core:", i["plugins_import_core"][:6])
print("plugins_use_base_helpers:", i["plugins_use_base_helpers"])
print("plugin_to_plugin edges:", len(i["plugin_to_plugin"]))
print("boundary violations:", len(i["boundary_observations"]["violations"]))
print("=== standards ===")
s = d["standards"]
print("manifest_required:", s["manifest_required"])
print("plugin_standard:", s["plugin_standard"]["file"], "| sections:", len(s["plugin_standard"]["sections"]))
print("agents_md headings:", len(s["agents_md"]["headings"]), s["agents_md"]["headings"][:4])
print("key_docs:", len(s["key_docs"]))
