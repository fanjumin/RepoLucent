# -*- coding: utf-8 -*-
"""v1.6.0 阶段一 1-A 验收：AGENTS.md 三态产物。

覆盖矩阵（对应方案 1-E 的 verify_agents 7 用例，另加 2 项 CLI e2e）：
  1) workspace 模式不触碰仓库根（repo/AGENTS.md 字节与 mtime 均不变）
  2) repo 模式：仓库不存在 AGENTS.md → 新建（含品牌无关的标记块）
  3) repo 模式：已有用户内容但无标记块 → 追加到文末，用户内容一字不动
  4) repo 模式：已有标记块 → 块内替换，块外保留，且标记块唯一
  5) 幂等性：连续两次生成，文件逐字节一致（可进 Git / 可 review diff）
  6) settings.output.agents_md 三态生效（off 不生成 / repo 落仓库根 / workspace 落产物目录）
  7) 非法配置值回落 workspace（绝不因配置写错中断分析）
  8) CLI e2e：默认 workspace → AGENTS.md 只落产物目录，仓库根不变
  9) CLI e2e：--agents-md off 覆盖 settings → 不生成任何 AGENTS.md
 10) 标记匹配与品牌解耦：人为把标记块品牌词改掉，仍能被识别并替换

启动方式：python verify_agents.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/agents_verify.json。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens import cli, report_agents as RA          # noqa: E402
from repo_lens.config import RepoConfig, agents_md_setting  # noqa: E402

FIXTURE = HERE / "tests" / "fixture_repo"
RESULTS: list[dict] = []

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

def _analyze(repo: Path, out: Path):
    """对给定仓库跑一次真实分析（禁用缓存与时间戳，保证确定性）。"""
    out.mkdir(parents=True, exist_ok=True)
    cfg = RepoConfig(repo_root=repo, out_dir=out, stable_out_dir=out)
    ns = types.SimpleNamespace(no_cache=True, deterministic=True,
                               module=None, plugin=None)
    data, _dur, _pc = cli._analyze(ns, cfg)
    return cfg, data

def _mini_repo(root: Path, with_agents: str | None = None) -> Path:
    """造一个最小可分析仓库（含一个核心模块与一个插件目录）。"""
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "__init__.py").write_text(
        '"""共享设施。"""\n\n\ndef helper():\n    return 1\n', encoding="utf-8")
    pdir = root / "plugins" / "demo"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "__init__.py").write_text(
        '"""演示插件。"""\n\n\nclass Demo:\n    def ping(self):\n        return "pong"\n',
        encoding="utf-8")
    (pdir / "plugin.json").write_text(json.dumps({
        "identifier": "demo", "name": "Demo", "version": "1.0.0",
        "description": "演示", "author": "t", "min_app_version": "1.0.0",
        "agent_role": "assistant", "capabilities": [],
    }, ensure_ascii=False), encoding="utf-8")
    if with_agents is not None:
        (root / "AGENTS.md").write_text(with_agents, encoding="utf-8")
    return root

def _settings_ctx(payload: dict):
    """把 REPO_LENS_SETTINGS 临时指向给定配置；返回还原函数。"""
    p = Path(tempfile.mkdtemp(prefix="agents_set_")) / "settings.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    old = os.environ.get("REPO_LENS_SETTINGS")
    os.environ["REPO_LENS_SETTINGS"] = str(p)
    return lambda: (os.environ.__setitem__("REPO_LENS_SETTINGS", old)
                    if old is not None
                    else os.environ.pop("REPO_LENS_SETTINGS", None))

def _cli(repo: Path, out: Path, extra: list[str]):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    env.pop("REPO_LENS_SETTINGS", None)
    cmd = [sys.executable, "-m", "repo_lens", "--repo", str(repo),
           "--out", str(out), "--no-date-dir", "--quiet"] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       cwd=str(HERE), timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agents_verify_"))

    # ---- 1) workspace 模式不触碰仓库根 ----
    repo_g = HERE / "tests" / "fixture_repo"
    rp = repo_g / "AGENTS.md"
    before = rp.read_bytes()
    before_mt = rp.stat().st_mtime_ns
    cfg, data = _analyze(repo_g, tmp / "c1")
    p1 = RA.emit_agents_md(cfg, data, "workspace")
    check("workspace_mode_does_not_touch_repo_root",
          p1 == cfg.out_dir / "AGENTS.md" and p1.is_file()
          and rp.read_bytes() == before and rp.stat().st_mtime_ns == before_mt,
          f"out={p1.name} repo_unchanged={rp.read_bytes() == before}")

    # ---- 2) repo 模式新建 ----
    r2 = _mini_repo(tmp / "repo2")
    cfg2, data2 = _analyze(r2, tmp / "c2")
    p2 = RA.emit_agents_md(cfg2, data2, "repo")
    t2 = p2.read_text(encoding="utf-8") if p2 and p2.exists() else ""
    check("repo_mode_creates_file_with_markers",
          p2 == r2 / "AGENTS.md" and ":begin auto" in t2 and ":end auto" in t2,
          f"path={p2.name if p2 else None} len={len(t2)}")

    # ---- 3) repo 模式：无标记块 → 追加且保留用户内容 ----
    user_md = "# 本仓库自有约定\n\n- 先方案后执行\n- 禁止手动 push\n"
    r3 = _mini_repo(tmp / "repo3", with_agents=user_md)
    cfg3, data3 = _analyze(r3, tmp / "c3")
    RA.emit_agents_md(cfg3, data3, "repo")
    t3 = (r3 / "AGENTS.md").read_text(encoding="utf-8")
    check("repo_mode_appends_when_no_marker",
          t3.startswith(user_md) and ":begin auto" in t3 and t3.count(":begin auto") == 1,
          f"starts_with_user={t3.startswith(user_md)} len={len(t3)}")

    # ---- 4) repo 模式：标记块内替换，块外保留且块唯一 ----
    (r3 / "AGENTS.md").write_text(
        user_md + "\n<!-- repolens:begin auto（本块由 RepoLens 生成，勿手改） -->\n"
        "旧内容-应被替换\n<!-- repolens:end auto -->\n\n### 尾部手写\n尾部内容\n",
        encoding="utf-8")
    RA.emit_agents_md(cfg3, data3, "repo")
    t4 = (r3 / "AGENTS.md").read_text(encoding="utf-8")
    check("repo_mode_replaces_marker_block_only",
          "旧内容-应被替换" not in t4 and t4.count(":begin auto") == 1
          and t4.startswith(user_md) and "### 尾部手写" in t4
          and "尾部内容" in t4,
          f"begin_count={t4.count(':begin auto')} tail_kept={'尾部内容' in t4}")

    # ---- 5) 幂等性：三条路径各连跑两次，均逐字节一致 ----
    first = (r3 / "AGENTS.md").read_bytes()
    RA.emit_agents_md(cfg3, data3, "repo")
    second = (r3 / "AGENTS.md").read_bytes()

    r5 = _mini_repo(tmp / "repo5", with_agents=user_md)      # 追加路径
    cfg5, data5 = _analyze(r5, tmp / "c5")
    RA.emit_agents_md(cfg5, data5, "repo")
    a5 = (r5 / "AGENTS.md").read_bytes()
    RA.emit_agents_md(cfg5, data5, "repo")
    b5 = (r5 / "AGENTS.md").read_bytes()

    RA.emit_agents_md(cfg5, data5, "workspace")              # workspace 路径
    w1 = (cfg5.out_dir / "AGENTS.md").read_bytes()
    RA.emit_agents_md(cfg5, data5, "workspace")
    w2 = (cfg5.out_dir / "AGENTS.md").read_bytes()

    check("idempotent_marker_replace_append_workspace",
          first == second and a5 == b5 and w1 == w2,
          f"replace={first == second} append={a5 == b5} workspace={w1 == w2}")

    # ---- 6) settings 三态 ----
    restore = _settings_ctx({"output": {"agents_md": "off"}})
    try:
        off_mode = agents_md_setting()
        r6 = _mini_repo(tmp / "repo6")
        cfg6, data6 = _analyze(r6, tmp / "c6")
        p6 = RA.emit_agents_md(cfg6, data6, agents_md_setting())
    finally:
        restore()
    check("settings_off_generates_nothing",
          off_mode == "off" and p6 is None
          and not (r6 / "AGENTS.md").exists()
          and not (cfg6.out_dir / "AGENTS.md").exists(),
          f"mode={off_mode} path={p6}")

    restore = _settings_ctx({"output": {"agents_md": "repo"}})
    try:
        repo_mode = agents_md_setting()
        r7 = _mini_repo(tmp / "repo7")
        cfg7, data7 = _analyze(r7, tmp / "c7")
        p7 = RA.emit_agents_md(cfg7, data7, agents_md_setting())
    finally:
        restore()
    check("settings_repo_writes_repo_root",
          repo_mode == "repo" and p7 == r7 / "AGENTS.md" and p7.is_file(),
          f"mode={repo_mode} path={p7.name if p7 else None}")

    # ---- 7) 非法值回落 workspace ----
    restore = _settings_ctx({"output": {"agents_md": "banana"}})
    try:
        bad_mode = agents_md_setting()
    finally:
        restore()
    check("settings_invalid_falls_back_workspace", bad_mode == "workspace",
          f"mode={bad_mode}")

    # ---- 8) / 9) CLI e2e ----
    out8 = tmp / "c8"
    rc8, log8 = _cli(FIXTURE, out8, ["--only", "agents"])
    ok8 = (rc8 == 0 and (out8 / "AGENTS.md").is_file()
           and rp.read_bytes() == before)
    check("cli_default_workspace_product_only", ok8,
          f"rc={rc8} out_has_agents={(out8 / 'AGENTS.md').is_file()} "
          f"repo_root_unchanged={rp.read_bytes() == before}")

    out9 = tmp / "c9"
    rc9, log9 = _cli(FIXTURE, out9, ["--only", "agents", "--agents-md", "off"])
    ok9 = (rc9 == 0 and not (out9 / "AGENTS.md").exists()
           and rp.read_bytes() == before)
    check("cli_agents_md_off_overrides_settings", ok9,
          f"rc={rc9} out_has_agents={(out9 / 'AGENTS.md').exists()} "
          f"repo_root_unchanged={rp.read_bytes() == before}")

    # ---- 10) 标记匹配与品牌解耦（更名后旧块仍可更新）----
    r10 = _mini_repo(tmp / "repo10")
    (r10 / "AGENTS.md").write_text(
        "# keep-me\n\n<!-- archlens:begin auto（旧品牌块） -->\nSTALE\n"
        "<!-- archlens:end auto -->\n\n尾部\n", encoding="utf-8")
    cfg10, data10 = _analyze(r10, tmp / "c10")
    RA.emit_agents_md(cfg10, data10, "repo")
    t10 = (r10 / "AGENTS.md").read_text(encoding="utf-8")
    check("marker_matching_is_brand_agnostic",
          "STALE" not in t10 and "archlens:begin" not in t10
          and t10.startswith("# keep-me") and "尾部" in t10
          and t10.count(":begin auto") == 1,
          f"stale_removed={'STALE' not in t10} begin_count={t10.count(':begin auto')}")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "agents_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
