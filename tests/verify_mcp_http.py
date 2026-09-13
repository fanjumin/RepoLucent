# -*- coding: utf-8 -*-
"""MCP HTTP 分支验证（需求3 P1c）：进程内起 server，用 urllib POST /mcp 验证
initialize / tools/list / tools/call(repo.summary) / 未知方法 四用例。

HTTP 分支复用与 stdio 完全相同的 catalog.handle_jsonrpc，本测试验证「传输壳」本身
（127.0.0.1 绑定、POST 路由、body 解析、响应回写）正确，外层门控不变。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens import cli  # noqa: E402
from repo_lens.server import _Handler, ThreadingHTTPServer  # noqa: E402

def _make_args(repo, out):
    a = types.SimpleNamespace()
    a.repo = str(repo)
    a.out = str(out)
    a.no_cache = False
    a.deterministic = False
    a.tree_depth = 2
    a.extra_repos = []
    a.module = None
    a.plugin = None
    a.quiet = True
    a.summary_only = False
    return a

def main() -> int:
    import tempfile
    repo = HERE / "tests" / "fixture_repo"
    out = Path(tempfile.mkdtemp(prefix="mcp_http_"))
    cfg = cli._setup(_make_args(repo, out))

    def analyzer(_target=None):
        ns = types.SimpleNamespace(no_cache=False, deterministic=False)
        data, duration, pc = cli._analyze(ns, cfg)
        return data, duration, pc

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
    srv = ThreadingHTTPServer(("127.0.0.1", 8799), _Handler)
    # v1.5.1 起 /api/* 与 /mcp 需令牌。本套件不测鉴权层（那是 verify_security 的职责），
    # 故显式走「降级无鉴权」模式；L1 Host / L2 Origin 门控仍同样生效。
    srv.auth_token = ""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    def post(msg):
        req = urllib.request.Request(
            "http://127.0.0.1:8799/mcp",
            data=json.dumps(msg).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))

    results = []
    try:
        r = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"client": "http-verify"}})
        results.append(("initialize", "result" in r and "serverInfo" in (r.get("result") or {})))

        r = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = (r.get("result") or {}).get("tools") or []
        results.append(("tools/list", isinstance(tools, list) and len(tools) > 0))

        r = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "repo.summary", "arguments": {}}})
        ok3 = "result" in r
        results.append(("tools/call repo.summary", ok3))

        r = post({"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"})
        results.append(("unknown method → error", "error" in r))
    finally:
        srv.shutdown()
        srv.server_close()

    for name, ok in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    exit_code = 0 if all(ok for _, ok in results) else 1

    (HERE / "out" / "mcp_verify_http.json").write_text(
        json.dumps({"exit": exit_code, "cases": [{"name": n, "ok": o} for n, o in results]},
                   ensure_ascii=True, indent=2), encoding="utf-8")
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
