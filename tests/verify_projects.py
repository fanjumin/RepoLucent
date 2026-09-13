# -*- coding: utf-8 -*-
"""项目分组（project_registry + /api/projects/*）验收。

  1) add_project / resolve / 重名拒绝
  2) add_member：scopes 校验（合法/非法/路径不存在/重名拒绝）
  3) remove_member 往返
  4) HTTP /api/projects/add：dry-run 门控 → confirm 落盘
  5) HTTP /api/projects/repos/add：dry-run；坏 scope 400；合法写入
  6) HTTP /api/projects/analyze 成员级 e2e：fixture 真分析，summary.files>0，
     profile 覆盖已还原（settings._PROFILE_OVERRIDE 回 None）
  7) HTTP /api/projects/analyze 子范围级 e2e（plugins，目录口径）
  8) GET /api/projects/summary：成员 + scope 行均 analyzed 且有摘要
  9) 未登记 scope 的 analyze → 400
 10) summary 未知项目 → 404

注册表经 REPO_LUCENT_HOME 重定向到临时目录，不碰真实 ~/.repolucent。
输出纯 ASCII JSON 到 out/projects_verify.json。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

TEST_HOME = Path(tempfile.mkdtemp(prefix="projreg_home_"))
os.environ["REPO_LUCENT_HOME"] = str(TEST_HOME)

from repo_lucent import cli  # noqa: E402
from repo_lucent import project_registry as PR  # noqa: E402
from repo_lucent.server import _Handler, ThreadingHTTPServer  # noqa: E402

PORT = 8822
RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:160]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {str(detail)[:120]}")

def main() -> int:
    fixture = HERE / "tests" / "fixture_repo"

    # ---- 1) 项目 CRUD ----
    ent = PR.add_project("P1")
    ok1 = (ent["name"] == "P1" and PR.resolve_project("P1") is not None)
    try:
        PR.add_project("P1")
        dup = False
    except ValueError:
        dup = True
    ok1 = ok1 and dup
    record("project_add_resolve_dup_rejected", ok1, f"dup_rejected={dup}")

    # ---- 2) 成员添加与校验 ----
    m = PR.add_member("P1", "fixture", str(fixture), None, ["plugins"])
    ok2 = (m["scopes"] == ["plugins"] and str(fixture) in m["path"])
    try:
        PR.add_member("P1", "bad_scope", str(fixture), None, ["no_such_subdir"])
        bad_scope = False
    except ValueError:
        bad_scope = True
    try:
        PR.add_member("P1", "bad_path", str(fixture / "no_dir"), None, [])
        bad_path = False
    except ValueError:
        bad_path = True
    try:
        PR.add_member("P1", "fixture", str(fixture), None, [])
        dup_m = False
    except ValueError:
        dup_m = True
    ok2 = ok2 and bad_scope and bad_path and dup_m
    record("member_add_and_validation", ok2,
           f"bad_scope={bad_scope} bad_path={bad_path} dup={dup_m}")

    # ---- 3) 成员移除往返 ----
    PR.add_member("P1", "temp", str(fixture), None, [])
    ok3 = PR.remove_member("P1", "temp") and not PR.remove_member("P1", "temp")
    record("member_remove_roundtrip", ok3, "add->remove->remove(False)")

    # ---- HTTP 层 ----
    srv_out = Path(tempfile.mkdtemp(prefix="projreg_srv_"))
    cfg = cli._setup(types.SimpleNamespace(
        repo=str(fixture), out=str(srv_out), no_cache=False, deterministic=False,
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
    srv.auth_token = ""      # 本套件不测鉴权层（verify_security 职责）
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    def req(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=300)
        try:
            payload = json.dumps(body or {}) if body is not None else None
            hdr = {"Content-Type": "application/json"} if payload else {}
            c.request(method, path, body=payload, headers=hdr)
            r = c.getresponse()
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw or "{}"), raw[:400]
            except (json.JSONDecodeError, ValueError):
                return r.status, {}, raw[:400]
        finally:
            c.close()

    try:
        # ---- 4) /api/projects/add dry-run → confirm ----
        st4a, d4a, _ = req("POST", "/api/projects/add", {"name": "HTTP1"})
        not_yet = PR.resolve_project("HTTP1") is None   # dry-run 后不得落盘
        st4b, d4b, raw4 = req("POST", "/api/projects/add",
                              {"name": "HTTP1", "confirm": True})
        created = PR.resolve_project("HTTP1") is not None
        ok4 = (st4a == 200 and d4a.get("dry_run") is True and not_yet
               and st4b == 200 and created)
        record("http_project_add_gate", ok4,
               f"dry={d4a.get('dry_run')} not_yet={not_yet} created={created} st={st4b} raw={raw4}")

        # ---- 5) /api/projects/repos/add：dry-run / 坏 scope / 合法 ----
        st5a, d5a, _ = req("POST", "/api/projects/repos/add",
                           {"project": "HTTP1", "name": "fx", "path": str(fixture)})
        st5b, d5b, raw5 = req("POST", "/api/projects/repos/add",
                              {"project": "HTTP1", "name": "fx", "path": str(fixture),
                               "scopes": ["nope"], "confirm": True})
        st5c, d5c, raw5c = req("POST", "/api/projects/repos/add",
                               {"project": "HTTP1", "name": "fx", "path": str(fixture),
                                "scopes": ["plugins"], "confirm": True})
        ok5 = (st5a == 200 and d5a.get("dry_run") is True
               and st5b == 400 and st5c == 200
               and (d5c.get("member") or {}).get("scopes") == ["plugins"])
        record("http_member_add_gate_and_scope_check", ok5,
               f"dry={d5a.get('dry_run')} bad_scope_st={st5b} ok_st={st5c} raw={raw5c}")

        # ---- 6) analyze 成员级 e2e + profile 还原 ----
        from repo_lucent import settings as S
        st6, d6, raw6 = req("POST", "/api/projects/analyze",
                            {"project": "P1", "member": "fixture", "only": "json"})
        s6 = d6.get("summary") or {}
        ok6 = (st6 == 200 and s6.get("files", 0) > 0
               and getattr(S, "_PROFILE_OVERRIDE", None) is None
               and "projects" in str(d6.get("out_dir", "")))
        record("http_analyze_member_e2e", ok6,
               f"st={st6} files={s6.get('files')} out={d6.get('out_dir')} raw={raw6}")

        # ---- 7) analyze 子范围级 e2e ----
        st7, d7, raw7 = req("POST", "/api/projects/analyze",
                            {"project": "P1", "member": "fixture",
                             "scope": "plugins", "only": "json"})
        s7 = d7.get("summary") or {}
        ok7 = (st7 == 200 and s7.get("files", 0) > 0
               and str(d7.get("target")) == "fixture/plugins")
        record("http_analyze_scope_e2e", ok7,
               f"st={st7} files={s7.get('files')} raw={raw7}")

        # ---- 8) summary 聚合 ----
        st8, d8, raw8 = req("GET", "/api/projects/summary?name=P1")
        rows = d8.get("members") or []
        fx = next((r for r in rows if r.get("name") == "fixture"), {})
        sc_rows = fx.get("scopes") or []
        sc0 = sc_rows[0] if sc_rows else {}
        ok8 = (st8 == 200 and fx.get("status") == "analyzed"
               and (fx.get("summary") or {}).get("files", 0) > 0
               and sc0.get("status") == "analyzed")
        record("http_summary_aggregate", ok8,
               f"st={st8} member={fx.get('status')} scope={sc0.get('status')} raw={raw8}")

        # ---- 9) 未登记 scope → 400 ----
        st9, d9, raw9 = req("POST", "/api/projects/analyze",
                            {"project": "P1", "member": "fixture",
                             "scope": "unregistered"})
        ok9 = st9 == 400
        record("http_analyze_unregistered_scope", ok9, f"st={st9} raw={raw9}")

        # ---- 10) summary 未知项目 → 404 ----
        st10, d10, raw10 = req("GET", "/api/projects/summary?name=NOPE")
        ok10 = st10 == 404
        record("http_summary_unknown_project", ok10, f"st={st10} raw={raw10}")
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
    (outdir / "projects_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== projects verify: {passed}/{len(RESULTS)} PASS ===")
    return 0 if passed == len(RESULTS) else 1

if __name__ == "__main__":
    raise SystemExit(main())
