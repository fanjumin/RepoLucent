# -*- coding: utf-8 -*-
"""一次性回归跑批：串行执行全部 verify 脚本，输出 EXIT 码。无任何删除动作。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
SUITES = ["verify_security", "verify_agents", "verify_symbols", "verify_schema",
          "verify_settings", "verify_cache", "verify_profile",
          "verify_mcp_cache_hook", "verify_mcp", "verify_mcp_http",
          "verify_ui", "verify_llm", "verify_mcp_outbound", "verify_web_gap",
          "verify_features", "verify_datedir", "verify_ui_startup",
          "verify_core_probe",
          # v1.8.0 阶段三新增：热点增量缓存 / packs 分层 / SQLite 加速层
          "verify_hotspot", "verify_packs", "verify_index",
          # v1.8.0 阶段三 3-C 新增：多 kind 适配器（EXT-2/3/5）/ LLM 流式与用量（LLM-5）
          "verify_adapters", "verify_llm_stream",
          # v2.0.0 阶段 F 新增：规则引擎（SPEC/SEC/ARCH/CMP）
          "verify_rules",
          # v2.x 新增：路径点选（fs_browse）/ 项目分组与子范围统计（projects）
          "verify_fs_browse", "verify_projects"]

py = sys.executable
fails = []
for name in SUITES:
    r = subprocess.run([py, str(TESTS / f"{name}.py")],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=560)
    line = f"{name} EXIT={r.returncode}"
    print(line)
    if r.returncode != 0:
        fails.append(name)
        tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
        print("--- tail output ---")
        print(tail)

print(f"\nRESULT: {len(SUITES) - len(fails)}/{len(SUITES)} green"
      + (f"; FAILED: {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
