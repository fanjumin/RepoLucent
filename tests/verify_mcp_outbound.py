# -*- coding: utf-8 -*-
"""outbound MCP 自验证（审计 EXT-4 / 总计划 P2-1）：本工具作为**客户端**消费外部 MCP Server。

被测外部 Server 用本工具自身的 `python -m repo_lens mcp` 充当（真实子进程、真实
JSON-RPC 往返），因此不是 mock，而是端到端可复现。

用例：
1. 未配置任何 server → tools/list 与改造前完全一致（核心 8 条 + registry active 脚本数，动态基线，零行为变化）
2. 配置 stdio server → 能发现外部工具（真实 tools/list）
3. catalog 暴露 mcp.<server>.<tool> 条目
4. 外部工具调用 confirm=false → 仅 dry-run（门控不放宽）
5. 外部工具调用 confirm=true → 真实执行并返回结果
6. 未配置的 server → 拒绝（白名单生效）
7. HTTP 传输 → 通过进程内起服务验证 /mcp 端点消费
8. kind 适配器分派：内置路径可用 / 未注册 kind 拒绝 / 越权 entry 拒绝
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.request
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens import cli  # noqa: E402

REPO = HERE / "tests" / "fixture_repo"

def _write_settings(obj: dict) -> str:
    """写一份临时 settings.json（每次独立目录，避免相互污染）。"""
    d = Path(tempfile.mkdtemp(prefix="mcp_out_cfg_"))
    p = d / "settings.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)

def _payload(resp: dict):
    """取 tools/call 的 content 文本并尝试解析为 JSON。"""
    txt = "".join(c.get("text", "") for c in (resp.get("content") or []))
    try:
        return json.loads(txt), txt
    except ValueError:
        return None, txt

def _make_args(repo, out):
    a = types.SimpleNamespace()
    a.repo, a.out = str(repo), str(out)
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
    results: list[dict] = []
    out = Path(tempfile.mkdtemp(prefix="mcp_out_"))
    cfg = cli._setup(_make_args(REPO, out))
    from repo_lens.mcp import catalog

    tmp = Path(tempfile.mkdtemp(prefix="mcp_out_cfg_"))

    # 动态基线：核心工具数（取自 catalog.CORE_TOOLS，随版本增删自动跟随）
    # + registry 中 active 且 kind=tool 的脚本数。
    # 两者都是"活的资产"，故不硬编码条数，避免能力演进（如 v1.6.0 新增
    # repo.context / repo.search）导致本套件误报。
    from repo_lens.script_cmd import _load_registry
    _reg = _load_registry()
    N_ACTIVE = sum(1 for s in _reg.get("scripts", [])
                   if s.get("status") == "active" and s.get("kind") == "tool")
    BASE_N = len(catalog.CORE_TOOLS) + N_ACTIVE

    # ---------- 用例 1：无 server → 清单不变 ----------
    os.environ["REPO_LENS_SETTINGS"] = _write_settings(
        {"mcp_outbound_enabled": True, "mcp_servers": []})
    tools0 = catalog.list_tools(cfg)
    ok1 = (len(tools0) == BASE_N) and not any(t["name"].startswith("mcp.") for t in tools0)
    results.append({"case": "no_servers_list_unchanged", "ok": ok1,
                    "detail": {"count": len(tools0), "base": BASE_N,
                               "has_ext": any(t["name"].startswith("mcp.") for t in tools0)}})

    # ---------- 用例 2/3：配置 stdio server ----------
    os.environ["REPO_LENS_SETTINGS"] = _write_settings({
        "mcp_outbound_enabled": True,
        "mcp_servers": [{
            "name": "demo", "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "repo_lens", "mcp", "--repo", str(REPO), "--out", str(out)],
            "env": {"PYTHONPATH": str(HERE)},
            "timeout_s": 60,
        }],
    })
    from repo_lens.scriptlib.adapters import mcp as mcpad
    mcpad.release_all()
    tools, errors = mcpad.discover_tools()
    names = [t["name"] for t in tools]
    ok2 = (len(tools) == BASE_N) and ("repo.summary" in names) and not errors
    results.append({"case": "discover_external_tools_stdio", "ok": ok2,
                    "detail": {"count": len(tools),
                               "has_insight_summary": "repo.summary" in names,
                               "errors": errors}})

    tools1 = catalog.list_tools(cfg)
    ext = [t["name"] for t in tools1 if t["name"].startswith("mcp.")]
    ok3 = ("mcp.demo.repo.summary" in ext) and (len(tools1) == 2 * BASE_N)
    results.append({"case": "catalog_exposes_ext_tools", "ok": ok3,
                    "detail": {"total": len(tools1), "ext_count": len(ext),
                               "has_demo_summary": "mcp.demo.repo.summary" in ext}})

    # ---------- 用例 4/5：调用门控 ----------
    r4 = catalog.call_tool(cfg, "mcp.demo.repo.summary", {})
    obj4, txt4 = _payload(r4)
    ok4 = bool(obj4 is not None and obj4.get("dry_run") is True
               and obj4.get("server") == "demo" and not r4.get("isError"))
    results.append({"case": "ext_call_without_confirm_is_dryrun", "ok": ok4,
                    "detail": {"isError": r4.get("isError"), "dry_run": (obj4 or {}).get("dry_run"),
                               "server": (obj4 or {}).get("server")}})

    r5 = catalog.call_tool(cfg, "mcp.demo.repo.summary", {"confirm": True})
    obj5, txt5 = _payload(r5)
    ok5 = bool(obj5 is not None and obj5.get("isError") is False
               and obj5.get("ok") is True and bool(obj5.get("text"))
               and not r5.get("isError"))
    results.append({"case": "ext_call_confirmed_executes", "ok": ok5,
                    "detail": {"isError": r5.get("isError"),
                               "tool_isError": (obj5 or {}).get("isError"),
                               "chars": len((obj5 or {}).get("text") or ""),
                               "sample": ((obj5 or {}).get("text") or "")[:100]}})

    # ---------- 用例 6：未配置 server 拒绝 ----------
    r6 = catalog.call_tool(cfg, "mcp.nope.some_tool", {"confirm": True})
    txt6 = "".join(c.get("text", "") for c in r6.get("content", []))
    ok6 = bool(r6.get("isError")) and ("未配置的 MCP server" in txt6 or "失败" in txt6)
    results.append({"case": "unconfigured_server_rejected", "ok": ok6,
                    "detail": {"isError": r6.get("isError"), "text": txt6[:160]}})

    # ---------- 用例 7：HTTP 传输 ----------
    from repo_lens.server import _Handler, ThreadingHTTPServer
    mcpad.release_all()
    os.environ["REPO_LENS_SETTINGS"] = _write_settings({
        "mcp_outbound_enabled": True,
        "mcp_servers": [{"name": "demo_http", "transport": "http",
                         "url": "http://127.0.0.1:8801/mcp", "timeout_s": 60}],
    })

    def analyzer(_target=None):
        return cli._analyze(types.SimpleNamespace(no_cache=False, deterministic=False), cfg)

    _Handler.state = {
        "cfg": cfg, "frontends": [],
        "analyzer": analyzer,
        "summarize": lambda d, dur: dict(cli._summary_pairs(d, dur)),
        "write_reports": lambda c: cli._write_reports(c, analyzer()[0]),
        "last_data": {}, "last_duration": 0, "last_cache": {},
    }
    srv = ThreadingHTTPServer(("127.0.0.1", 8801), _Handler)
    # v1.5.1 起 /api/* 与 /mcp 需令牌。本套件不测鉴权层（那是 verify_security 的职责），
    # 故显式走「降级无鉴权」模式；L1 Host / L2 Origin 门控仍同样生效。
    srv.auth_token = ""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)
    try:
        c = mcpad.get_client("demo_http")
        htools = c.list_tools()
        hnames = [t.get("name") for t in htools]
        res = c.call_tool("repo.summary", {})
        ok7 = ("repo.summary" in hnames) and (not res.get("isError")) and bool(res.get("text"))
        results.append({"case": "http_transport_discover_and_call", "ok": ok7,
                        "detail": {"count": len(htools),
                                   "has_insight_summary": "repo.summary" in hnames,
                                   "call_isError": res.get("isError"),
                                   "chars": len(res.get("text") or "")}})
    except Exception as e:  # noqa: BLE001
        results.append({"case": "http_transport_discover_and_call", "ok": False,
                        "detail": {"error": f"{type(e).__name__}: {e}"}})
    finally:
        srv.shutdown()
        srv.server_close()
    mcpad.release_all()

    # ---------- 用例 8：kind 适配器分派 ----------
    from repo_lens.scriptlib.adapters import dispatch
    import importlib
    fake_mod = types.SimpleNamespace(main=lambda argv: 0)
    orig_import = importlib.import_module
    try:
        importlib.import_module = lambda name: fake_mod  # noqa: E731 - 桩掉导入，避免真执行脚本
        rb = dispatch({"kind": "builtin", "entry": "repo_lens.scriptlib.fake"}, [])
    finally:
        importlib.import_module = orig_import
    ru = dispatch({"kind": "quantum"}, [])
    rx = dispatch({"kind": "builtin", "entry": "os.system"}, [])
    rm = dispatch({"kind": "mcp"}, [])
    ok8 = (rb.ok and rb.exit_code == 0
           and ru.exit_code == 2 and ru.error == "unsupported_kind"
           and rx.exit_code == 2 and "拒绝" in (rx.hint or "")
           and rm.exit_code == 2)
    results.append({"case": "adapter_dispatch_by_kind", "ok": ok8,
                    "detail": {"builtin_ok": rb.ok,
                               "unsupported_kind_code": ru.exit_code,
                               "outside_pkg_rejected": (rx.hint or "")[:60] or None,
                               "mcp_missing_spec_code": rm.exit_code}})

    os.environ.pop("REPO_LENS_SETTINGS", None)
    mcpad.release_all()

    fails = [r for r in results if not r["ok"]]
    summary = {"exit": 1 if fails else 0, "cases": results}
    (HERE / "out").mkdir(parents=True, exist_ok=True)
    (HERE / "out" / "mcp_outbound_verify.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    for r in results:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['case']}")
    print("ALL PASS" if not fails else f"FAILED {len(fails)}")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
