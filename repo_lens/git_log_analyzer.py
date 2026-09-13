# -*- coding: utf-8 -*-
"""Git 提交历史扫描与分析。

通过 git log --numstat 获取结构化提交数据，支持按作者、时间范围过滤。
输出包含：提交总数、作者分布、文件热度统计等。
"""
from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CommitInfo:
    """单次提交的详细信息。"""
    hash: str
    short_hash: str
    author_name: str
    author_email: str
    date: str
    message: str
    files_changed: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0


def _check_git_available(repo_root: Path) -> bool:
    """检查目录是否为有效的 git 仓库。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def scan_commits(repo_root: Path, since_days: int = 90,
                 author: str | None = None,
                 max_commits: int | None = None) -> dict[str, Any]:
    """扫描指定时间范围内的提交记录。

    Args:
        repo_root: 仓库根目录
        since_days: 扫描最近多少天的提交
        author: 可选，只扫描指定作者的提交
        max_commits: 可选，限制返回的 commit 数量

    Returns:
        结构化的提交历史数据
    """
    if not _check_git_available(repo_root):
        return {"error": "当前目录不是有效的 git 仓库"}

    # 构建 git log 命令
    cmd = [
        "git", "log",
        f"--since={since_days}.days",
        "--pretty=format:%H|%h|%an|%ae|%ad|%s",
        "--numstat",
    ]

    if author:
        cmd.extend(["--author", author])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"error": "git log 执行超时"}
    except FileNotFoundError:
        return {"error": "未找到 git 命令"}

    if result.returncode != 0:
        return {"error": f"git log 失败: {result.stderr}"}

    # 解析输出
    commits: list[CommitInfo] = []
    current_commit: CommitInfo | None = None

    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        # 判断是否为 commit 头行（包含 | 分隔符且不是 numstat 行）
        # numstat 行格式：<additions>\t<deletions>\t<filepath>
        if "|" in line and "\t" not in line:
            parts = line.split("|", 5)
            if len(parts) == 6:
                current_commit = CommitInfo(
                    hash=parts[0],
                    short_hash=parts[1],
                    author_name=parts[2],
                    author_email=parts[3],
                    date=parts[4],
                    message=parts[5],
                )
                commits.append(current_commit)
        elif current_commit and line.strip() and "\t" in line:
            # numstat 行：<additions>\t<deletions>\t<filepath>
            parts = line.split("\t")
            if len(parts) == 3:
                try:
                    adds = int(parts[0]) if parts[0] != "-" else 0
                    dels = int(parts[1]) if parts[1] != "-" else 0
                except ValueError:
                    adds = dels = 0

                current_commit.files_changed.append(parts[2])
                current_commit.additions += adds
                current_commit.deletions += dels

    # 应用 max_commits 限制
    if max_commits and len(commits) > max_commits:
        commits = commits[:max_commits]

    # 聚合统计
    authors = Counter(c.author_name for c in commits)
    file_churn = Counter()
    total_additions = 0
    total_deletions = 0

    for c in commits:
        for f in c.files_changed:
            file_churn[f] += 1
        total_additions += c.additions
        total_deletions += c.deletions

    # 构建返回数据
    result_data: dict[str, Any] = {
        "total_commits": len(commits),
        "date_range": {
            "since_days": since_days,
            "latest": commits[0].date if commits else None,
            "earliest": commits[-1].date if commits else None,
        },
        "authors": dict(authors.most_common(10)),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "top_churned_files": [
            {"file": f, "commits": n}
            for f, n in file_churn.most_common(20)
        ],
    }

    # 完整 commit 列表（用于 JSON 输出）
    result_data["commits"] = [
        {
            "hash": c.hash,
            "short_hash": c.short_hash,
            "author": c.author_name,
            "date": c.date,
            "message": c.message,
            "files_changed_count": len(c.files_changed),
            "additions": c.additions,
            "deletions": c.deletions,
        }
        for c in commits
    ]

    return result_data


def format_commit_table(data: dict[str, Any]) -> str:
    """将提交历史数据格式化为可读表格。

    Args:
        data: scan_commits 返回的数据

    Returns:
        格式化的表格字符串
    """
    if "error" in data:
        return f"错误: {data['error']}"

    lines = []
    lines.append(f"提交历史 (最近 {data['date_range']['since_days']} 天)")
    lines.append("=" * 60)
    lines.append(f"总计: {data['total_commits']} 次提交 | "
                 f"{len(data['authors'])} 位作者 | "
                 f"+{data['total_additions']}/-{data['total_deletions']} 行")
    lines.append("")

    # 作者分布
    if data["authors"]:
        lines.append("作者分布:")
        for author, count in data["authors"].items():
            pct = (count / data["total_commits"] * 100) if data["total_commits"] > 0 else 0
            lines.append(f"  {author:<20} {count:>4} 次提交 ({pct:.1f}%)")
        lines.append("")

    # 热门文件
    if data["top_churned_files"]:
        lines.append("最频繁修改的文件:")
        for item in data["top_churned_files"][:10]:
            lines.append(f"  {item['file']:<50} {item['commits']:>3} 次提交")

    return "\n".join(lines)
