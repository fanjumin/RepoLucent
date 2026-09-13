# -*- coding: utf-8 -*-
"""v1.6.0 阶段一 1-C 验收：AST 符号倒排索引 + repo.search（CLI/MCP 共用执行面）。

覆盖矩阵：
  1) py_ast 新增 lineno：类/函数/路由三类事实均带正整数行号
  2) build_symbol_index 覆盖三种 kind（class / function / route）
  3) 路径归一 POSIX：产物中不出现反斜杠（跨平台确定性）
  4) owner 派生：plugins/<id>/… → <id>；顶层目录 / 文件 → 顶层目录
  5) 检索精确命中优先（match=exact，单条）
  6) 检索子串回退（大小写不敏感，match=substring）
  7) 检索截断：limit 生效且置 truncated=true（count 仍为真实命中数）
  8) 未命中：count=0、hits=[]、match 保持 exact
  9) 确定性：同一 parse_cache 两次构建逐字节一致
 10) 产物往返：_write_reports 落 repo_lens_symbols.json，且主 JSON 的 symbols
     摘要段 count/index_file 与索引文件一致
 11) 索引缺失时降级：build_search 返回空结果 + note，不抛异常
 12) CLI e2e：search 命中 rc=0 / 未命中 rc=1；JSON 载荷字段齐全

启动方式：python verify_symbols.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/symbols_verify.json。
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
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repo_lens import cli                                   # noqa: E402
from repo_lens.config import RepoConfig                     # noqa: E402
from repo_lens.symbol_index import (build_symbol_index, search_symbols,  # noqa: E402
                                    index_from_file)

RESULTS: list[dict] = []


def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


def _mini_repo(root: Path) -> Path:
    """造一个含「核心模块 + 插件 + Flask 路由 + 多定义同名符号」的最小仓库。

    注意：路由文件刻意**不带 UTF-8 BOM**——读取层目前会把 BOM 当非法字符
    （既有缺陷，已记录待阶段二 2-C 的 BOM 边界用例覆盖），此处不应依赖它。
    """
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "__init__.py").write_text(
        '"""共享设施。"""\n\n\n'
        'def get_pooled_connection():\n    return None\n\n\n'
        'def helper():\n    return 1\n', encoding="utf-8")

    # 仓库签名要求同时存在 plugins/ 与 plugin_manager/（CLI 自动定位条件）
    pm = root / "plugin_manager"
    pm.mkdir(parents=True, exist_ok=True)
    (pm / "__init__.py").write_text('"""插件框架。"""\n', encoding="utf-8")
    (pm / "base.py").write_text(
        '"""插件基类。"""\n\n\nclass BasePlugin:\n'
        '    def on_load(self):\n        raise NotImplementedError\n',
        encoding="utf-8")

    pdir = root / "plugins" / "demo"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "__init__.py").write_text(
        '"""演示插件。"""\n\n\n'
        'class DemoPlugin:\n    def ping(self):\n        return "pong"\n',
        encoding="utf-8")
    (pdir / "routes.py").write_text(
        'from flask import Blueprint\n\n'
        'demo_bp = Blueprint("demo", __name__, url_prefix="/admin/demo")\n\n\n'
        '@demo_bp.route("/ping", methods=["GET"])\n'
        'def ping():\n    return "pong"\n\n\n'
        '@demo_bp.route("/list")\n'
        'def list_items():\n    return []\n',
        encoding="utf-8")
    (pdir / "plugin.json").write_text(json.dumps({
        "identifier": "demo", "name": "Demo", "version": "1.0.0",
        "description": "演示", "author": "t", "min_app_version": "1.0.0",
        "agent_role": "assistant", "capabilities": [],
    }, ensure_ascii=False), encoding="utf-8")

    # 同名函数在两个模块中重复定义：用于验证"同符号多定义"的排序确定性
    pdir2 = root / "plugins" / "other"
    pdir2.mkdir(parents=True, exist_ok=True)
    (pdir2 / "__init__.py").write_text(
        '"""另一个插件。"""\n\n\ndef helper():\n    return 2\n', encoding="utf-8")
    (pdir2 / "plugin.json").write_text(json.dumps({
        "identifier": "other", "name": "Other", "version": "1.0.0",
        "description": "另一", "author": "t", "min_app_version": "1.0.0",
        "agent_role": "assistant", "capabilities": [],
    }, ensure_ascii=False), encoding="utf-8")
    return root


def _analyze(repo: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    cfg = RepoConfig(repo_root=repo, out_dir=out, stable_out_dir=out)
    ns = types.SimpleNamespace(no_cache=True, deterministic=True,
                               module=None, plugin=None)
    data, _dur, parse_cache = cli._analyze(ns, cfg)
    return cfg, data, parse_cache


def _cli(repo: Path, out: Path, extra: list[str]):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    env.pop("REPO_LENS_SETTINGS", None)
    cmd = [sys.executable, "-m", "repo_lens", "--repo", str(repo),
           "--out", str(out), "--no-date-dir", "--quiet"] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       cwd=str(HERE), timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="symbols_verify_"))
    repo = _mini_repo(tmp / "repo")
    cfg, data, pc = _analyze(repo, tmp / "out1")
    index = build_symbol_index(pc, cfg)
    syms = index["symbols"]

    # ---- 1) lineno 覆盖三类事实 ----
    ent = next(iter(pc.values()))
    has_lines = all(isinstance(f.get("lineno"), int) and f["lineno"] > 0
                    for e in pc.values() for f in
                    (e.get("classes") or []) + (e.get("functions") or [])
                    + (e.get("routes") or []))
    kinds = {h["kind"] for v in syms.values() for h in v}
    check("py_ast_exposes_lineno_for_all_kinds",
          has_lines and {"class", "function", "route"} <= kinds,
          f"files={len(pc)} kinds={sorted(kinds)}")

    # ---- 2) 三种 kind 都被索引 ----
    check("index_covers_class_function_route",
          "DemoPlugin" in syms and "get_pooled_connection" in syms
          and "/ping" in syms,
          f"count={index['count']} sample={sorted(syms)[:6]}")

    # ---- 3) 路径归一 POSIX ----
    files = [h["file"] for v in syms.values() for h in v]
    check("paths_normalized_to_posix",
          bool(files) and not any("\\" in f for f in files),
          f"n={len(files)} sample={files[:3]}")

    # ---- 4) owner 派生 ----
    demo = next(h for h in syms["DemoPlugin"])
    pooled = next(h for h in syms["get_pooled_connection"])
    check("owner_derived_from_path",
          demo["owner"] == "demo" and pooled["owner"] == "shared"
          and next(h for h in syms["/ping"])["owner"] == "demo",
          f"plugin_owner={demo['owner']} core_owner={pooled['owner']}")

    # ---- 5) 精确命中优先 ----
    exact = search_symbols(index, "DemoPlugin")
    check("search_exact_match_preferred",
          exact["match"] == "exact" and exact["count"] == 1
          and exact["hits"][0]["file"] == "plugins/demo/__init__.py",
          f"match={exact['match']} count={exact['count']}")

    # ---- 6) 子串回退（大小写不敏感 + 同符号多定义）----
    sub = search_symbols(index, "HELPER")
    check("search_substring_fallback_case_insensitive",
          sub["match"] == "substring" and sub["count"] >= 2
          and {h["file"] for h in sub["hits"]} >= {
              "shared/__init__.py", "plugins/other/__init__.py"},
          f"match={sub['match']} count={sub['count']} "
          f"files={sorted({h['file'] for h in sub['hits']})}")

    sub2 = search_symbols(index, "e")          # 多命中，验证排序稳定
    ordered = [(h["file"], h["line"]) for h in sub2["hits"]]
    check("search_substring_result_sorted_by_file_line",
          len(ordered) >= 3 and ordered == sorted(ordered),
          f"n={len(ordered)} head={ordered[:3]}")

    # ---- 7) 截断 ----
    trunc = search_symbols(index, "e", limit=1)      # 极小上限必然触发截断
    check("search_truncates_with_flag",
          trunc["truncated"] is True and len(trunc["hits"]) == 1
          and trunc["count"] > 1,
          f"count={trunc['count']} hits={len(trunc['hits'])}")

    # ---- 8) 未命中 ----
    miss = search_symbols(index, "this_symbol_does_not_exist_at_all")
    check("search_miss_returns_empty",
          miss["count"] == 0 and miss["hits"] == [] and miss["truncated"] is False,
          f"count={miss['count']}")

    # ---- 9) 确定性 ----
    a = json.dumps(build_symbol_index(pc, cfg), ensure_ascii=False, sort_keys=False)
    b = json.dumps(build_symbol_index(pc, cfg), ensure_ascii=False, sort_keys=False)
    check("index_build_is_deterministic", a == b, f"len={len(a)}")

    # ---- 10) 产物往返（独立索引文件 + 主 JSON 摘要段一致）----
    cli._write_reports(cfg, data, "json,symbols", parse_cache=pc)
    idx_path = cfg.out_dir / "repo_lens_symbols.json"
    d = json.loads((cfg.out_dir / "repo_lens.json").read_text(encoding="utf-8"))
    disk = index_from_file(idx_path) if idx_path.is_file() else {}
    summ = d.get("symbols") or {}
    check("symbols_artifact_and_summary_in_sync",
          idx_path.is_file() and summ.get("index_file") == "repo_lens_symbols.json"
          and summ.get("count") == index["count"] == disk.get("count"),
          f"file={idx_path.name} summary_count={summ.get('count')} "
          f"disk_count={disk.get('count')}")

    # ---- 11) 索引缺失时降级不抛异常 ----
    empty_cfg = RepoConfig(repo_root=repo, out_dir=tmp / "empty_out",
                           stable_out_dir=tmp / "empty_out")
    empty_cfg.out_dir.mkdir(parents=True, exist_ok=True)
    degraded = cli.build_search(empty_cfg, data, "DemoPlugin", parse_cache=None)
    check("search_degrades_when_index_missing",
          degraded["count"] == 0 and "note" in degraded,
          f"note={str(degraded.get('note'))[:60]}")

    # ---- 12) CLI e2e ----
    out12 = tmp / "c12"
    rc_hit, log_hit = _cli(repo, out12, ["search", "DemoPlugin"])
    payload = None
    try:
        start = log_hit.index("{")
        payload = json.loads(log_hit[start:])
    except (ValueError, json.JSONDecodeError):
        payload = None
    rc_miss, _ = _cli(repo, out12, ["search", "definitely_not_present_xyz"])
    check("cli_search_rc_and_payload",
          rc_hit == 0 and rc_miss == 1 and isinstance(payload, dict)
          and payload.get("hits") and payload["hits"][0]["kind"] == "class",
          f"rc_hit={rc_hit} rc_miss={rc_miss} "
          f"first={payload['hits'][0] if payload and payload.get('hits') else None}")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "symbols_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
