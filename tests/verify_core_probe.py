# -*- coding: utf-8 -*-
"""无删除探针：复刻 verify_settings / verify_profile 在 settings.py 后续改动
（profile 按仓覆盖）后的关键断言。全程不删除任何文件（临时目录不清理）。

  1) settings 搜索链：REPO_LUCENT_SETTINGS 环境文件覆盖包内默认
  2) get_secret 新旧前缀别名回落
  3) profile 按仓覆盖：generic-python → 组件短路；显式点名 verorun → 基线口径
  3b) 强制显式声明：三个声明通道全空 → exit 2（不再静默回落内置 VeroRun）
  4) ToolConfig.from_settings 注入 max_*
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# 去 VeroRun 硬编码（阶段 7）：分析口径须显式声明（改造前"未声明即内置 verorun
# 回落"已删除）。本套件以随包 verorun 预设为基线口径；用例 3b 会另行清空该通道
# 以验证"真·未声明 → 退出码 2"。
os.environ.setdefault("REPO_LUCENT_PROFILE", "verorun")

results = []

def record(name, ok, detail=""):
    results.append({"case": name, "ok": bool(ok)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")

def main() -> int:
    from repo_lucent import config
    from repo_lucent.settings import (load_settings, get_secret, set_profile_override,
                                    profile_get)

    # ---- 1) 搜索链覆盖 ----
    td = tempfile.mkdtemp(prefix="core_probe_")
    env_file = Path(td) / "settings.json"
    env_file.write_text(json.dumps({"max_plugins_in_ai_context": 10}), encoding="utf-8")
    old = os.environ.get("REPO_LUCENT_SETTINGS")
    os.environ["REPO_LUCENT_SETTINGS"] = str(env_file)
    try:
        merged = load_settings()
        ok1 = merged.get("max_plugins_in_ai_context") == 10
        record("settings_chain_env_override", ok1, f"max_plugins={merged.get('max_plugins_in_ai_context')}")
    finally:
        if old is None:
            os.environ.pop("REPO_LUCENT_SETTINGS", None)
        else:
            os.environ["REPO_LUCENT_SETTINGS"] = old

    # ---- 2) get_secret 新旧前缀别名 ----
    os.environ["VR_INSIGHT_TEST_ALIAS_KEY"] = "legacy-secret"
    os.environ.pop("REPO_LUCENT_TEST_ALIAS_KEY", None)
    ok2 = get_secret("REPO_LUCENT_TEST_ALIAS_KEY") == "legacy-secret"
    os.environ["REPO_LUCENT_TEST_ALIAS_KEY"] = "new-secret"
    ok2 = ok2 and get_secret("REPO_LUCENT_TEST_ALIAS_KEY") == "new-secret"
    for k in ("VR_INSIGHT_TEST_ALIAS_KEY", "REPO_LUCENT_TEST_ALIAS_KEY"):
        os.environ.pop(k, None)
    record("get_secret_legacy_alias", ok2)

    # ---- 3) profile 按仓覆盖往返（fixture 稳定，CI 永远全绿）----
    from repo_lucent.cli import (_build_argparser, _setup, _analyze, _write_reports)
    from repo_lucent.plugin_analyzer import analyze_plugins
    cfg = _setup(_build_argparser().parse_args(
        ["--repo", str(HERE / "tests" / "fixture_repo"),
         "--out", str(Path(td) / "out")]))
    set_profile_override("generic-python")
    config.apply_profile()
    short = config.PLUGINS_DIR == "" and config.MANIFEST_NAME == ""
    empty = analyze_plugins(cfg, {})
    short = short and empty.get("count") == 0 and empty.get("items") == []
    # 改造前这里是 set_profile_override(None) → 回落"内置 verorun"。
    # 强制显式声明后已无内置回落，切回基线口径必须显式点名随包预设。
    set_profile_override("verorun")
    config.apply_profile()
    back = (config.PLUGINS_DIR == "plugins" and config.MANIFEST_NAME == "plugin.json")
    record("profile_override_roundtrip", short and back,
           f"short_circuit={short} preset_restore={back}")

    # ---- 3b) 强制显式声明：无任何声明 → exit 2 ----
    # 用 settings 文件里的 profile:None 强制清空「用户级/项目级」声明通道，
    # 再清掉环境变量与 --profile 通道，保证"真·未声明"可复现。
    null_prof = Path(td) / "null_profile.json"
    null_prof.write_text(json.dumps({"profile": None}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "REPO_LUCENT_PROFILE"}
    env["REPO_LUCENT_SETTINGS"] = str(null_prof)
    r = subprocess.run(
        [sys.executable, "-m", "repo_lucent",
         "--repo", str(HERE / "tests" / "fixture_repo"),
         "--out", str(Path(td) / "nodecl_out"), "--no-date-dir", "--no-cache"],
        cwd=str(HERE), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    msg3 = (r.stdout or "") + (r.stderr or "")
    ok3b = r.returncode == 2 and "未声明分析口径" in msg3
    record("undeclared_profile_exits_2", ok3b,
           f"exit={r.returncode} 提示={'未声明分析口径' in msg3}")

    # ---- 4) ToolConfig 注入 ----
    from repo_lucent.config import ToolConfig
    tc = ToolConfig.from_settings()
    ok4 = cfg.max_plugins_in_ai_context == tc.max_plugins_in_ai_context
    record("toolconfig_injection", ok4,
           f"max_plugins={cfg.max_plugins_in_ai_context}")

    # ---- 5) 真实仓库分析探针（阶段四：opt-in，REPOLUCENT_REAL_REPO 触发真实分析）----
    # 此前该用例仅 record SKIPPED、未真正分析，DoD 真实仓核验靠手动 CLI 闭合；
    # 现改为：设了环境变量即真正跑 _analyze + _write_reports 并断言真实产物。
    # 全程不删除、不污染仓库（产物写临时 out）。未设则维持 SKIPPED（CI 绿）。
    real_repo = os.environ.get("REPOLUCENT_REAL_REPO")
    if real_repo and Path(real_repo).is_dir():
        rargs = _build_argparser().parse_args(
            ["--repo", real_repo, "--out", str(Path(td) / "real_out")])
        rcfg = _setup(rargs)
        rdata, rdur, rpc = _analyze(rargs, rcfg, deep=False)
        rplugins = rdata.get("plugins", {}).get("count", 0)
        rroutes = sum(x.get("route_count", 0)
                      for x in rdata.get("plugins", {}).get("items", []))
        rspots = rdata.get("hotspots") or {}
        rgit_ok = rspots.get("git_ok")
        rchurn = len(rspots.get("churn") or {})
        rmdur = (rdata.get("meta") or {}).get("duration_ms") or 0
        _write_reports(rcfg, rdata, "json,md,html,ai,symbols", parse_cache=rpc)
        ok5 = (rplugins > 0 and bool(rspots) and rmdur > 0
               and bool(rdata.get("overview")))
        record("real_repo_probe", ok5,
               f"plugins={rplugins} routes={rroutes} hotspots_git_ok={rgit_ok} "
               f"churn_files={rchurn} tool_duration_ms={rmdur} "
               f"overview_ok={bool(rdata.get('overview'))}")
    else:
        record("real_repo_probe", True,
               "SKIPPED: 未设置 REPOLUCENT_REAL_REPO 环境变量"
               "（设为 VeroRun 仓库根后重跑即触发真实分析）")

    out_file = HERE / "out" / "core_probe_verify.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\n{n_ok}/{len(results)} PASS")
    return 0 if n_ok == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
