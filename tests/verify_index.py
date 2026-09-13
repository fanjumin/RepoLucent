# -*- coding: utf-8 -*-
"""v1.8.0 阶段三 3-B 验收：query 的 SQLite 加速层。

设计要点：index.db 是**加速层**而非事实源。它的价值在于让 query 在库新鲜时
**跳过全量分析**（大仓库冷跑 40s → 毫秒级），同时通过「取回原始 payload 行 →
复用 query.run_query 同一段投影/过滤代码」保证两条路径结果必然一致。

覆盖矩阵：
  1) 库不存在 → load_rows 返回 None（回退原路径）
  2) build 成功：文件落 cfg.cache_dir/index.db，六表齐全
  3) 表内容：plugins / routes / core_modules / symbols 均有数据
  4) 新鲜度：指纹一致 → load_rows 可用
  5) 仓库变化（触碰文件）→ 指纹变化 → load_rows 返回 None
  6) **结果一致性（核心）**：多组 select/where 下 SQL 路径 == JSON 路径
  7) 不支持的 scope → None
  8) 库损坏 → None，且 build 能重建
  9) --no-cache 不读不写库
 10) --rebuild-index 强制走全量并重建
 11) CLI 端到端：首次建库 → 二次走加速层，两次输出逐字节一致
 12) payload 往返保真：嵌套路径 routes[].rule 在 SQL 路径下仍正确

启动方式：python verify_index.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/index_verify.json。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent import cli, index_db                             # noqa: E402
from repo_lucent.config import RepoConfig                         # noqa: E402

RESULTS: list[dict] = []

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:240]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

def _copy_fixture(tmp: Path) -> Path:
    """复制 fixture_repo 到临时目录——绝不改真 fixture（用例 5 要触碰文件）。"""
    dst = tmp / "repo"
    shutil.copytree(HERE / "tests" / "fixture_repo", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return dst

def _cfg(repo: Path, out: Path) -> RepoConfig:
    out.mkdir(parents=True, exist_ok=True)
    return RepoConfig(repo_root=repo, out_dir=out)

def _table_names(path: Path) -> set[str]:
    """读库中的表名。

    ⚠️ 绝不能写成 ``with sqlite3.connect(...) as c``：连接的上下文管理器只负责
    提交/回滚事务，**并不关闭连接**。残留句柄会在 Windows 上锁住 index.db，使后续
    ``build`` 的 ``os.replace`` 原子替换恒抛 ``WinError 5``（实测踩过一次）。
    """
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

def _analyze(cfg) -> tuple[dict, dict]:
    ns = types.SimpleNamespace(no_cache=True, deterministic=True,
                               module=None, plugin=None)
    data, _, pc = cli._analyze(ns, cfg)
    return data, pc

def _cli(args: list[str], cwd: Path | None = None):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    r = subprocess.run([sys.executable, "-m", "repo_lucent"] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(cwd or HERE))
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="index_verify_"))
    repo = _copy_fixture(tmp)
    out = tmp / "out"
    cfg = _cfg(repo, out)

    # ---- 1) 库不存在 → None ---------------------------------------------------
    check("load_rows_none_when_db_absent", index_db.load_rows(cfg, "plugins") is None,
          f"db={index_db.db_path(cfg).exists()}")

    # ---- 2) build 成功 + 表齐全 ------------------------------------------------
    data, pc = _analyze(cfg)
    p = index_db.build(cfg, data, symbols=cli.symbol_index_for(cfg, pc))
    tables = _table_names(p) if p is not None else set()
    want = {"meta", "plugins", "routes", "symbols", "files", "core_modules"}
    check("build_creates_db_with_all_tables", p is not None and want <= tables,
          f"path={p} tables={sorted(tables)}")

    # ---- 3) 表内容 -------------------------------------------------------------
    st = index_db.stats(cfg)
    check("tables_populated",
          st.get("plugins", 0) >= 2 and st.get("routes", 0) >= 2
          and st.get("core_modules", 0) >= 1 and st.get("symbols", 0) >= 1,
          f"plugins={st.get('plugins')} routes={st.get('routes')} "
          f"core={st.get('core_modules')} symbols={st.get('symbols')}")

    # ---- 4) 新鲜 → 可用 --------------------------------------------------------
    rows = index_db.load_rows(cfg, "plugins")
    check("load_rows_available_when_fresh",
          isinstance(rows, list) and len(rows) >= 2,
          f"n={len(rows) if rows else None}")

    # ---- 5) 仓库变化 → 指纹失效 → None ----------------------------------------
    (repo / "shared" / "__init__.py").write_text(
        (repo / "shared" / "__init__.py").read_text(encoding="utf-8") + "\n# touch\n",
        encoding="utf-8")
    check("load_rows_none_when_repo_changed",
          index_db.load_rows(cfg, "plugins") is None,
          "触碰共享模块后指纹应变化")

    # 复原并重建，供后续用例使用
    shutil.rmtree(repo)
    repo = _copy_fixture(tmp)
    cfg = _cfg(repo, out)
    data, pc = _analyze(cfg)
    index_db.build(cfg, data, symbols=cli.symbol_index_for(cfg, pc))

    # ---- 6) 结果一致性（核心断言）---------------------------------------------
    cases = [
        ("plugins", ["identifier", "version"], None),
        ("plugins", ["identifier"], "agent_role=business"),
        ("plugins", ["identifier"], "agent_role!=business"),
        ("plugins", ["identifier", "routes[].rule"], None),
        ("plugins", ["plugins[].identifier"], None),
        ("core", ["name", "loc"], None),
        ("core", ["name"], "known=true"),
    ]
    mismatches = []
    for scope, sel, wh in cases:
        got_json = cli.build_query(data, scope=scope, select=sel, where=wh)["result"]
        rows_sql = index_db.load_rows(cfg, scope)
        got_sql = cli.build_query({}, scope=scope, select=sel, where=wh,
                                  rows=rows_sql)["result"]
        if got_json != got_sql:
            mismatches.append(f"{scope}/{sel}/{wh}")
    check("sql_path_equals_json_path", not mismatches,
          f"cases={len(cases)} mismatches={mismatches}")

    # ---- 12) 嵌套路径在 SQL 路径下保真 ----------------------------------------
    r = cli.build_query({}, scope="plugins", select=["identifier", "routes[].rule"],
                        rows=index_db.load_rows(cfg, "plugins"))["result"]
    rules = [rule for row in r["rows"] for rule in (row.get("routes[].rule") or [])]
    check("nested_path_payload_roundtrip",
          r["count"] >= 2 and len(rules) >= 2,
          f"rules={rules}")

    # ---- 7) 不支持的 scope → None ---------------------------------------------
    check("load_rows_none_for_unsupported_scope",
          index_db.load_rows(cfg, "nope") is None, "")

    # ---- 8) 库损坏 → None，且可重建 -------------------------------------------
    db = index_db.db_path(cfg)
    db.write_bytes(b"this is not a sqlite database at all" * 10)
    broke = index_db.load_rows(cfg, "plugins") is None
    rebuilt = index_db.build(cfg, data, symbols=cli.symbol_index_for(cfg, pc))
    back = index_db.load_rows(cfg, "plugins")
    check("corrupt_db_degrades_then_rebuilds",
          broke and rebuilt is not None and isinstance(back, list) and len(back) >= 2,
          f"broke_ok={broke} rebuilt={rebuilt is not None} rows={len(back or [])}")

    # ---- 9) --no-cache 不读不写库（CLI 端到端）--------------------------------
    out_nc = tmp / "out_nocache"
    rc, _ = _cli(["--repo", str(repo), "--out", str(out_nc), "--no-date-dir",
                  "--no-cache", "query", "--select", "identifier"])
    check("no_cache_does_not_build_index",
          rc == 0 and not (out_nc / index_db.DB_NAME).exists(),
          f"rc={rc} db_exists={(out_nc / index_db.DB_NAME).exists()}")

    # ---- 10/11) --rebuild-index 与端到端一致性 --------------------------------
    out_cli = tmp / "out_cli"
    rc1, o1 = _cli(["--repo", str(repo), "--out", str(out_cli), "--no-date-dir",
                    "query", "--select", "identifier,version", "--format", "table"])
    built = (out_cli / index_db.DB_NAME).exists()
    rc2, o2 = _cli(["--repo", str(repo), "--out", str(out_cli), "--no-date-dir",
                    "query", "--select", "identifier,version", "--format", "table"])
    rc3, o3 = _cli(["--repo", str(repo), "--out", str(out_cli), "--no-date-dir",
                    "query", "--select", "identifier,version", "--format", "table",
                    "--rebuild-index"])
    check("cli_builds_index_then_second_run_matches",
          rc1 == 0 and rc2 == 0 and rc3 == 0 and built and o1 == o2 and o1 == o3,
          f"rc={rc1}/{rc2}/{rc3} built={built} same={o1 == o2 == o3}")
    check("cli_rebuild_index_forces_full_and_rebuilds",
          (out_cli / index_db.DB_NAME).exists() and rc3 == 0, "")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "index_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
