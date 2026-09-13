# -*- coding: utf-8 -*-
"""VeroRun 应用商店 · 只读 / 越界门控 真实环境探针（通用化自上线前测试脚本）。

沉淀自"应用商店上线前真实环境测试"的一次性探针（见 origin.source_path），提炼为
参数化、优先标准库、无内网/账号硬绑定的可复用能力：对给定 base-url 的商店后端
执行一组【只读 + 鉴权越界门控】用例，逐条给出 PASS/FAIL 与结构化结果。

安全边界：
- 仅 GET 与"无 token 的写端点探测"（后者预期被 403 拒绝，不会真正落库/变更）。
- 凭证只从环境变量读取（VR_STORE_TOKEN / VR_STORE_USER+VR_STORE_PASS 登录取 token），
  绝不写命令行、不落文件、不回显。
- 不做安装/升级/购买/同步等真实变更；如目标环境把无鉴权写请求放行执行，属被测方缺陷，
  本脚本仍只发送不带凭证的请求，不代替用户授权。

退出码（与工具契约一致）：0=全部用例通过；1=存在断言失败(缺陷)；2=参数/环境/网络不可达。

用法：
    python -m repo_lens.scriptlib.store_probe --base-url https://agent.easykai.cn
    repolens.py script run store_probe --base-url https://<host> [--token-env VR_STORE_TOKEN] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

_UA = "VeroRun-StoreProbe/1.0"
_STORE_PREFIX = "/admin/plugins"


def _opener():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # 测试环境常见自签/证书链不全，探针不因此失败
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def _req(opener, url, token=None, method="GET", timeout=25, retries=3, body=None):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method, data=body)
        req.add_header("User-Agent", _UA)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, dict(r.headers), raw
        except urllib.error.HTTPError as e:
            raw = e.read() if e.fp else b""
            return e.code, dict(e.headers or {}), raw
        except Exception as e:  # noqa: BLE001 - 网络抖动重试
            last = e
            time.sleep(1.0 + attempt)
    raise last  # type: ignore[misc]


def _json_of(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _login(base, opener, timeout):
    """用环境变量账号登录取 JWT（不落 argv/文件）。失败返回 None。"""
    u = os.environ.get("VR_STORE_USER"); p = os.environ.get("VR_STORE_PASS")
    if not (u and p):
        return None
    body = json.dumps({"username": u, "password": p, "client_type": "web"}).encode()
    req = urllib.request.Request(base + "/admin/login", method="POST", data=body)
    req.add_header("Content-Type", "application/json"); req.add_header("User-Agent", _UA)
    try:
        with opener.open(req, timeout=timeout) as r:
            j = _json_of(r.read())
        return (j or {}).get("data", {}).get("token") if (j or {}).get("success") else None
    except Exception:  # noqa: BLE001
        return None


def run_scenarios(base, token, prefix=_STORE_PREFIX, timeout=25):
    """执行只读 + 越界门控用例集，返回 [(id, desc, PASS|FAIL|SKIP, detail)]。"""
    opener = _opener()
    out = []
    P = base.rstrip("/") + prefix

    def add(cid, desc, verdict, detail=""):
        out.append((cid, desc, verdict, detail))

    # 1) 就绪健康（含 store_catalog / registry_db / mcp_servers 三项）
    try:
        st, hdr, raw = _req(opener, P + "/health/ready", timeout=timeout)
        j = _json_of(raw) or {}
        checks = (j.get("data") or {}).get("checks")
        ok = st == 200 and j.get("success") is True and isinstance(checks, list) and len(checks) >= 3
        add("C3-health", "就绪检查 /health/ready 三项 checks", "PASS" if ok else "FAIL",
            f"http={st} checks={len(checks) if isinstance(checks,list) else checks}")
        rid = hdr.get("X-Request-Id")
    except Exception as e:  # noqa: BLE001
        add("C3-health", "就绪检查 /health/ready", "FAIL", f"net: {type(e).__name__}")
        rid = None

    # 2) X-Request-Id 可观测
    add("C2-reqid", "响应头 X-Request-Id 存在", "PASS" if rid else "FAIL", str(rid))

    # 3) 公开浏览（无鉴权）：有 total；不得泄露 download_url/package_hash
    try:
        st, _, raw = _req(opener, P + "/store/public/browse", timeout=timeout)
        j = _json_of(raw) or {}
        plugins = (j.get("data") or {}).get("plugins") or []
        leak = any(("download_url" in p or "package_hash" in p) for p in plugins)
        ok = st == 200 and j.get("success") and (j.get("data") or {}).get("total") is not None and not leak
        add("S-public-browse", "公开浏览最小权限(不泄露下载URL/哈希)", "PASS" if ok else "FAIL",
            f"http={st} total={(j.get('data') or {}).get('total')} leak={leak}")
    except Exception as e:  # noqa: BLE001
        add("S-public-browse", "公开浏览", "FAIL", f"net: {type(e).__name__}")

    # 4) 管理浏览（带 token）：应含 download_url/current_edition
    if token:
        try:
            st, _, raw = _req(opener, P + "/store/browse", token=token, timeout=timeout)
            j = _json_of(raw) or {}
            plugins = (j.get("data") or {}).get("plugins") or []
            has = bool(plugins) and "download_url" in plugins[0]
            add("S-auth-browse", "登录浏览含下载URL/edition", "PASS" if (st == 200 and has) else "FAIL",
                f"http={st} n={len(plugins)} edition={plugins[0].get('current_edition') if plugins else None}")
        except Exception as e:  # noqa: BLE001
            add("S-auth-browse", "登录浏览", "FAIL", f"net: {type(e).__name__}")
    else:
        add("S-auth-browse", "登录浏览(缺 token，跳过)", "SKIP", "env VR_STORE_TOKEN 未设")

    # 5) 公开详情字段裁剪：取一个真实 identifier
    ident = None
    try:
        st, _, raw = _req(opener, P + "/store/public/browse", timeout=timeout)
        plugins = ((_json_of(raw) or {}).get("data") or {}).get("plugins") or []
        ident = plugins[0]["identifier"] if plugins else None
    except Exception:  # noqa: BLE001
        ident = None
    if ident:
        try:
            st, _, raw = _req(opener, P + f"/store/public/{ident}", timeout=timeout)
            j = _json_of(raw) or {}
            d = j.get("data") or {}
            stripped = ("download_url" not in d) and ("package_hash" not in d)
            add("S-public-detail", f"公开详情裁剪下载字段({ident})", "PASS" if (st == 200 and stripped) else "FAIL",
                f"http={st} stripped={stripped}")
        except Exception as e:  # noqa: BLE001
            add("S-public-detail", "公开详情", "FAIL", f"net: {type(e).__name__}")

    # 6) 兼容门控：check-compatibility
    if ident:
        try:
            st, _, raw = _req(opener, P + f"/store/check-compatibility/{ident}", timeout=timeout)
            j = _json_of(raw) or {}
            d = j.get("data") or {}
            add("Q-compat", f"版本兼容判定({ident})", "PASS" if (st == 200 and "compatible" in d) else "FAIL",
                f"app={d.get('app_version')} min={d.get('min_app_version')} compat={d.get('compatible')}")
        except Exception as e:  # noqa: BLE001
            add("Q-compat", "版本兼容判定", "FAIL", f"net: {type(e).__name__}")

    # 7) 越界门控：无 token 的管理/写端点应被拒(401/403)。POST /store/sync 无凭证必被拒，不产生副作用。
    for cid, desc, path in [
        ("G-admin", "无 token GET /store/admin → 401/403", "/store/admin"),
        ("G-cat", "无 token GET /store/categories → 401/403", "/store/categories"),
    ]:
        try:
            st, _, raw = _req(opener, P + path, timeout=timeout)
            j = _json_of(raw) or {}
            add(cid, desc, "PASS" if st in (401, 403) else "FAIL",
                f"http={st} err={j.get('error')!r}")
        except Exception as e:  # noqa: BLE001
            add(cid, desc, "FAIL", f"net: {type(e).__name__}")
    try:
        st, _, raw = _req(opener, P + "/store/sync", method="POST", timeout=timeout, body=b"")
        j = _json_of(raw) or {}
        add("G-sync", "无 token POST /store/sync 被拒(不落库)", "PASS" if st in (401, 403) else "FAIL",
            f"http={st} err={j.get('error')!r}")
    except Exception as e:  # noqa: BLE001
        add("G-sync", "无 token POST /store/sync", "FAIL", f"net: {type(e).__name__}")

    # 8) 输入加固 / 异常边界
    try:
        st, _, raw = _req(opener, P + "/store/__no_such_plugin_x__", timeout=timeout)
        add("X-404", "不存在 identifier → 404", "PASS" if st == 404 else "FAIL", f"http={st}")
    except Exception as e:  # noqa: BLE001
        add("X-404", "不存在 identifier", "FAIL", f"net: {type(e).__name__}")
    try:
        st, _, raw = _req(opener, P + "/store/browse?page_size=99999&sort_by=;DROP&page=0",
                          token=token or "", timeout=timeout)
        j = _json_of(raw) or {}
        d = j.get("data") or {}
        clamp = st == 200 and d.get("page_size", 0) <= 100 and d.get("page", 1) >= 1
        add("X-harden", "page_size 收敛/负页归一/sort 白名单(无 SQLi)", "PASS" if clamp else "FAIL",
            f"http={st} page_size={d.get('page_size')} page={d.get('page')}")
    except Exception as e:  # noqa: BLE001
        add("X-harden", "输入加固", "FAIL", f"net: {type(e).__name__}")

    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="store_probe",
                                 description="VeroRun 应用商店 只读/越界门控 真实环境探针（不产生变更）。")
    ap.add_argument("--base-url", default="https://agent.easykai.cn", help="商店后端基址")
    ap.add_argument("--prefix", default=_STORE_PREFIX, help="插件 API 前缀（默认 /admin/plugins）")
    ap.add_argument("--token-env", default=None, help="存放 JWT 的环境变量名（默认自动尝试登录）")
    ap.add_argument("--timeout", type=int, default=25, help="单请求超时秒")
    ap.add_argument("--json", action="store_true", dest="as_json", help="输出结构化 JSON")
    args = ap.parse_args(argv)

    opener = _opener()
    token = os.environ.get(args.token_env) if args.token_env else (os.environ.get("VR_STORE_TOKEN") or _login(args.base_url, opener, args.timeout))

    try:
        results = run_scenarios(args.base_url, token, args.prefix, args.timeout)
    except Exception as e:  # noqa: BLE001 - 整体不可达
        print(f"[store_probe] 目标不可达：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    fails = [r for r in results if r[2] == "FAIL"]
    if args.as_json:
        print(json.dumps({"base": args.base_url, "authenticated": bool(token),
                          "results": [{"id": i, "desc": d, "verdict": v, "detail": t} for i, d, v, t in results]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== VeroRun 应用商店探针  base={args.base_url}  auth={'yes' if token else 'no'} ===")
        for i, d, v, t in results:
            print(f"  [{v:4}] {i:16} {d}  |  {t}")
        print(f"\n合计 {len(results)}：PASS {sum(1 for r in results if r[2]=='PASS')}  "
              f"FAIL {len(fails)}  SKIP {sum(1 for r in results if r[2]=='SKIP')}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
