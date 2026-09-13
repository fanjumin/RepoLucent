# -*- coding: utf-8 -*-
"""本轮五项功能（多仓库 / LLM 可视化配置 / 报告模板化 / 双击启动配套 / SSH 工具）验收。

  1) repo_registry 模块往返：add → resolve → duplicate 拒绝 → remove
  2) GET  /api/repos            → 200，含 profiles（verorun）与 current
  3) POST /api/repos/add|remove → dry-run → confirm → 列表可见 → 移除
  4) POST /api/repos/switch     → 切到已注册 fixture 仓，current.path 更新，缓存失效不抛错
  5) POST /api/settings/llm     → dry-run → confirm 写回（.bak 备份）→ 还原原文件
  6) report_md.apply_md_template：无模板原文等价 / 过滤重排 / 全未知 id 回退
  7) ssh_tool.check_readonly：破坏性命令拒绝、只读命令放行

输出纯 ASCII JSON 到 out/features_verify.json。
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
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
from repo_lens.server import _Handler, ThreadingHTTPServer  # noqa: E402

PORT = 8805
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
    repo = HERE / "tests" / "fixture_repo"
    out = Path(tempfile.mkdtemp(prefix="feat_verify_"))
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

    TMP_NAME = "_verify_feat_tmp"
    proj_settings = HERE / "settings.json"   # 项目级 settings.json = 工具根（与 /api/settings/llm 一致）
    try:
        # ---- 1) repo_registry 模块往返 ----
        from repo_lens import repo_registry as RR
        ent = RR.add_repo(TMP_NAME, str(repo), None)
        ok_res = RR.resolve(TMP_NAME) is not None
        dup_rejected = False
        try:
            RR.add_repo(TMP_NAME, str(repo))
        except ValueError:
            dup_rejected = True
        ok_rm = RR.remove_repo(TMP_NAME)
        ok_gone = RR.resolve(TMP_NAME) is None
        record("repo_registry_roundtrip",
               ok_res and dup_rejected and ok_rm and ok_gone and ent.get("path"),
               f"res={ok_res} dup_rej={dup_rejected} rm={ok_rm} gone={ok_gone}")

        # ---- 2) GET /api/repos ----
        st, _ct, d = req("GET", "/api/repos")
        ok2 = (st == 200 and isinstance(d.get("repos"), list)
               and "verorun" in (d.get("profiles") or [])
               and str(d.get("current", {}).get("path", "")).endswith(repo.name))
        record("repos_list_endpoint", ok2,
               f"status={st} profiles={d.get('profiles')} current={d.get('current', {}).get('name')}")

        # ---- 3) repos add/remove 往返（dry-run 门控）----
        st, _ct, d = req("POST", "/api/repos/add",
                         {"name": TMP_NAME, "path": str(repo), "confirm": False})
        ok_dry = st == 200 and d.get("dry_run") is True
        st, _ct, d = req("POST", "/api/repos/add",
                         {"name": TMP_NAME, "path": str(repo),
                          "profile": "generic-python", "confirm": True})
        ok_add = st == 200 and d.get("ok") is True
        st, _ct, d = req("GET", "/api/repos")
        seen = any(r.get("name") == TMP_NAME and r.get("profile") == "generic-python"
                   for r in d.get("repos", []))
        st, _ct, d = req("POST", "/api/repos/remove",
                         {"name": TMP_NAME, "confirm": True})
        ok_rm = st == 200 and d.get("ok") is True
        record("repos_add_remove_roundtrip",
               ok_dry and ok_add and seen and ok_rm,
               f"dry={ok_dry} add={ok_add} seen={seen} rm={ok_rm}")

        # ---- 4) repos switch（重新注册临时项后切换）----
        RR.add_repo(TMP_NAME, str(repo), None)
        st, _ct, d = req("POST", "/api/repos/switch", {"name": TMP_NAME})
        ok_sw = (st == 200 and d.get("ok") is True
                 and Path(d.get("path", "")) == repo.resolve())
        st, _ct, d = req("GET", "/api/repos")
        cur_ok = str(d.get("current", {}).get("path", "")) == str(repo.resolve())
        st, _ct, d = req("POST", "/api/repos/switch", {"name": "no_such"})
        ok_404 = st == 404
        RR.remove_repo(TMP_NAME)
        record("repos_switch_and_invalidate", ok_sw and cur_ok and ok_404,
               f"switch={ok_sw} current_updated={cur_ok} unknown_404={ok_404}")

        # ---- 5) /api/settings/llm：dry-run → confirm 写回 → 还原 ----
        orig = proj_settings.read_text(encoding="utf-8") if proj_settings.is_file() else None
        st, _ct, d = req("POST", "/api/settings/llm",
                         {"llm_enabled": True, "default_model": "qwen-plus",
                          "models": [{"name": "qwen-plus", "provider": "openai_compat",
                                      "model": "qwen-plus",
                                      "key_env": "REPO_LENS_DASHSCOPE_KEY"}],
                          "confirm": False})
        ok_dry5 = st == 200 and d.get("dry_run") is True
        st, _ct, d = req("POST", "/api/settings/llm",
                         {"llm_enabled": True, "default_model": "qwen-plus",
                          "models": [{"name": "qwen-plus", "provider": "openai_compat",
                                      "model": "qwen-plus",
                                      "key_env": "REPO_LENS_DASHSCOPE_KEY"}],
                          "confirm": True})
        written = proj_settings.is_file() and d.get("ok") is True
        bak = proj_settings.with_suffix(".json.bak")
        bak_ok = bak.is_file()
        on_disk = json.loads(proj_settings.read_text(encoding="utf-8")) if written else {}
        ok_llm = (on_disk.get("llm_enabled") is True
                  and (on_disk.get("models") or [{}])[0].get("key_env")
                  == "REPO_LENS_DASHSCOPE_KEY"
                  and "sk-" not in proj_settings.read_text(encoding="utf-8"))
        # 零删除还原：覆写回原内容（无原文件则覆写为 {}）；.bak 只检不删
        restore = orig if orig is not None else "{}\n"
        proj_settings.write_text(restore, encoding="utf-8")
        st, _ct, d = req("GET", "/api/settings")
        env_ok = st == 200 and "env_status" in d
        record("settings_llm_writeback",
               ok_dry5 and written and bak_ok and ok_llm and env_ok,
               f"dry={ok_dry5} written={written} bak={bak_ok} disk_ok={ok_llm} env_status={env_ok}")

        # ---- 6) 报告模板：过滤/重排/回退 ----
        from repo_lens.report_md import apply_md_template
        md = ("# T\n> head\n## 1. A\nsec1\n## 2. B\nsec2\n## 3. C\nsec3\n---\n*sign*\n")
        t_none = apply_md_template(md, None) == md and apply_md_template(md, []) == md
        t_re = apply_md_template(md, ["core", "overview"])
        t_ok = (t_re.startswith("# T") and t_re.index("sec3") < t_re.index("sec1")
                and "sec2" not in t_re and "*sign*" in t_re)
        t_fb = apply_md_template(md, ["nope"]) == md
        record("report_template_engine", t_none and t_ok and t_fb,
               f"noop={t_none} reorder={t_ok} fallback={t_fb}")

        # ---- 7) ssh_tool 只读守卫 ----
        from repo_lens.scriptlib.ssh_tool import check_readonly
        guard = (check_readonly("rm -rf /x") and check_readonly("systemctl restart nginx")
                 and check_readonly("echo x >> /etc/passwd") and check_readonly("git push origin m"))
        allow = (check_readonly("uname -a") is None and check_readonly("df -h") is None
                 and check_readonly("cat /etc/hostname") is None)
        record("ssh_tool_readonly_guard", bool(guard) and allow,
               f"deny={bool(guard)} allow={allow}")

    finally:
        srv.shutdown()
        srv.server_close()

    out_file = HERE / "out" / "features_verify.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n{n_ok}/{len(RESULTS)} PASS -> {out_file.name}")
    return 0 if n_ok == len(RESULTS) else 1

if __name__ == "__main__":
    sys.exit(main())
