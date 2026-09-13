# -*- coding: utf-8 -*-
"""产物按日期归档（DONE-15）验收。

  1) apply_date_dir 默认开启：out_dir == <base>/<YYYY-MM-DD>，稳定根 == base
  2) enabled=False：out_dir == base（历史行为不变）
  3) settings.output.date_dir=false → 开关生效（关）
  4) 环境变量 REPO_LENS_DATE_DIR=0 覆盖 settings（关）
  5) 缓存/基线留稳定根：cache_dir、snapshot_dir 均为 base，且 != out_dir
  6) latest_artifact_dir / list_artifact_dirs 往返（多日期目录倒序）
  7) CLI e2e 默认归档：产物落在 <out>/<date>/repo_lens.json
  8) CLI e2e --no-date-dir：产物落在 <out>/repo_lens.json（旧行为）
  9) HTTP /api/artifacts：artifact_root / current / runs 正确

输出纯 ASCII JSON 到 out/datedir_verify.json。
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens import cli  # noqa: E402
from repo_lens.config import (RepoConfig, apply_date_dir, date_dir_setting,  # noqa: E402
                              latest_artifact_dir, list_artifact_dirs)
from repo_lens.server import _Handler, ThreadingHTTPServer  # noqa: E402

PORT = 8809
RESULTS = []
TODAY = dt.datetime.now().strftime("%Y-%m-%d")

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:160]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {str(detail)[:120]}")

def _write_settings(d: dict) -> Path:
    p = Path(tempfile.mkdtemp(prefix="datedir_set_")) / "settings.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p

def _with_settings(d: dict):
    """临时把 REPO_LENS_SETTINGS 指向给定配置（settings 模块带缓存，需清缓存）。"""
    from repo_lens import settings as S
    p = _write_settings(d)
    old = os.environ.get("REPO_LENS_SETTINGS")
    os.environ["REPO_LENS_SETTINGS"] = str(p)
    for attr in ("_CACHE", "_LOADED", "_SETTINGS"):
        if hasattr(S, attr):
            try:
                delattr(S, attr)
            except Exception:  # noqa: BLE001
                pass
    if hasattr(S, "load_settings"):
        try:
            S.load_settings.cache_clear()
        except Exception:  # noqa: BLE001
            pass
    return old, p

def _restore_settings(old):
    from repo_lens import settings as S
    if old is None:
        os.environ.pop("REPO_LENS_SETTINGS", None)
    else:
        os.environ["REPO_LENS_SETTINGS"] = old
    for attr in ("_CACHE", "_LOADED", "_SETTINGS"):
        if hasattr(S, attr):
            try:
                delattr(S, attr)
            except Exception:  # noqa: BLE001
                pass
    if hasattr(S, "load_settings"):
        try:
            S.load_settings.cache_clear()
        except Exception:  # noqa: BLE001
            pass

def _cli(repo: Path, out: Path, extra: list[str]):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE)
    env.pop("REPO_LENS_SETTINGS", None)
    cmd = [sys.executable, "-m", "repo_lens", "--repo", str(repo),
           "--out", str(out), "--only", "json", "--quiet"] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       cwd=str(HERE), timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def main() -> int:
    repo = HERE / "tests" / "fixture_repo"
    out = Path(tempfile.mkdtemp(prefix="datedir_verify_"))

    try:
        # ---- 1) 默认开启 ----
        base1 = out / "case1"
        cfg1 = apply_date_dir(RepoConfig(repo_root=repo, out_dir=base1), base1)
        ptr = base1 / "_latest.txt"
        ok1 = (cfg1.out_dir == base1 / TODAY and cfg1.stable_out_dir == base1
               and cfg1.out_dir.is_dir() and ptr.exists()
               and ptr.read_text(encoding="utf-8").splitlines()[0].strip() == TODAY)
        record("datedir_default_on", ok1,
               f"out={cfg1.out_dir.name} stable={cfg1.stable_out_dir.name} ptr={ptr.exists()}")

        # ---- 2) enabled=False 保持历史行为 ----
        base2 = out / "case2"
        cfg2 = apply_date_dir(RepoConfig(repo_root=repo, out_dir=base2), base2,
                              enabled=False)
        ok2 = (cfg2.out_dir == base2 and cfg2.out_dir.is_dir()
               and not any(p.is_dir() for p in base2.iterdir()))
        record("datedir_disabled_keeps_flat", ok2, f"out={cfg2.out_dir}")

        # ---- 3) settings.output.date_dir=false ----
        old_s, _p = _with_settings({"output": {"date_dir": False}})
        try:
            got3 = date_dir_setting()[0]
        finally:
            _restore_settings(old_s)
        record("datedir_settings_off", got3 is False, f"enabled={got3}")

        # ---- 4) 环境变量 REPO_LENS_DATE_DIR=0 覆盖 ----
        old_s, _p = _with_settings({"output": {"date_dir": True}})
        old_env = os.environ.get("REPO_LENS_DATE_DIR")
        os.environ["REPO_LENS_DATE_DIR"] = "0"
        try:
            got4 = date_dir_setting()[0]
        finally:
            if old_env is None:
                os.environ.pop("REPO_LENS_DATE_DIR", None)
            else:
                os.environ["REPO_LENS_DATE_DIR"] = old_env
            _restore_settings(old_s)
        record("datedir_env_override", got4 is False, f"enabled={got4}")

        # ---- 5) 缓存 / 基线留稳定根 ----
        ok5 = (cfg1.cache_dir == base1 and cfg1.snapshot_dir == base1
               and cfg1.out_dir != cfg1.cache_dir)
        record("cache_snapshot_on_stable_root", ok5,
               f"cache={cfg1.cache_dir.name} snap={cfg1.snapshot_dir.name} out={cfg1.out_dir.name}")

        # ---- 6) latest / list 往返 ----
        hist = out / "hist"
        (hist / "2026-01-01").mkdir(parents=True)
        (hist / "2026-09-12").mkdir(parents=True)
        (hist / "2026-01-01" / "repo_lens.json").write_text("{}", encoding="utf-8")
        (hist / "2026-09-12" / "repo_lens.json").write_text("{}", encoding="utf-8")
        (hist / "_latest.txt").write_text("2026-09-12\n", encoding="utf-8")
        runs = list_artifact_dirs(hist)
        lat = latest_artifact_dir(hist)
        ok6 = ([r["name"] for r in runs] == ["2026-09-12", "2026-01-01"]
               and runs[0]["file_count"] == 1
               and lat is not None and lat.name == "2026-09-12")
        record("latest_and_list_roundtrip", ok6,
               f"runs={[r['name'] for r in runs]} latest={lat.name if lat else None}")

        # ---- 7) CLI e2e 默认归档 ----
        cli_out7 = out / "cli7"
        rc7, log7 = _cli(repo, cli_out7, [])
        f7 = cli_out7 / TODAY / "repo_lens.json"
        ok7 = rc7 == 0 and f7.is_file()
        record("cli_e2e_datedir", ok7, f"rc={rc7} exists={f7.is_file()} path={f7}")

        # ---- 8) CLI e2e --no-date-dir ----
        cli_out8 = out / "cli8"
        rc8, log8 = _cli(repo, cli_out8, ["--no-date-dir"])
        f8 = cli_out8 / "repo_lens.json"
        ok8 = rc8 == 0 and f8.is_file()
        record("cli_e2e_no_datedir", ok8, f"rc={rc8} exists={f8.is_file()} path={f8}")

        # ---- 9) HTTP /api/artifacts ----
        srv_out = out / "srv"
        cfg = cli._setup(types.SimpleNamespace(
            repo=str(repo), out=str(srv_out), no_cache=False, deterministic=False,
            tree_depth=2, extra_repos=[], module=None, plugin=None,
            quiet=True, summary_only=False))

        def analyzer(_target=None):
            ns = types.SimpleNamespace(no_cache=False, deterministic=False)
            return cli._analyze(ns, cfg)

        _Handler.state = {
            "cfg": cfg, "frontends": [], "analyzer": analyzer,
            "summarize": lambda d, dur: dict(cli._summary_pairs(d, dur)),
            "write_reports": lambda c: cli._write_reports(c, analyzer()[0]),
            "last_data": {}, "last_duration": 0, "last_cache": {},
        }
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        # v1.5.1 起 /api/* 与 /mcp 需令牌。本套件不测鉴权层（那是 verify_security
        # 的职责），故显式走「降级无鉴权」模式；L1 Host / L2 Origin 门控仍同样生效。
        srv.auth_token = ""
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(1.0)

        def req(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
            try:
                c.request("GET", path)
                r = c.getresponse()
                raw = r.read().decode("utf-8", "replace")
                try:
                    return r.status, json.loads(raw or "{}"), raw[:300]
                except (json.JSONDecodeError, ValueError):
                    return r.status, {}, raw[:300]
            finally:
                c.close()

        st9, d9, raw9 = req("/api/artifacts")
        names = [r["name"] for r in (d9.get("runs") or [])]
        ok9 = (st9 == 200 and str(d9.get("artifact_root")) == str(srv_out)
               and TODAY in names
               and str(d9.get("current")) == str(srv_out / TODAY)
               and str(d9.get("latest")) == str(srv_out / TODAY))
        record("http_artifacts_endpoint", ok9,
               f"status={st9} runs={names} current={d9.get('current')} raw={raw9}")

        st9b, d9b, raw9b = req("/api/state")
        ok9b = (st9b == 200 and str(d9b.get("artifact_root")) == str(srv_out))
        record("http_state_artifact_root", ok9b,
               f"status={st9b} root={d9b.get('artifact_root')} out={d9b.get('out_dir')}")

    finally:
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass

    outdir = HERE / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in RESULTS if r["ok"])
    payload = {"total": len(RESULTS), "passed": passed,
               "failed": len(RESULTS) - passed, "cases": RESULTS}
    (outdir / "datedir_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== datedir verify: {passed}/{len(RESULTS)} PASS ===")
    return 0 if passed == len(RESULTS) else 1

if __name__ == "__main__":
    raise SystemExit(main())
