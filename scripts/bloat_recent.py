# -*- coding: utf-8 -*-
"""近 30 天增量细粒度归因：HEAD vs 30 天前 commit 逐文件 diff。

按 plugins/<identifier> 二级目录聚合 + Top 文件清单，定位膨胀源头。
"""
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(r"D:\projects\verorun-code")
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm",
             ".css", ".scss", ".sh", ".sql"}
EXCLUDE_TOPS = {"docs", "data", "backups", "server_backup", "tmp", "poc", "poc2",
                ".git", "venv", "node_modules", "dist", "build", "release",
                "release-staging", "dev_insight", "verorun-plugin-test-report", ".github",
                "platform", "site", "site_builder", "admin"}


def git(*args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def old_head(days: str) -> str:
    return git("rev-list", "-1", f"--before={days}", "HEAD").strip()


def diff_numstat(old: str) -> list[tuple[int, int, str]]:
    rows = []
    out = git("diff", "--numstat", old, "HEAD", "--no-renames")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[2]
        if a == "-" or d == "-":
            continue
        if Path(path).suffix.lower() not in CODE_EXTS:
            continue
        top = path.split("/")[0] if "/" in path else "<root>"
        if top in EXCLUDE_TOPS or top.startswith("."):
            continue
        rows.append((int(a), int(d), path))
    return rows


def main():
    old = old_head("30.days")
    print("30 天前 commit:", old)
    rows = diff_numstat(old)
    net = sum(a - d for a, d, _ in rows)
    added = sum(a for a, _, _ in rows)
    deleted = sum(d for _, d, _ in rows)
    print(f"近 30 天代码类：新增 {added:,} 行 / 删除 {deleted:,} 行 / 净增 {net:,} 行\n")

    # 按 plugins/<id> 聚合
    by_plugin = defaultdict(lambda: [0, 0])
    for a, d, p in rows:
        parts = p.split("/")
        if len(parts) >= 3 and parts[0] == "plugins":
            by_plugin[parts[1]][0] += a
            by_plugin[parts[1]][1] += d
    print("===== 近 30 天 plugins/ 内增量 Top 12（按净增） =====")
    for pid, (a, d) in sorted(by_plugin.items(), key=lambda kv: -(kv[1][0] - kv[1][1])):
        print(f"  {pid:24s} 新增 {a:>7,}  净增 {a-d:>+8,}")

    # 按二级顶层目录聚合（非 plugins）
    by_top = defaultdict(lambda: [0, 0])
    for a, d, p in rows:
        parts = p.split("/")
        key = parts[1] if len(parts) >= 2 and parts[0] == "plugins" else (parts[0] if parts[0] != "<root>" else "<root>")
        if parts[0] == "plugins" and len(parts) >= 3:
            continue
        by_top[key][0] += a
        by_top[key][1] += d
    print("\n===== 近 30 天其余目录增量 =====")
    for k, (a, d) in sorted(by_top.items(), key=lambda kv: -(kv[1][0] - kv[1][1]))[:12]:
        if a - d != 0:
            print(f"  {k:20s} 新增 {a:>7,}  净增 {a-d:>+8,}")

    print("\n===== 近 30 天净增 Top 25 文件 =====")
    for a, d, p in sorted(rows, key=lambda r: -(r[0] - r[1]))[:25]:
        if a - d > 0:
            print(f"  {a-d:>+8,}  {p}")


if __name__ == "__main__":
    main()
