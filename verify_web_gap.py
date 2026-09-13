# -*- coding: utf-8 -*-
"""OPEN-D 补齐（Web 全量挂接）· 验收脚本。

覆盖新增端点（进程内起 server，真实 HTTP 往返）：
  1) GET  /api/settings              → 200，含 profile_name；密钥类明文被掩码
  2) POST /api/git/groups/add|remove → dry-run 预检 → confirm 写入 → 列表可见 → 移除还原
  3) POST /api/batch                 → 非法 command 400；未知组 404；合法组 200 且逐仓返回
  4) GET  /api/mcp-out/servers       → 200（未配置时 count=0，不报错）
  5) POST /api/mcp-out/call          → confirm=false 仅 dry-run 预览
  6) GET  /api/scripts/doctor        → 200，checked 与 results 结构完整
  7) GET  /api/scripts/manual        → text/markdown
  8) POST /api/scripts/baseline + GET baseline-diff → 基线落盘与对比往返
  9) POST /api/audit/run (with_llm)  → 200；无模型时 llm_channel.ran=false（降级不影响审计）

输出纯 ASCII JSON 到 out/web_gap_verify.json。
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
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repo_lens import cli  # noqa: E402
from repo_lens.server import _Handler, ThreadingHTTPServer  # noqa: E402

PORT = 8803
RESULTS = []


def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:160]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {str(detail)[:120]}")


def _make_args(repo, out):
    a = types.SimpleNamespace()
    a.repo = str(repo); a.out = str(out)
    a.no_cache = False; a.deterministic = False
    a.tree_depth = 2; a.extra_repos = []
    a.module = None; a.plugin = None
    a.quiet = True; a.summary_only = False
    return a


def req(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    try:
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=payload, headers=headers)
        r = c.getresponse()
        raw = r.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = raw
        return r.status, r.getheader("Content-Type") or "", data
    finally:
        c.close()


def main() -> int:
    # ---- 起服务（fixture 仓库）----
    repo = HERE / "tests" / "fixture_repo"
    out = Path(tempfile.mkdtemp(prefix="webgap_verify_"))
    cfg = cli._setup(_make_args(repo, out))

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
    # v1.5.1 起 /api/* 与 /mcp 需令牌。本套件不测鉴权层（那是 verify_security 的职责），
    # 故显式走「降级无鉴权」模式；L1 Host / L2 Origin 门控仍同样生效。
    srv.auth_token = ""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    TMP_GROUP = "_verify_gap_tmp"
    try:
        # ---- 1) /api/settings：掩码 + profile 名 ----
        sec_file = HERE / "out" / "_gap_secret_settings.json"
        sec_file.parent.mkdir(exist_ok=True)
        sec_file.write_text(json.dumps({
            "llm_key": "sk-plaintext-SHOULD_MASK",
            "llm_enabled": False, "mcp_servers": [],
        }), encoding="utf-8")
        old = os.environ.get("REPO_LENS_SETTINGS")
        os.environ["REPO_LENS_SETTINGS"] = str(sec_file)
        try:
            st, _ct, d = req("GET", "/api/settings")
            ok1 = (st == 200 and d.get("profile_name") == "verorun"
                   and "sk-plaintext-SHOULD_MASK" not in json.dumps(d)
                   and d["config"].get("llm_key") == "***masked***")
            record("settings_endpoint_masks_secrets", ok1,
                   f"status={st} profile_name={d.get('profile_name')} masked={d['config'].get('llm_key')!r}")
        finally:
            if old is None:
                os.environ.pop("REPO_LENS_SETTINGS", None)
            else:
                os.environ["REPO_LENS_SETTINGS"] = old

        # ---- 2) 组管理往返 ----
        st, _ct, d = req("POST", "/api/git/groups/add",
                         {"group": TMP_GROUP, "repo": str(repo), "confirm": False})
        ok_dry = st == 200 and d.get("dry_run") is True
        st, _ct, d = req("POST", "/api/git/groups/add",
                         {"group": TMP_GROUP, "repo": str(repo), "confirm": True})
        ok_add = st == 200 and d.get("ok") is True
        st, _ct, d = req("GET", "/api/git/groups")
        ok_seen = TMP_GROUP in d.get("groups", {})
        st, _ct, d = req("POST", "/api/git/groups/remove",
                         {"group": TMP_GROUP, "repo": str(repo), "confirm": True})
        ok_rm = st == 200 and d.get("ok") is True
        # 语义与 CLI 一致：remove 移除的是组内仓库，空组本身保留
        st, _ct, d = req("GET", "/api/git/groups")
        g = d.get("groups", {}).get(TMP_GROUP, {})
        ok_gone = all(str(r["path"]) != str(repo.resolve())
                      for r in (g.get("repos") or []))
        record("groups_add_remove_roundtrip",
               ok_dry and ok_add and ok_seen and ok_rm and ok_gone,
               f"dry={ok_dry} add={ok_add} seen={ok_seen} rm={ok_rm} repo_gone={ok_gone}")

        # ---- 3) batch：非法 command / 未知组 / 合法组 ----
        # 先放回一个临时组供 batch 用
        req("POST", "/api/git/groups/add",
            {"group": TMP_GROUP, "repo": str(repo), "confirm": True})
        st, _ct, _d = req("POST", "/api/batch", {"command": "rm -rf", "group": TMP_GROUP})
        ok_bad_cmd = st == 400
        st, _ct, _d = req("POST", "/api/batch", {"command": "log", "group": "no_such_group"})
        ok_bad_grp = st == 404
        st, _ct, d = req("POST", "/api/batch", {"command": "untracked", "group": TMP_GROUP})
        ok_batch = (st == 200 and d.get("count", 0) >= 1
                    and d["results"][0].get("repo") == str(repo.resolve()))
        req("POST", "/api/git/groups/remove",
            {"group": TMP_GROUP, "repo": str(repo), "confirm": True})
        record("batch_guard_and_run", ok_bad_cmd and ok_bad_grp and ok_batch,
               f"bad_cmd={ok_bad_cmd} bad_grp={ok_bad_grp} run={ok_batch}")

        # ---- 4) mcp-out servers（未配置 → count=0）----
        st, _ct, d = req("GET", "/api/mcp-out/servers")
        record("mcpout_servers", st == 200 and "servers" in d and d.get("count") == 0,
               f"status={st} count={d.get('count')}")

        # ---- 5) mcp-out call dry-run ----
        st, _ct, d = req("POST", "/api/mcp-out/call",
                         {"server": "x", "tool": "y", "args": {"a": "1"}, "confirm": False})
        record("mcpout_call_dry_run", st == 200 and d.get("dry_run") is True,
               f"status={st} dry_run={d.get('dry_run')}")

        # ---- 6) scripts/doctor ----
        st, _ct, d = req("GET", "/api/scripts/doctor")
        ok = st == 200 and "checked" in d and isinstance(d.get("results"), list)
        if d.get("results"):
            ok = ok and all("id" in r and "ok" in r for r in d["results"])
        record("scripts_doctor", ok, f"status={st} checked={d.get('checked')}")

        # ---- 7) scripts/manual ----
        st, ct, d = req("GET", "/api/scripts/manual")
        record("scripts_manual", st == 200 and ct.startswith("text/markdown"),
               f"status={st} ctype={ct.split(';')[0]}")

        # ---- 8) scripts baseline + diff ----
        st, _ct, d = req("POST", "/api/scripts/baseline", {"name": "_gap_base"})
        ok_snap = st == 200 and d.get("path", "").endswith(".json")
        st, _ct, d = req("GET", "/api/scripts/baseline-diff?name=_gap_base")
        ok_diff = st == 200 and all(k in d for k in ("added", "removed", "changed"))
        st, _ct, _d = req("GET", "/api/scripts/baseline-diff?name=_no_such")
        ok_miss = st == 404
        record("scripts_baseline_roundtrip", ok_snap and ok_diff and ok_miss,
               f"snap={ok_snap} diff={ok_diff} missing404={ok_miss}")

        # ---- 9) audit/run with_llm（无模型 → 降级但不影响审计）----
        st, _ct, d = req("POST", "/api/audit/run",
                         {"confirm": True, "with_llm": True})
        lc = d.get("llm_channel") or {}
        ok = (st == 200 and "verdict" in d
              and lc.get("ran") is False and lc.get("reason"))
        record("audit_with_llm_degrades", ok,
               f"status={st} llm_ran={lc.get('ran')} reason={lc.get('reason')}")
    finally:
        srv.shutdown()
        srv.server_close()
        # 清理测试残留的临时空组（不污染用户级 groups.json）
        try:
            from repo_lens.packs.gitflow.repo_group import load_groups, save_groups
            gs = load_groups()
            if TMP_GROUP in gs and not gs[TMP_GROUP].repos:
                gs.pop(TMP_GROUP)
                save_groups(gs)
        except Exception:  # noqa: BLE001
            pass

    fails = [r for r in RESULTS if not r["ok"]]
    (HERE / "out" / "web_gap_verify.json").write_text(
        json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                   ensure_ascii=True, indent=2), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
