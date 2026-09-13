# -*- coding: utf-8 -*-
"""Git 增量归因（合并通用化自 bloat_analysis.py + bloat_recent.py）。

回答"这段时间代码涨在哪"：窗口内净增行、按目录/插件/文件聚合、新增文件、当前构成。
需外部 git 可执行 + 目标为 git 仓库；无网络、纯本地。

用法：
    python -m repo_lens.scriptlib.git_churn --repo <仓库> [--since 30.days]
        [--group-by {dir,plugin,file}] [--top 15] [--json]
    repolens.py script run git_churn --repo D:\\projects\\verorun-code --since 90.days

退出码：0=成功；2=参数/环境错误（非 git 仓库 / 无 git）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    from ..config import DEFAULT_EXCLUDE_DIRS
    _EXCL = set(DEFAULT_EXCLUDE_DIRS) | {"docs", "server_backup", "poc", "poc2"}
except Exception:  # noqa: BLE001 - 降级提示：取不到工具配置时用兜底排除集
    _EXCL = {"docs", "data", "backups", "server_backup", "tmp", "poc", "poc2",
             ".git", "venv", "node_modules", "dist", "build", "release",
             "dev_insight", ".github", "__pycache__"}

CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm",
             ".css", ".scss", ".sh", ".sql"}


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def _excluded_top(top: str) -> bool:
    return top in _EXCL or top.startswith(".")


def _group_key(path: str, group_by: str) -> str:
    parts = path.split("/")
    if group_by == "file":
        return path
    if group_by == "plugin":
        if parts[0] == "plugins" and len(parts) >= 2:
            return f"plugins/{parts[1]}"
        return parts[0] if len(parts) > 1 else "<root>"
    return parts[0] if len(parts) > 1 else "<root>"  # dir


def _numstat_diff(repo: Path, ref: str) -> list[tuple[int, int, str]]:
    """HEAD 与 ref 之间逐文件代码类增删行。"""
    rows = []
    out = _git(repo, "diff", "--numstat", ref, "HEAD", "--no-renames")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[2]
        if a == "-" or d == "-" or Path(path).suffix.lower() not in CODE_EXTS:
            continue
        if _excluded_top(path.split("/")[0] if "/" in path else "<root>"):
            continue
        rows.append((int(a), int(d), path))
    return rows


def _window_ref(repo: Path, since: str) -> str | None:
    days = since.replace(".days", "")
    base = _git(repo, "rev-list", "-1", f"--before={days} days ago", "HEAD").strip()
    return base or None


def analyze(repo: Path, since: str, group_by: str, top: int) -> dict:
    ref = _window_ref(repo, since)
    result = {"repo": str(repo), "since": since, "ref": ref,
              "added": 0, "deleted": 0, "net": 0, "by_group": [], "top_files": []}
    if not ref:
        result["note"] = "仓库历史不足该时间窗，取根提交作近似基准"
        roots = _git(repo, "rev-list", "--max-parents=0", "HEAD").split()
        ref = roots[0] if roots else ""
        result["ref"] = ref
    if not ref:
        print("[git_churn] 环境错误：仓库无可用提交", file=sys.stderr)
        return {"repo": str(repo), "error": "no commits"}
    rows = _numstat_diff(repo, ref)
    added = sum(a for a, _, _ in rows)
    deleted = sum(d for _, d, _ in rows)
    result["added"], result["deleted"], result["net"] = added, deleted, added - deleted
    agg = defaultdict(lambda: [0, 0])
    for a, d, p in rows:
        g = agg[_group_key(p, group_by)]
        g[0] += a
        g[1] += d
    result["by_group"] = [
        {"group": k, "added": v[0], "deleted": v[1], "net": v[0] - v[1]}
        for k, v in sorted(agg.items(), key=lambda kv: -(kv[1][0] - kv[1][1]))[:top]
    ]
    result["top_files"] = [
        {"file": p, "added": a, "deleted": d, "net": a - d}
        for a, d, p in sorted(rows, key=lambda r: -(r[0] - r[1]))[:top] if a - d > 0
    ]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="git_churn", description="Git 增量归因（窗口净增/分组/Top 文件）。")
    ap.add_argument("--repo", default=".", help="目标 git 仓库根目录")
    ap.add_argument("--since", default="30.days", help="时间窗，如 30.days/90.days/180.days")
    ap.add_argument("--group-by", choices=("dir", "plugin", "file"), default="dir")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    if not shutil.which("git"):
        print("[git_churn] 环境错误：未找到 git 可执行文件", file=sys.stderr)
        return 2
    if not (repo / ".git").is_dir():
        print(f"[git_churn] 环境错误：不是 git 仓库: {repo}", file=sys.stderr)
        return 2
    res = analyze(repo, args.since, args.group_by, args.top)
    if res.get("error"):
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    print(f"仓库 {res['repo']}｜窗口 {res['since']}｜基准 {res['ref'][:12] or '—'}")
    if res.get("note"):
        print(f"提示：{res['note']}")
    print(f"代码类：新增 {res['added']:,} / 删除 {res['deleted']:,} / 净增 {res['net']:,} 行")
    print(f"\n=== 按 {args.group_by} 净增 Top {args.top} ===")
    for g in res["by_group"]:
        print(f"  {g['group']:28s} 新增 {g['added']:>7,}  净增 {g['net']:>+8,}")
    print(f"\n=== 净增 Top {args.top} 文件 ===")
    for f in res["top_files"]:
        print(f"  {f['net']:>+8,}  {f['file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
