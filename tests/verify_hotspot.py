# -*- coding: utf-8 -*-
"""v1.8.0 阶段三前置验收：变更热点的增量加速（HEAD 短路 / 祖先增量合并）。

背景：全量 ``git log --numstat --since=90.days`` 是大仓库上最大的单项开销
（实测 VeroRun 27.8 万行仓库约 10s）。本套件在**临时合成 git 仓库**上验证
两级加速的正确性与降级行为，核心断言是「增量结果 == 全量结果」。

覆盖矩阵：
  1) 非 git 仓库 → 返回 None（不中断主流程）
  2) 冷跑 → source=full，churn/体量字段正确
  3) 同 HEAD 复跑 → source=head_hit，且 git 调用次数恰为 2（真正零 churn 重算）
  4) head_hit 结果与强制全量逐字段一致
  5) 新 commit（旧 sha 为祖先）→ source=incremental，churn 与强制全量完全一致
  6) 滑出窗口：缓存 cutoff 前移以模拟时间流逝 → 增量减法正确，仍与全量一致
  7) 非祖先（侧分支 / rebase）→ 自动回退 source=full
  8) days 变更 → 缓存失效 → 回退 full
  9) 缓存损坏（非法 JSON / 版本不符 / 缺字段）→ 回退 full
 10) use_cache=False → 恒为 full（对应 CLI --no-cache 语义）
 11) 空 churn → files_top/groups_top 键名与主路径一致（历史键名不一致缺陷已修）
 12) _combine 纯函数：plus/minus 归零移除语义

启动方式：python verify_hotspot.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/hotspot_verify.json。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent import hotspot_analyzer as H                     # noqa: E402
from repo_lucent.config import RepoConfig                         # noqa: E402

RESULTS: list[dict] = []

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

# ------------------------------------------------------------------ git 助手 ----

def _git(root: Path, *args, env: dict | None = None, ok: bool = True):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", "-C", str(root)] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e)
    if ok and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()[:200]}")
    return r

def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
    _git(root, "config", "user.email", "verify@example.invalid")
    _git(root, "config", "user.name", "verify-bot")
    _git(root, "config", "commit.gpgsign", "false")     # 本机可能开着全局签名

def _commit(root: Path, rel: str, content: str, msg: str,
            days_ago: float | None = None) -> str:
    """写文件并提交，返回 commit sha。days_ago 用于构造窗口外/边缘的提交。"""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    env = None
    if days_ago is not None:
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago))
        env = {"GIT_AUTHOR_DATE": ts.isoformat(), "GIT_COMMITTER_DATE": ts.isoformat()}
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg, env=env)
    return _git(root, "rev-parse", "HEAD").stdout.strip()

def _cfg(repo: Path, out: Path) -> RepoConfig:
    out.mkdir(parents=True, exist_ok=True)
    return RepoConfig(repo_root=repo, out_dir=out)

def _run(cfg, use_cache=True) -> dict | None:
    return H.analyze_hotspots(cfg, top_files=999, top_groups=999, use_cache=use_cache)

def _churn(hs: dict) -> dict:
    return {r["file"]: r["churn"] for r in hs["files_top"]}

def _added(hs: dict) -> dict:
    return {r["file"]: r["added_lines"] for r in hs["files_top"]}

def _rows(hs: dict) -> dict:
    return {r["file"]: r for r in hs["files_top"]}

def _same_result(a: dict, b: dict) -> bool:
    """比对两次分析的可观察结果（churn / added / loc / score / high_risk / group）。"""
    ra, rb = _rows(a), _rows(b)
    if set(ra) != set(rb):
        return False
    for k in ra:
        for f in ("churn", "added_lines", "loc", "score", "high_risk", "group"):
            if ra[k][f] != rb[k][f]:
                return False
    return _churn(a) == _churn(b)

def _reset_cache(cfg) -> None:
    p = Path(cfg.cache_dir) / H._CACHE_NAME
    if p.exists():
        p.unlink()

def _cache_path(cfg) -> Path:
    return Path(cfg.cache_dir) / H._CACHE_NAME

PY = "x = 1\n"

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hotspot_verify_"))

    # ---- 1) 非 git 仓库 --------------------------------------------------------
    ng = tmp / "notgit"
    (ng / "pkg").mkdir(parents=True, exist_ok=True)
    (ng / "pkg" / "a.py").write_text(PY, encoding="utf-8")
    check("non_git_repo_returns_none", _run(_cfg(ng, tmp / "o_ng")) is None, "")

    # ---- 仓库准备：两个 commit（都在 90 天窗口内）------------------------------
    repo = tmp / "repo"
    _init_repo(repo)
    _commit(repo, "pkg/a.py", "a0\n" + PY, "c1")
    _commit(repo, "pkg/b.py", "b0\n" + PY, "c2")
    cfg = _cfg(repo, tmp / "out")

    # ---- 2) 冷跑 → full --------------------------------------------------------
    hs_full = _run(cfg)
    ok2 = (hs_full is not None and hs_full.get("source") == "full"
           and _churn(hs_full) == {"pkg/a.py": 1, "pkg/b.py": 1})
    check("cold_run_source_full_and_churn", ok2,
          f"source={hs_full and hs_full.get('source')} churn={_churn(hs_full or {})}")
    check("cache_written_after_full", _cache_path(cfg).exists(),
          str(_cache_path(cfg)))

    # ---- 3) 同 HEAD 复跑 → head_hit，且 git 调用恰为 2 --------------------------
    calls = {"n": 0}
    _orig_run_git = H._run_git

    def _spy(root, args, timeout=120):
        calls["n"] += 1
        return _orig_run_git(root, args, timeout)

    H._run_git = _spy
    try:
        hs_hit = _run(cfg)
    finally:
        H._run_git = _orig_run_git
    check("head_hit_source_and_two_git_calls",
          hs_hit.get("source") == "head_hit" and calls["n"] == 2,
          f"source={hs_hit.get('source')} git_calls={calls['n']}")

    # ---- 4) head_hit 与强制全量逐字段一致 --------------------------------------
    hs_nocache1 = _run(cfg, use_cache=False)
    check("head_hit_equals_full", _same_result(hs_hit, hs_nocache1)
          and hs_nocache1.get("source") == "full",
          f"hit={_churn(hs_hit)} full={_churn(hs_nocache1)}")

    # ---- 5) 新 commit → incremental，与强制全量一致 -----------------------------
    _commit(repo, "pkg/a.py", "a0\na1\n" + PY, "c3")          # a.py 第二次改动
    _commit(repo, "pkg/c.py", PY, "c4")                       # 新增文件
    hs_inc = _run(cfg)
    hs_all = _run(cfg, use_cache=False)
    expect = {"pkg/a.py": 2, "pkg/b.py": 1, "pkg/c.py": 1}
    check("incremental_source", hs_inc.get("source") == "incremental",
          f"source={hs_inc.get('source')}")
    check("incremental_churn_exact", _churn(hs_inc) == expect,
          f"got={_churn(hs_inc)} want={expect}")
    check("incremental_equals_full", _same_result(hs_inc, hs_all),
          f"inc={_churn(hs_inc)} full={_churn(hs_all)} added_inc={_added(hs_inc)} "
          f"added_full={_added(hs_all)}")

    # ---- 6) 滑出窗口：cutoff 前移，模拟两次分析相隔 10 天 -----------------------
    repo2 = tmp / "repo2"
    _init_repo(repo2)
    _commit(repo2, "pkg/old.py", PY, "old", days_ago=95)      # 首次提交在窗口外
    _commit(repo2, "pkg/new.py", PY, "new")                   # HEAD 在窗口内
    cfg2 = _cfg(repo2, tmp / "out2")
    hs2_first = _run(cfg2)
    check("slide_out_setup_window_excludes_old",
          _churn(hs2_first) == {"pkg/new.py": 1},
          f"churn={_churn(hs2_first)}")

    # 把缓存改成「上次窗口从 100 天前开始」且 old.py 在窗口内（模拟 10 天前那次分析）
    d = json.loads(_cache_path(cfg2).read_text(encoding="utf-8"))
    d["churn"] = {"pkg/old.py": 1, "pkg/new.py": 1}
    d["added"] = {"pkg/old.py": 1, "pkg/new.py": 1}     # git numstat 口径：行数
    d["cutoff_ts"] = time.time() - 100 * 86400
    _cache_path(cfg2).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    _commit(repo2, "pkg/third.py", PY, "third")               # 推进 HEAD，触发增量
    hs2_inc = _run(cfg2)
    hs2_full = _run(cfg2, use_cache=False)
    check("slide_out_incremental_source", hs2_inc.get("source") == "incremental",
          f"source={hs2_inc.get('source')}")
    check("slide_out_subtraction_correct",
          _churn(hs2_inc) == {"pkg/new.py": 1, "pkg/third.py": 1},
          f"got={_churn(hs2_inc)}")
    check("slide_out_equals_full", _same_result(hs2_inc, hs2_full),
          f"inc={_churn(hs2_inc)} full={_churn(hs2_full)}")

    # ---- 7) 非祖先（侧分支）→ 回退 full ----------------------------------------
    repo3 = tmp / "repo3"
    _init_repo(repo3)
    base = _commit(repo3, "pkg/a.py", PY, "base")
    _commit(repo3, "pkg/b.py", PY, "main-tip")
    cfg3 = _cfg(repo3, tmp / "out3")
    _run(cfg3)                                                # 缓存 HEAD = main-tip
    _git(repo3, "checkout", "-q", "-b", "side", base)          # 从 base 分叉
    _commit(repo3, "pkg/side.py", PY, "side-tip")
    hs3 = _run(cfg3)
    check("non_ancestor_falls_back_to_full", hs3.get("source") == "full",
          f"source={hs3.get('source')}")

    # ---- 8) days 变更 → 缓存失效 ----------------------------------------------
    hs_days = H.analyze_hotspots(cfg, days=30, top_files=999, top_groups=999)
    check("days_change_invalidates_cache", hs_days.get("source") == "full",
          f"source={hs_days.get('source')}")
    check("days_change_cache_rewritten",
          json.loads(_cache_path(cfg).read_text(encoding="utf-8")).get("days") == 30, "")

    # ---- 9) 缓存损坏三态 → 回退 full -------------------------------------------
    bad_ok = []
    for name, raw in (("illegal_json", "{not json"),
                      ("version_mismatch",
                       json.dumps({"version": 999, "days": 90, "head": "x",
                                   "cutoff_ts": 0.0, "churn": {}, "added": {}})),
                      ("missing_field",
                       json.dumps({"version": H.HOTSPOT_CACHE_VERSION, "days": 90,
                                   "churn": {}, "added": {}}))):
        _cache_path(cfg).write_text(raw, encoding="utf-8")
        got = H.analyze_hotspots(cfg, days=90, top_files=999, top_groups=999)
        bad_ok.append(got is not None and got.get("source") == "full")
    check("corrupt_cache_falls_back_to_full", all(bad_ok), f"results={bad_ok}")

    # ---- 10) use_cache=False 恒为 full，且不写回缓存 ---------------------------
    _reset_cache(cfg)
    forced = _run(cfg, use_cache=False)
    check("no_cache_flag_forces_full", forced.get("source") == "full",
          f"source={forced.get('source')}")
    check("no_cache_does_not_write_cache", not _cache_path(cfg).exists(),
          f"cache_exists={_cache_path(cfg).exists()}")

    # ---- 11) 空 churn 的键名与主路径一致 ---------------------------------------
    repo4 = tmp / "repo4"
    _init_repo(repo4)
    (repo4 / "docs").mkdir(parents=True, exist_ok=True)
    _commit(repo4, "docs/readme.txt", "no code here\n", "docs-only")
    cfg4 = _cfg(repo4, tmp / "out4")
    hs4 = _run(cfg4)
    check("empty_churn_keys_consistent",
          hs4 is not None and hs4.get("files_top") == [] and hs4.get("groups_top") == []
          and "note" in hs4 and hs4.get("source") == "full",
          f"keys={sorted(hs4.keys()) if hs4 else None}")

    # ---- 12) _combine 纯函数语义 ----------------------------------------------
    c, a = H._combine({"x": 3, "y": 2}, {"x": 30, "y": 20},
                      {"x": 1}, {"x": 10}, {"x": 4}, {"x": 40})
    check("combine_zero_entries_removed",
          c == {"y": 2} and a == {"y": 20}, f"churn={c} added={a}")
    c2, a2 = H._combine({"x": 1}, {"x": 5}, {}, {}, {"x": 1}, {"x": 5})
    check("combine_full_subtraction_removes_key",
          c2 == {} and a2 == {}, f"churn={c2} added={a2}")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "hotspot_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
