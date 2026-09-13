# -*- coding: utf-8 -*-
"""UI 启动可用性验收（针对 2026-09-12 实际故障：前端卡在「加载中…」）。

故障根因两条：
  A) 旧版本进程占用端口后，新进程因 SO_REUSEADDR（Windows 允许重复绑定）静默"启动成功"，
     请求仍被旧进程受理；旧进程的 UI_STATIC_DIR 指向已改名的 vr_insight/ui/static → 静态资源 404。
  B) start_repolucent.bat 把 PYEXE（值可为 "py -3"，含空格）加了引号 → cmd 找不到程序。

本脚本断言：
  1) _ConsoleServer.allow_reuse_address is False（防回退到"静默重复绑定"）
  2) _port_in_use：占用端口 True / 空闲端口 False
  3) start() 在端口被占用时 SystemExit(2)（不再是静默启动）
  4) 真实 HTTP：/ 200 且含 RepoLucent 品牌与 REPOLUCENT 版本栏（不是旧 DEV INSIGHT）
  5) 真实 HTTP：/static/{app.js,tokens.css,icons.js} 全部 200 且非空 ← 故障核心断言
  6) 静态资源目录穿越仍被拦截（回归）
  7) start_repolucent.bat：%PYEXE% 未被引号包裹、无 where py 的旧写法

输出纯 ASCII JSON 到 out/ui_startup_verify.json。
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repo_lucent import cli, server as S  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
PORT_HTTP = 8811
PORT_BUSY = 8812
RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {str(detail)[:140]}")

def main() -> int:
    repo = HERE / "tests" / "fixture_repo"
    out = HERE / "out"

    # ---- 1) 服务器类禁用端口复用 ----
    ok1 = S._ConsoleServer.allow_reuse_address is False
    record("console_server_no_reuse_address", ok1,
           f"allow_reuse_address={S._ConsoleServer.allow_reuse_address}")

    # ---- 2) 端口探测 ----
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", PORT_BUSY))
    blocker.listen(1)
    try:
        busy = S._port_in_use("127.0.0.1", PORT_BUSY)
        free = S._port_in_use("127.0.0.1", PORT_HTTP)
    finally:
        blocker.close()
    record("port_in_use_probe", busy is True and free is False,
           f"busy={busy} free={free}")

    # ---- 3) 端口被占用时不再静默启动 ----
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", PORT_BUSY))
    blocker.listen(1)
    code = None
    try:
        cfg = cli._setup(types.SimpleNamespace(
            repo=str(repo), out=str(out), no_cache=False, deterministic=False,
            tree_depth=2, extra_repos=[], module=None, plugin=None,
            quiet=True, summary_only=False))
        S.start(cfg, [], lambda t=None: ({}, 0, {}), lambda d, x: {},
                lambda c: [], port=PORT_BUSY)
        code = "NO_EXIT"
    except SystemExit as e:
        code = e.code
    except Exception as e:  # noqa: BLE001
        code = f"{type(e).__name__}:{e}"
    finally:
        blocker.close()
    record("start_refuses_busy_port", code == 2, f"exit={code}")

    # ---- 4/5/6) 真实 HTTP：起服务并断言前端资源 ----
    cfg = cli._setup(types.SimpleNamespace(
        repo=str(repo), out=str(out), no_cache=False, deterministic=False,
        tree_depth=2, extra_repos=[], module=None, plugin=None,
        quiet=True, summary_only=False))

    def analyzer(_t=None):
        ns = types.SimpleNamespace(no_cache=False, deterministic=False)
        return cli._analyze(ns, cfg)

    S._Handler.state = {
        "cfg": cfg, "frontends": [], "analyzer": analyzer,
        "summarize": lambda d, dur: dict(cli._summary_pairs(d, dur)),
        "write_reports": lambda c: cli._write_reports(c, analyzer()[0]),
        "last_data": {}, "last_duration": 0, "last_cache": {},
    }
    # v1.5.1 起 _ConsoleServer 需显式令牌；本套件不测鉴权层（verify_security 的职责），
    # 故传空串走「降级无鉴权」模式，其余门控与生产路径完全一致。
    srv = S._ConsoleServer(("127.0.0.1", PORT_HTTP), S._Handler, "")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)

    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT_HTTP}{path}",
                                        timeout=30) as r:
                return r.status, r.read()

        st, body = get("/")
        html = body.decode("utf-8", "replace")
        ok4 = (st == 200 and "RepoLucent" in html and "REPOLUCENT" in html
               and "DEV INSIGHT" not in html)
        record("index_page_brand", ok4,
               f"status={st} RepoLucent={'RepoLucent' in html} "
               f"REPOLUCENT={'REPOLUCENT' in html} old={'DEV INSIGHT' in html}")

        sizes = {}
        for f in ("app.js", "tokens.css", "icons.js"):
            st, b = get(f"/static/{f}")
            sizes[f] = [st, len(b)]
        ok5 = all(st == 200 and n > 500 for st, n in sizes.values())
        record("static_assets_served", ok5, f"{sizes}")

        # 目录穿越仍被拦截（原始 + URL 编码）
        blocked = []
        for p in ("/static/../server.py", "/static/%2e%2e%2fserver.py",
                  "/static/..%2f..%2frepolucent.py"):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT_HTTP}{p}",
                                            timeout=10) as r:
                    blocked.append(r.status)
            except urllib.error.HTTPError as e:
                blocked.append(e.code)
        ok6 = all(c in (403, 404) for c in blocked)
        record("static_traversal_blocked", ok6, f"codes={blocked}")
    finally:
        srv.shutdown()

    # ---- 7) bat 启动器引号修复 ----
    bat = (HERE / "start_repolucent.bat").read_text(encoding="utf-8", errors="replace")
    ok7 = ('"%PYEXE%"' not in bat) and ("%PYEXE%" in bat) and ("where py" not in bat)
    record("bat_pyexe_unquoted", ok7,
           f"quoted={'\"%PYEXE%\"' in bat} unquoted={'%PYEXE%' in bat} "
           f"old_where={'where py' in bat}")

    payload = {"total": len(RESULTS), "passed": sum(1 for r in RESULTS if r["ok"]),
               "failed": sum(1 for r in RESULTS if not r["ok"]), "cases": RESULTS}
    (HERE / "out" / "ui_startup_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== ui startup verify: {payload['passed']}/{payload['total']} PASS ===")
    return 0 if payload["failed"] == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
