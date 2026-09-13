# -*- coding: utf-8 -*-
"""路径点选（fs_browse + /api/fs/list）验收。

  1) list_dir(None) 起点视图：盘符非空（Windows）+ home 字段
  2) 子目录列举：隐藏目录跳过、排序、is_repo 徽标（.git 存在性）
  3) parent 指向父目录；盘符根 parent 为 None 由实现保证（此处测普通目录）
  4) 坏路径 → ValueError
  5) 文件路径 → ValueError
  6) limit 截断：truncated=true
  7) HTTP /api/fs/list 正常路径 → 200 且 dirs 一致
  8) HTTP /api/fs/list 坏路径 → 400 BadRequest

输出纯 ASCII JSON 到 out/fs_browse_verify.json。
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

from repo_lucent import cli  # noqa: E402
from repo_lucent.fs_browse import list_dir  # noqa: E402
from repo_lucent.server import _Handler, ThreadingHTTPServer  # noqa: E402

PORT = 8821
RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:160]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {str(detail)[:120]}")

def main() -> int:
    # ---- 1) 起点视图 ----
    root = list_dir(None)
    ok1 = (root["path"] is None and root["parent"] is None
           and root.get("home") == str(Path.home())
           and isinstance(root.get("roots"), list)
           and (len(root["roots"]) >= 1 or sys.platform != "win32"))
    record("roots_view", ok1, f"roots={root['roots'][:4]} home={root.get('home')}")

    # ---- 2) 子目录列举 ----
    tmp = Path(tempfile.mkdtemp(prefix="fsbrowse_"))
    (tmp / "alpha").mkdir()
    (tmp / "beta").mkdir()
    (tmp / "alpha" / ".git").mkdir()          # alpha 是 git 仓库
    (tmp / ".hidden").mkdir()
    d = list_dir(str(tmp))
    names = [x["name"] for x in d["dirs"]]
    by_name = {x["name"]: x for x in d["dirs"]}
    ok2 = (names == ["alpha", "beta"]
           and by_name["alpha"]["is_repo"] is True
           and by_name["beta"]["is_repo"] is False
           and ".hidden" not in names and d["truncated"] is False)
    record("subdirs_sorted_hidden_skipped_isrepo", ok2, f"names={names}")

    # ---- 3) parent ----
    d3 = list_dir(str(tmp / "alpha"))
    ok3 = d3["parent"] == str(tmp)
    record("parent_link", ok3, f"parent={d3['parent']}")

    # ---- 4) 坏路径 ----
    try:
        list_dir(str(tmp / "no_such_dir"))
        ok4 = False
    except ValueError:
        ok4 = True
    record("bad_path_raises", ok4, "ValueError expected")

    # ---- 5) 文件路径 ----
    f = tmp / "afile.txt"
    f.write_text("x", encoding="utf-8")
    try:
        list_dir(str(f))
        ok5 = False
    except ValueError:
        ok5 = True
    record("file_path_raises", ok5, "ValueError expected")

    # ---- 6) limit 截断 ----
    many = Path(tempfile.mkdtemp(prefix="fsbrowse_many_"))
    for i in range(6):
        (many / f"d{i}").mkdir()
    d6 = list_dir(str(many), limit=3)
    ok6 = (len(d6["dirs"]) == 3 and d6["truncated"] is True)
    record("limit_truncates", ok6, f"n={len(d6['dirs'])} truncated={d6['truncated']}")

    # ---- 7/8) HTTP 端点 ----
    srv_out = Path(tempfile.mkdtemp(prefix="fsbrowse_srv_"))
    cfg = cli._setup(types.SimpleNamespace(
        repo=str(HERE / "tests" / "fixture_repo"), out=str(srv_out),
        no_cache=False, deterministic=False, tree_depth=2, extra_repos=[],
        module=None, plugin=None, quiet=True, summary_only=False))

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

    def req(path, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        try:
            c.request(method, path, body=body)
            r = c.getresponse()
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw or "{}"), raw[:300]
            except (json.JSONDecodeError, ValueError):
                return r.status, {}, raw[:300]
        finally:
            c.close()

    try:
        st7, d7, raw7 = req(f"/api/fs/list?path={tmp}")
        got_names = [x["name"] for x in (d7.get("dirs") or [])]
        ok7 = (st7 == 200 and got_names == ["alpha", "beta"]
               and (d7.get("dirs") or [{}])[0].get("is_repo") is True)
        record("http_fs_list_ok", ok7, f"status={st7} names={got_names} raw={raw7}")

        st8, d8, raw8 = req(f"/api/fs/list?path={tmp / 'missing'}")
        ok8 = (st8 == 400 and "BadRequest" in json.dumps(d8))
        record("http_fs_list_bad_path", ok8, f"status={st8} raw={raw8}")
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
    (outdir / "fs_browse_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== fs_browse verify: {passed}/{len(RESULTS)} PASS ===")
    return 0 if passed == len(RESULTS) else 1

if __name__ == "__main__":
    raise SystemExit(main())
