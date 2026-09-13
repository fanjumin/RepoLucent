# -*- coding: utf-8 -*-
"""v1.5.1 安全加固验证：四层门控（Host/Origin/Token/最小豁免）+ 路由注册表回归。

覆盖矩阵（对应升级实施方案 P0-B 的 10 用例，另加路由回归 4 项）：
  1) 无 token 访问 /api/state            → 401
  2) 错误 token 访问 /api/state          → 401（compare_digest 恒时比较）
  3) 正确 token 访问 /api/state          → 200
  4) Host: evil.com 访问任意端点         → 403
  5) POST Origin: http://evil.com        → 403
  6) POST Origin: http://127.0.0.1:port + token → 200
  7) GET / 首页含注入 token 且页面 200
  8) /mcp（HTTP）无 token → 401；带 token initialize → 200
  9) REPOLENS_TOKEN 环境变量固定         → 用该值访问 200
 10) /static/ 目录穿越                    → 仍被拦截（403/400）
 11) 路由回归：/api/scripts（精确）与 /api/scripts/<id>（前缀兜底）都可达
 12) 路由回归：未知路径 → 404；/api/report 未生成 → 404
 13) 豁免面回归：/static/tokens.css 免 token 可达
 14) X-RepoLens-Token 与 Authorization: Bearer 两种凭据通道都有效

启动方式：python verify_security.py   （自起 127.0.0.1 随机端口服务，自测自停）
退出码 0=全 PASS；1=有 FAIL。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repo_lens import server as SR           # noqa: E402
from repo_lens.config import RepoConfig      # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(method, path, token=None, host_header=None, origin=None,
         body=None, base=None, token_header="X-RepoLens-Token"):
    """发一个真实 HTTP 请求，返回 (status, headers, body_bytes)。"""
    url = base + path
    data = None
    headers = {"Accept": "*/*"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        if token_header == "Bearer":
            headers["Authorization"] = "Bearer " + token
        else:
            headers[token_header] = token
    if host_header:
        headers["Host"] = host_header
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _json(b: bytes):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


class _Srv:
    """在独立线程里起真实 _ConsoleServer（http.server 自带 ThreadingHTTPServer）。"""

    def __init__(self, token: str, port: int):
        self.port = port
        SR._Handler.state = {
            "cfg": self._mk_cfg(),
            "frontends": [],
            "analyzer": None,
            "summarize": lambda d, du: {},
            "write_reports": lambda cfg: [],
            "last_data": {}, "last_duration": 0, "last_cache": {},
        }
        self.httpd = SR._ConsoleServer(("127.0.0.1", port), SR._Handler, token)
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        time.sleep(0.2)

    @staticmethod
    def _mk_cfg():
        tmp = Path(tempfile.mkdtemp(prefix="sec_verify_"))
        repo = HERE / "tests" / "fixture_repo"
        return RepoConfig(repo_root=repo, out_dir=tmp)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> int:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    os.environ.pop("REPOLENS_TOKEN", None)
    token, degraded = SR.resolve_auth_token()
    check("token_generated", bool(token) and not degraded,
          f"len={len(token)} degraded={degraded}")

    srv = _Srv(token, port)
    try:
        # 1) 无 token → 401
        st, _, b = _req("GET", "/api/state", base=base)
        check("no_token_401", st == 401, f"status={st} body={_json(b)}")

        # 2) 错误 token → 401
        st, _, _ = _req("GET", "/api/state", token="wrong-token-000", base=base)
        check("wrong_token_401", st == 401, f"status={st}")

        # 3) 正确 token → 200（两种凭据通道）
        st, _, _ = _req("GET", "/api/state", token=token, base=base)
        check("good_token_200", st == 200, f"status={st}")
        st, _, _ = _req("GET", "/api/state", token=token, token_header="Bearer", base=base)
        check("bearer_channel_200", st == 200, f"status={st}")

        # 4) Host: evil.com → 403（DNS rebinding 模拟）
        st, _, b = _req("GET", "/api/state", token=token,
                        host_header="evil.com", base=base)
        j = _json(b)
        check("bad_host_403", st == 403 and (j or {}).get("error", {}).get("type") == "BadHost",
              f"status={st} err={(j or {}).get('error', {}).get('type')}")

        # 5) POST 坏 Origin → 403
        st, _, b = _req("POST", "/api/analyze", token=token,
                        origin="http://evil.com", body={}, base=base)
        j = _json(b)
        check("bad_origin_403",
              st == 403 and (j or {}).get("error", {}).get("type") == "BadOrigin",
              f"status={st}")

        # 6) POST 本地 Origin + token → 放行（到达业务层；analyzer 未注入，允许 500，
        #    但绝不 401/403——证明门控已通过）
        st, _, _ = _req("POST", "/api/analyze", token=token,
                        origin=f"http://127.0.0.1:{port}", body={}, base=base)
        check("good_origin_passes_gate", st not in (401, 403), f"status={st}")

        # 7) 首页注入 token：200 + 页面含 token（豁免面：无 token 也 200）
        st, _, b = _req("GET", "/", base=base)
        page = b.decode("utf-8", errors="replace")
        check("index_200_with_token",
              st == 200 and token in page and "__REPOLENS_TOKEN__" not in page,
              f"status={st} injected={token in page}")

        # 8) /mcp：无 token 401；带 token initialize 200
        st, _, _ = _req("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1,
                                              "method": "initialize", "params": {}},
                        base=base)
        check("mcp_no_token_401", st == 401, f"status={st}")
        st, _, b = _req("POST", "/mcp", token=token,
                        body={"jsonrpc": "2.0", "id": 1,
                              "method": "initialize", "params": {}}, base=base)
        j = _json(b)
        si = (j or {}).get("result", {}).get("serverInfo", {})
        check("mcp_with_token_200", st == 200 and si.get("version"), f"status={st} serverInfo={si}")

        # 10) 目录穿越仍被拦截
        st, _, _ = _req("GET", "/static/..%2F..%2F..%2Fserver.py", base=base)
        ok10 = st in (400, 403, 404)
        st2, _, _ = _req("GET", "/static/../server.py", base=base)
        check("traversal_blocked", ok10 and st2 in (400, 403, 404),
              f"encoded={st} plain={st2}")

        # 11) 路由回归：精确 vs 前缀兜底
        st, _, b = _req("GET", "/api/scripts", token=token, base=base)
        j = _json(b)
        check("routes_exact_scripts", st == 200 and isinstance(j, dict)
              and "scripts" in j, f"status={st}")
        st, _, b = _req("GET", "/api/scripts/anchor_assert", token=token, base=base)
        j = _json(b)
        check("routes_prefix_script_one",
              st == 200 and (j or {}).get("id") == "anchor_assert", f"status={st}")

        # 12) 未知路径 404 / 未生成报告 404
        st, _, _ = _req("GET", "/api/definitely-not-here", token=token, base=base)
        check("unknown_path_404", st == 404, f"status={st}")
        st, _, _ = _req("GET", "/api/report", token=token, base=base)
        check("report_missing_404", st == 404, f"status={st}")

        # 13) 豁免面：静态资源免 token 可达
        st, _, b = _req("GET", "/static/tokens.css", base=base)
        check("static_exempt_no_token", st == 200 and b[:4] != b"", f"status={st}")
    finally:
        srv.stop()

    # 9) REPOLENS_TOKEN 环境变量固定
    fixed = "fixed-token-for-env-test-abc123"
    os.environ["REPOLENS_TOKEN"] = fixed
    try:
        t2, deg2 = SR.resolve_auth_token()
        check("env_token_fixed", t2 == fixed and not deg2)
        port2 = _free_port()
        srv2 = _Srv(fixed, port2)
        try:
            b2 = f"http://127.0.0.1:{port2}"
            st, _, _ = _req("GET", "/api/state", token=fixed, base=b2)
            check("env_token_200", st == 200, f"status={st}")
            st, _, _ = _req("GET", "/api/state", token=token, base=b2)
            check("env_token_old_rejected", st == 401, f"status={st}")
        finally:
            srv2.stop()
    finally:
        os.environ.pop("REPOLENS_TOKEN", None)

    # 降级模式：REPOLENS_TOKEN="" → 无鉴权（自担风险，显式选择）
    os.environ["REPOLENS_TOKEN"] = ""
    try:
        t3, deg3 = SR.resolve_auth_token()
        check("degrade_mode_explicit", t3 == "" and deg3)
    finally:
        os.environ.pop("REPOLENS_TOKEN", None)

    fails = [r for r in results if not r["ok"]]
    print("\n" + ("—— 安全矩阵全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：{', '.join(r['name'] for r in fails)}"))
    try:
        (HERE / "out" / "security_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": results},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[verify] 结果落盘失败：{e}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
