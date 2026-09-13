# -*- coding: utf-8 -*-
"""代码膨胀归因：git 历史分析 VeroRun 近期代码量增量来源。

- 当前构成：目录 × 语言 交叉统计（与工具口径一致：排除 docs/、本地脚本、资产类）
- 近期增量：近 30/60/90/180 天按顶层目录的代码类增删（numstat 聚合）
- 新增文件：期间 diff-filter=A 新增的代码文件按目录聚合
- 活跃度：plugins/* 最近提交日期
"""
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(r"D:\projects\verorun-code")
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm",
             ".css", ".scss", ".sh", ".sql"}
EXCLUDE_TOPS = {"docs", "data", "backups", "server_backup", "tmp", "poc", "poc2",
                ".git", "venv", "node_modules", "dist", "build", "release",
                "release-staging", "dev_insight", "verorun-plugin-test-report", ".github"}


def git(*args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def top_dir(path: str) -> str:
    if "/" not in path:
        return "<root>"
    return path.split("/")[0]


def ext_of(path: str) -> str:
    return Path(path).suffix.lower()


def aggregate_numstat(since: str) -> dict:
    """聚合某时间段内每顶层目录的代码类净增行。"""
    out = git("log", "--numstat", "--pretty=format:", f"--since={since}",
              "--no-renames")
    agg = defaultdict(lambda: defaultdict(int))  # top -> lang -> net
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[2]
        if added == "-" or deleted == "-":
            continue
        ext = ext_of(path)
        if ext not in CODE_EXTS:
            continue
        top = top_dir(path)
        if top in EXCLUDE_TOPS:
            continue
        try:
            net = int(added) - int(deleted)
        except ValueError:
            continue
        agg[top][ext] += net
    return agg


def current_breakdown() -> dict:
    """当前 HEAD 代码文件目录×语言构成（用 git ls-files 只统计已跟踪文件）。"""
    files = git("ls-files")
    cross = defaultdict(lambda: defaultdict(int))
    for f in files.splitlines():
        if not f.strip():
            continue
        ext = ext_of(f)
        if ext not in CODE_EXTS:
            continue
        top = top_dir(f)
        if top in EXCLUDE_TOPS:
            continue
        # 根目录本地脚本（与工具规则一致）
        name = Path(f).name
        if top == "<root>":
            if name in {"auth_server.py", "health_guardian.py", "run_gunicorn.py",
                        "run_auth_wsgi.py", "version.py"}:
                pass
            else:
                continue
        cross[top][ext] += 1
    return cross


def added_files(since: str) -> dict:
    """期间新增文件（diff-filter=A）按目录聚合。"""
    out = git("log", "--diff-filter=A", "--name-only", "--pretty=format:",
              f"--since={since}", "--no-renames")
    agg = defaultdict(int)
    for line in out.splitlines():
        if not line.strip():
            continue
        ext = ext_of(line)
        if ext not in CODE_EXTS:
            continue
        top = top_dir(line)
        if top in EXCLUDE_TOPS:
            continue
        agg[top] += 1
    return agg


def main():
    print("===== 近 180 天净增代码行（按顶层目录，代码类扩展名） =====")
    agg180 = aggregate_numstat("180.days")
    ranked = sorted(agg180.items(), key=lambda kv: -sum(kv[1].values()))
    for top, langs in ranked[:15]:
        net = sum(langs.values())
        detail = ", ".join(f"{ext}:{n:+d}" for ext, n in sorted(langs.items(), key=lambda x: -x[1])[:4])
        print(f"  {top:18s} 净增 {net:>8,}   ({detail})")

    print("\n===== 分时段净增合计（代码类） =====")
    for since in ("30.days", "60.days", "90.days", "180.days"):
        a = aggregate_numstat(since)
        tot = sum(sum(l.values()) for l in a.values())
        print(f"  {since:10s} 净增 {tot:>9,}")

    print("\n===== 近 180 天新增代码文件数（按顶层目录） =====")
    for top, n in sorted(added_files("180.days").items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {top:18s} 新增 {n:>5} 个文件")

    print("\n===== 当前已跟踪代码文件数（按顶层目录×主语言） =====")
    cross = current_breakdown()
    for top in sorted(cross, key=lambda t: -sum(cross[t].values()))[:15]:
        langs = dict(cross[top])
        py = langs.get(".py", 0)
        html = langs.get(".html", 0)
        js = langs.get(".js", 0)
        print(f"  {top:18s} 总 {sum(langs.values()):>5}  (py:{py} html:{html} js:{js})")


if __name__ == "__main__":
    main()
