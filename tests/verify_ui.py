# -*- coding: utf-8 -*-
"""P1a UI 侧栏化验证：进程内起 server，实际 GET 页面与静态资源。

覆盖：
  1) /            → 200，且是 V1 壳层骨架（.scr / #sider / #smain / 引用 /static/*）
  2) /static/tokens.css / icons.js / app.js → 200 且内容正确
  3) 目录穿越防护：/static/../../repo_lucent/server.py 与 URL 编码变体 → 403/404，绝不返回源码
  4) 不存在的静态资源 → 404
  5) JS 语法检查（node --check，若环境有 node）
"""
from __future__ import annotations

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

from repo_lucent import cli  # noqa: E402
from repo_lucent.server import _Handler, ThreadingHTTPServer, UI_STATIC_DIR  # noqa: E402

PORT = 8801

def _make_args(repo, out):
    a = types.SimpleNamespace()
    a.repo = str(repo); a.out = str(out)
    a.no_cache = False; a.deterministic = False
    a.tree_depth = 2; a.extra_repos = []
    a.module = None; a.plugin = None
    a.quiet = True; a.summary_only = False
    return a

def main() -> int:
    results = []

    # ---- 静态资源先做存在性与语法自检（不依赖服务） ----
    files_ok = all((UI_STATIC_DIR / f).is_file()
                   for f in ("tokens.css", "icons.js", "app.js"))
    results.append(("static_files_exist", files_ok))

    node = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
    js_ok = True
    js_detail = "node not found, skipped"
    if Path(node).exists():
        outs = []
        for f in ("icons.js", "app.js"):
            p = subprocess.run([node, "--check", str(UI_STATIC_DIR / f)],
                               capture_output=True, text=True)
            outs.append(f"{f}:rc={p.returncode}")
            if p.returncode != 0:
                js_ok = False
        js_detail = " ".join(outs)
    results.append(("js_syntax_check", js_ok))

    # ---- 起服务 ----
    repo = HERE / "tests" / "fixture_repo"
    out = Path(tempfile.mkdtemp(prefix="ui_verify_"))
    cfg = cli._setup(_make_args(repo, out))

    def analyzer(_target=None):
        ns = types.SimpleNamespace(no_cache=False, deterministic=False)
        return cli._analyze(ns, cfg)

    def summarize(data, duration):
        return dict(cli._summary_pairs(data, duration))

    def write_reports(c):
        data, _, _ = analyzer()
        return cli._write_reports(c, data)

    _Handler.state = {
        "cfg": cfg, "frontends": [], "analyzer": analyzer,
        "summarize": summarize, "write_reports": write_reports,
        "last_data": {}, "last_duration": 0, "last_cache": {},
    }
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    # v1.5.1 起 /api/* 与 /mcp 需令牌。本套件不测鉴权层（那是 verify_security 的职责），
    # 故显式走「降级无鉴权」模式；L1 Host / L2 Origin 门控仍同样生效。
    srv.auth_token = ""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    def get_raw(path):
        """用 http.client 发送原始路径（不做 .. 归一化），验证服务端穿越防护。"""
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        try:
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")
        finally:
            c.close()

    try:
        # 1) 首页是 V1 壳层骨架
        st, body = get_raw("/")
        ok = (st == 200 and 'class="scr"' in body and 'id="sider"' in body
              and 'id="smain"' in body and "/static/app.js" in body
              and "/static/tokens.css" in body)
        results.append(("index_is_v1_shell", ok))

        # 2) 静态资源可服务
        for name, needle in (("tokens.css", ".sider"),
                             ("icons.js", "VRIcon"),
                             ("app.js", "VRApp")):
            st, body = get_raw("/static/" + name)
            results.append((f"static_{name}", st == 200 and needle in body))

        # 3) 目录穿越防护（原始 + URL 编码两种）
        st, body = get_raw("/static/../../repo_lucent/server.py")
        results.append(("traversal_plain_blocked", st in (403, 404) and "ThreadingHTTPServer" not in body))
        st, body = get_raw("/static/%2e%2e%2f%2e%2e%2frepo_lucent%2fserver.py")
        results.append(("traversal_encoded_blocked", st in (403, 404) and "ThreadingHTTPServer" not in body))

        # 4) 不存在资源
        st, _ = get_raw("/static/nope.css")
        results.append(("static_missing_404", st == 404))
    finally:
        srv.shutdown()
        srv.server_close()

    fails = [n for n, ok in results if not ok]
    exit_code = 0 if not fails else 1
    (HERE / "out" / "ui_verify.json").write_text(
        json.dumps({"exit": exit_code, "js_detail": js_detail,
                    "cases": [{"name": n, "ok": bool(o)} for n, o in results]},
                   ensure_ascii=True, indent=2), encoding="utf-8")
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
