# -*- coding: utf-8 -*-
"""脚本普查器（一等化自 script_sweep_prototypes/inventory+score）。只读，不写任何源文件。

用途：扫描指定目录下的 `.py`，去噪后按四维（功能价值/代码质量/可复用性/集成成本）打分，
给出"值得提炼入库"的候选清单，并标注是否已在 registry.json 中。是脚本资产库的"发现"入口，
让"AI 又生成了新脚本 → 是否有复用价值"这一步可被自动初筛（人工再定夺 Tier）。

去噪：排除整仓/依赖/缓存/夹具目录（含 wt-*/uploads/venv/__pycache__/site-packages/fixture*/）。
只读：仅 rglob 读文件做静态分析，绝不写入被扫描目录。

用法：
    python -m repo_lens.scriptlib.script_sweep --root <目录> [--min-score 6] [--limit 40] [--json]
    repolens.py script sweep <目录> [--min-score 6] [--json]

退出码：0=成功（含 0 候选）；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

_NOISE_DIR = re.compile(
    r"(^|[/\\])(uploads|node_modules|\.venv|venv|__pycache__|site-packages|\.git|"
    r"fixture[^/\\]*|\.pytest_cache|wt-[\w\-]+)([/\\]|$)", re.I)
_NET_RE = re.compile(r"\b(requests|httpx|urllib3|aiohttp|socket|http\.client|paramiko|fabric)\b")
_ABS_RE = re.compile(r"['\"][A-Za-z]:\\\\|['\"]/[A-Za-z0-9_./\-]{6,}|/home/|/var/|/opt/")
_HOST_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}|https?://[\w.\-]+|ssh://[\w.\-]+)\b")
_CRED_RE = re.compile(r"(password|passwd|secret|token|api_?key|access_key)\s*=", re.I)
_DB_RE = re.compile(r"\b(psycopg2|pymysql|sqlalchemy|sqlite3|redis|pymongo|elasticsearch)\b")

_STD = {"ast", "os", "sys", "re", "json", "pathlib", "collections", "datetime", "argparse",
        "subprocess", "typing", "textwrap", "io", "math", "itertools", "hashlib", "csv",
        "statistics", "functools", "enum", "dataclasses", "time", "__future__", "abc", "os.path"}

_CAT_KW = [
    ("code_analysis", r"分析|扫描|ast|依赖|架构|指标|度量|sloc|热点|bloat|attribution|diff|loc|复杂度|churn"),
    ("test_verify", r"test|verify|retest|assert|regression|探针|冒烟|harness|spotcheck|probe|roundtrip|复测"),
    ("git_repo", r"git|commit|push|pull|sync|remote|repo|branch|changelog"),
    ("report_gen", r"report|dashboard|html|图表|chart|markdown|生成|导出|export|可视化|snapshot|manual"),
    ("deploy_ops", r"deploy|ssh|systemctl|service|运维|部署|重启|supervisor|copy|patchset"),
    ("data_db", r"sql|\bdb\b|database|query|csv|excel|xlsx|pandas|migration|forensic"),
    ("net_probe", r"接口|api|http|curl|health|ping|请求|endpoint|webhook|mcp|rpc"),
    ("patch_tool", r"patch|anchor|apply|replace|convert|迁移|改写|rewrite|stub"),
]


def _signals(p: Path) -> dict:
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {}
    d = {"lines": txt.count("\n") + 1}
    d["has_main"] = "__main__" in txt
    d["has_argparse"] = bool(re.search(r"\bargparse\b|\bfire\b|\bclick\b|sys\.argv", txt))
    d["net"] = bool(_NET_RE.search(txt))
    d["abs_path"] = len(_ABS_RE.findall(txt))
    d["n_hosts"] = len(set(_HOST_RE.findall(txt)))
    d["cred"] = bool(_CRED_RE.search(txt))
    d["db"] = bool(_DB_RE.search(txt))
    tops: Counter = Counter()
    funcs = 0
    doc = ""
    try:
        tree = ast.parse(txt)
        doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:90]
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs += 1
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops[a.name.split(".")[0]] += 1
            elif isinstance(n, ast.ImportFrom):
                if n.module:
                    tops[n.module.split(".")[0]] += 1
    except SyntaxError:
        d["syntax_error"] = True
    d["nonstd"] = sorted(set(tops) - _STD - {"typing"})
    d["funcs"] = funcs
    d["doc"] = doc
    return d


def _category(r: dict) -> str:
    hay = (r.get("doc", "") + " " + r["path"]).lower()
    for name, kw in _CAT_KW:
        if re.search(kw, hay, re.I):
            return name
    return "misc"


def _score(r: dict) -> tuple[int, int, int, int, int]:
    L = r.get("lines", 0)
    val = (2 if 40 <= L <= 400 else 1 if L > 400 else 0) + (1 if r.get("funcs", 0) >= 2 else 0)
    q = (1 if not r.get("syntax_error") else 0) + (1 if r.get("doc") else 0) + (1 if r.get("has_main") else 0)
    reuse = (2 if r.get("n_hosts", 0) == 0 else 1 if r.get("n_hosts", 0) <= 2 else 0) \
        + (2 if not r.get("nonstd") else 1 if len(r.get("nonstd", [])) <= 2 else 0) \
        + (1 if r.get("has_argparse") else 0) \
        + (1 if r.get("abs_path", 0) == 0 else 1 if r.get("abs_path", 0) <= 2 else 0)
    cost = (1 if not r.get("net") else 0) + (1 if not r.get("db") else 0) + (1 if not r.get("cred") else 0)
    return val, q, reuse, cost, val + q + reuse + cost


def sweep(root: Path, registry_ids: set[str]) -> list[dict]:
    out = []
    for p in sorted(root.rglob("*.py")):
        sp = str(p)
        if _NOISE_DIR.search(sp):
            continue
        try:
            if p.stat().st_size > 200000:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
        except (OSError, ValueError):
            continue
        sig = _signals(p)
        if not sig:
            continue
        sig["path"] = rel
        sig["category"] = _category(sig)
        v, q, reuse, cost, tot = _score(sig)
        sig.update(v=v, quality=q, reuse=reuse, cost=cost, score=tot)
        stem = Path(rel).stem
        sig["in_registry"] = stem in registry_ids
        out.append(sig)
    out.sort(key=lambda r: (-r["score"], -r["reuse"]))
    return out


def _registry_ids() -> set[str]:
    """已入库脚本 id 集合（内核 registry.core.json + 启用 pack 的 registry.json）。

    直接复用 script_cmd 的聚合加载，保证「普查标注」与「script list」永不双源分叉。
    注意：此处延迟导入以避免 scriptlib → script_cmd 的模块级循环依赖。
    """
    try:
        from ..script_cmd import _load_registry
        return {s["id"] for s in _load_registry().get("scripts", []) if s.get("id")}
    except Exception:  # noqa: BLE001 - 降级：拿不到注册表就按空集
        return set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="script_sweep",
                                 description="扫描目录并对可复用脚本打分（只读，初筛候选）。")
    ap.add_argument("root", nargs="?", default=".", help="被扫描目录")
    ap.add_argument("--min-score", type=int, default=6, help="展示分数阈值（默认 6）")
    ap.add_argument("--limit", type=int, default=40, help="最多展示条数")
    ap.add_argument("--json", action="store_true", help="结构化输出全部候选信号")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[script_sweep] 目录不存在: {root}", file=sys.stderr)
        return 2
    cands = sweep(root, _registry_ids())
    shown = [c for c in cands if c["score"] >= args.min_score][:args.limit]
    if args.json:
        print(json.dumps({"root": str(root), "scanned": len(cands),
                          "shown": len(shown), "candidates": cands[:args.limit]}, ensure_ascii=False))
        return 0
    print(f"扫描 {root}：{len(cands)} 个候选脚本，分数≥{args.min_score} 展示 {len(shown)} 个")
    for c in shown:
        flag = "★已入库" if c["in_registry"] else "      "
        print(f"  {flag} [{c['category']:13}] 分{c['score']:2}(复用{c['reuse']}) "
              f"L{c['lines']:4} 依赖{','.join(c['nonstd']) or '仅标准库':22} {c['path'][:60]}")
    print("\n注：分数仅为客观初筛，是否入库由人工定 Tier（可复用性看泛化改造成本）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
