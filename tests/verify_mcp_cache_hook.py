# -*- coding: utf-8 -*-
"""验证 MCP 写工具失效钩子（P0-3 极致一致性）。

不依赖真实脚本执行（monkeypatch run_script_api 为安全桩），
确定性验证：
  1) 共享分析器跨 tools/call 复用（缓存生效，不再每次重跑 5-30s）；
  2) confirm=true 的受控写后立即失效 catalog 侧 + cli 侧两份缓存；
  3) confirm=false 的 dry-run 不失效（与 server.py 写端点守卫一致）。
落盘纯 ASCII JSON（ensure_ascii=True），避免 Windows 控制台编码问题。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import repo_lucent.mcp.catalog as cat
from repo_lucent import cli

def build_cfg(repo: Path, out: Path):
    ap = cli._build_argparser()
    args = ap.parse_args(["mcp", "--repo", str(repo), "--out", str(out)])
    return cli._setup(args)

def main() -> int:
    repo = HERE / "tests" / "fixture_repo"
    out = HERE / "out"
    out.mkdir(parents=True, exist_ok=True)
    if not repo.exists():
        print("SKIP: fixture_repo missing")
        return 0

    cfg = build_cfg(repo, out / "mcp_hook")
    results = []

    # ---- 用例 1：共享分析器复用（缓存生效）----
    az1 = cat._get_analyzer(cfg)
    az1.data()  # 触发首次分析并填充
    az2 = cat._get_analyzer(cfg)
    reused = (az1 is az2) and (az1._data is not None)
    # 经 call_tool 走共享路径
    cat.call_tool(cfg, "repo.summary", {})
    cat.call_tool(cfg, "repo.summary", {})
    n_entries = len(cat._AZ_REGISTRY)
    cli_populated = cli._ANALYSIS_MEMO.get("token") is not None
    results.append({
        "case": "shared_analyzer_reuse",
        "ok": bool(reused and n_entries == 1 and cli_populated),
        "detail": {"reused": reused, "registry_entries": n_entries,
                   "cli_memo_populated": cli_populated},
    })

    # ---- 用例 2：confirm=true 受控写 → 两侧缓存即时失效 ----
    # 安全桩：不真跑脚本，只记录被调用；同时桩掉 _load_registry 让 fake_tool 可解析
    calls = []

    def fake_run(sid, argv=None, confirm=False):
        calls.append((sid, confirm))
        return {"ran": True, "sid": sid, "confirmed": confirm}

    fake_reg = {"scripts": [{"id": "fake_tool", "kind": "tool",
                             "status": "active", "inputs": [], "summary": "x"}]}

    saved_run = cat.run_script_api
    saved_load = cat._load_registry
    cat.run_script_api = fake_run
    cat._load_registry = lambda: fake_reg
    # 先确保缓存已填充
    cat.call_tool(cfg, "repo.summary", {})
    before_reg = id(cfg) in cat._AZ_REGISTRY
    before_cli = cli._ANALYSIS_MEMO.get("token") is not None
    resp = cat.call_tool(cfg, "script.fake_tool", {"confirm": True})
    after_reg = id(cfg) in cat._AZ_REGISTRY
    after_cli = cli._ANALYSIS_MEMO.get("token") is not None
    cat.run_script_api = saved_run
    cat._load_registry = saved_load
    ok2 = bool(before_reg and before_cli and not after_reg and not after_cli
               and calls == [("fake_tool", True)]
               and isinstance(resp, dict) and "content" in resp)
    results.append({
        "case": "confirm_true_invalidates_both_caches",
        "ok": ok2,
        "detail": {"before_registry": before_reg, "before_cli_memo": before_cli,
                   "after_registry": after_reg, "after_cli_memo": after_cli,
                   "write_called_with_confirm": calls},
    })

    # ---- 用例 3：confirm=false dry-run → 不失效 ----
    cat.call_tool(cfg, "repo.summary", {})  # 重新填充
    before_reg = id(cfg) in cat._AZ_REGISTRY
    before_cli = cli._ANALYSIS_MEMO.get("token") is not None
    cat.run_script_api = fake_run
    cat._load_registry = lambda: fake_reg
    cat.call_tool(cfg, "script.fake_tool", {"confirm": False})
    cat.run_script_api = saved_run
    cat._load_registry = saved_load
    after_reg = id(cfg) in cat._AZ_REGISTRY
    after_cli = cli._ANALYSIS_MEMO.get("token") is not None
    ok3 = bool(before_reg and before_cli and after_reg and after_cli)
    results.append({
        "case": "dry_run_no_invalidate",
        "ok": ok3,
        "detail": {"registry_preserved": after_reg, "cli_memo_preserved": after_cli},
    })

    fails = [r for r in results if not r["ok"]]
    summary = {"exit": 1 if fails else 0, "cases": results}
    (out / "mcp_cache_hook_verify.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary["exit"]

if __name__ == "__main__":
    raise SystemExit(main())
