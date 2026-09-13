# -*- coding: utf-8 -*-
"""阶段 F 规则引擎回归套件（2.0.0）。

- 纯函数级：run_rules 对空 data / 无 repo 安全。
- 合成 data：触发 spec / arch(数据派生) / cmp001 等数据派生规则。
- 合成仓库：构造临时 plugins/ 含违规 .py，断言 SEC / ARCH003 / CMP002 的 AST 规则命中。
- 端到端：fixture 仓库 analyze → data["findings"] 字段存在且结构正确。
- opt-in 真实仓（REPOLENS_REAL_REPO）：真实 analyze，断言 findings 非空；未设置则 SKIPPED（CI 绿）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

results = []

def record(name, ok, detail=""):
    results.append({"case": name, "ok": bool(ok)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")

def main() -> int:
    from repo_lens.config import RepoConfig
    from repo_lens.rules import run_rules

    # ---- 1) run_rules 对空 data / 无 repo 安全 ----
    empty = run_rules({}, None)
    ok1 = (empty["schema_version"] == "1.0"
           and empty["summary"] == {"error": 0, "warning": 0, "info": 0}
           and empty["items"] == [])
    record("run_rules_empty_safe", ok1, str(empty["summary"]))

    # ---- 2) 合成 data：spec / arch(数据派生) / cmp001 命中 ----
    data = {
        "plugins": {"items": [{
            "identifier": "demo", "agent_role": "weird_role",
            "capabilities": [], "version": "x.y", "min_app_version": "",
            "i18n_locales": [], "docs": {}, "routes": [],
        }]},
        "interactions": {
            "boundary_observations": {"violations": [
                {"module": "orchestrator", "file": "orchestrator/x.py",
                 "imports": "plugins.demo.foo"}]},
            "plugin_to_plugin": [{"from": "a", "to": ["b"]},
                                 {"from": "b", "to": ["a"]}],
        },
        "overview": {"top_files": [{"file": "huge.py", "code": 99999, "lines": 99999}]},
    }
    # HERE 无 plugins 目录 → AST 规则空；仅数据派生规则命中
    cfg = RepoConfig(repo_root=HERE, out_dir=Path(tempfile.mkdtemp(prefix="vr_")))
    fr = run_rules(data, cfg)
    ids = {f["rule_id"] for f in fr["items"]}
    expect = {"SPEC001", "SPEC002", "SPEC003", "SPEC004", "SPEC005",
              "ARCH001", "ARCH002", "CMP001"}
    ok2 = expect.issubset(ids)
    record("rules_dataderived_hit", ok2,
           f"hit={sorted(ids & expect)} missing={sorted(expect - ids)}")

    # ---- 3) 合成仓库：AST 规则（SEC / ARCH003 / CMP002）命中 ----
    td = Path(tempfile.mkdtemp(prefix="rules_repo_"))
    plug = td / "plugins" / "bad_plugin"
    plug.mkdir(parents=True)
    (plug / "plugin.json").write_text(json.dumps({
        "identifier": "bad_plugin", "agent_role": "analyzer",
        "capabilities": ["x"], "version": "1.0.0"}), encoding="utf-8")
    big = "\n".join("    x = %d" % i for i in range(200))  # 大函数体（>120 行）
    bad_py = (
        'API_KEY = "abcd1234secretvalue"\n'          # SEC001 硬编码密钥
        'import psycopg2\n'
        'conn = psycopg2.connect("host=localhost")\n'  # ARCH003 私有连接池
        'import subprocess\n'
        'subprocess.run("ls -la", shell=True)\n'        # SEC003 shell=True
        'eval("1+1")\n'                                # SEC002 eval
        'query = f"SELECT * FROM users WHERE id={uid}"\n'  # SEC004 SQL 拼接
        'def huge():\n' + big + '\n'                   # CMP002 大函数体
    )
    (plug / "bad.py").write_text(bad_py, encoding="utf-8")
    cfg2 = RepoConfig(repo_root=td, out_dir=td / "out")
    data2 = {
        "plugins": {"items": [{
            "identifier": "bad_plugin", "agent_role": "analyzer",
            "capabilities": ["x"], "version": "1.0.0", "min_app_version": "",
            "i18n_locales": ["zh"], "docs": {"readme": "README.md"}, "routes": [],
        }]},
        "interactions": {"boundary_observations": {"violations": []},
                         "plugin_to_plugin": []},
        "overview": {"top_files": []},
    }
    fr2 = run_rules(data2, cfg2)
    ids2 = {f["rule_id"] for f in fr2["items"]}
    expect2 = {"SEC001", "SEC002", "SEC003", "SEC004", "ARCH003", "CMP002"}
    ok3 = expect2.issubset(ids2)
    record("rules_ast_hit", ok3,
           f"hit={sorted(ids2 & expect2)} missing={sorted(expect2 - ids2)}")

    # ---- 4) 端到端：fixture 仓库 analyze 含 findings ----
    from repo_lens.cli import _build_argparser, _setup, _analyze
    fixture = HERE / "tests" / "fixture_repo"
    if fixture.is_dir():
        fargs = _build_argparser().parse_args(
            ["--repo", str(fixture), "--out", str(Path(tempfile.mkdtemp(prefix="fi_")))])
        fc = _setup(fargs)
        fd, _, _ = _analyze(fargs, fc)
        fnd = fd.get("findings") or {}
        ok4 = (fnd.get("schema_version") == "1.0"
               and isinstance(fnd.get("items"), list)
               and isinstance(fnd.get("summary"), dict))
        record("rules_e2e_fixture_findings", ok4, f"summary={fnd.get('summary')}")
    else:
        record("rules_e2e_fixture_findings", True, "SKIPPED: fixture_repo 缺失")

    # ---- 5) opt-in 真实仓 ----
    real = os.environ.get("REPOLENS_REAL_REPO")
    if real and Path(real).is_dir():
        rargs = _build_argparser().parse_args(
            ["--repo", real, "--out", str(Path(tempfile.mkdtemp(prefix="rl_")))])
        rc = _setup(rargs)
        rd, _, _ = _analyze(rargs, rc)
        rf = rd.get("findings") or {}
        s = rf.get("summary") or {}
        n = len(rf.get("items", []))
        # 真实仓必有 findings（VeroRun 含多种违规），且结构正确
        ok5 = (isinstance(rf.get("items"), list)
               and isinstance(s, dict) and n > 0)
        record("rules_real_repo", ok5, f"summary={s} items={n}")
    else:
        record("rules_real_repo", True, "SKIPPED: 未设置 REPOLENS_REAL_REPO")

    failed = [r["case"] for r in results if not r["ok"]]
    print(f"\nRESULT: {len(results) - len(failed)}/{len(results)} passed"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
