# -*- coding: utf-8 -*-
"""本地仓库与远程仓库的差异分析。

通过 git fetch + git rev-list 计算 ahead/behind 数量，
列出未推送和未拉取的 commit，检测远程新标签。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


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


def analyze_remote_diff(repo_root: Path, remote: str = "origin",
                        branch: str | None = None) -> dict[str, Any]:
    """分析本地分支与远程分支的差异。

    Args:
        repo_root: 仓库根目录
        remote: remote 名称（默认 origin）
        branch: 分支名（默认使用当前分支）

    Returns:
        结构化的差异数据
    """
    if not _check_git_available(repo_root):
        return {"error": "当前目录不是有效的 git 仓库"}

    # 如果未指定分支，获取当前分支
    if not branch:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch_result.returncode != 0:
            return {"error": "无法获取当前分支"}
        branch = branch_result.stdout.strip()

    # 1. Fetch 远程引用
    fetch_result = subprocess.run(
        ["git", "fetch", remote],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
    )

    if fetch_result.returncode != 0:
        return {"error": f"fetch 失败: {fetch_result.stderr.strip()}"}

    # 2. 计算 ahead/behind
    local_ref = f"refs/heads/{branch}"
    remote_ref = f"refs/remotes/{remote}/{branch}"

    count_result = subprocess.run(
        ["git", "rev-list", "--left-right", "--count",
         f"{local_ref}...{remote_ref}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=10,
    )

    if count_result.returncode != 0:
        # 可能分支不存在或未跟踪远程
        return {
            "error": f"分支 '{branch}' 在 remote '{remote}' 上不存在或未跟踪",
            "remote": remote,
            "branch": branch,
        }

    # 输出格式：<ahead_count>\t<behind_count>
    parts = count_result.stdout.strip().split("\t")
    if len(parts) != 2:
        return {"error": "无法解析 ahead/behind 计数"}

    ahead = int(parts[0])
    behind = int(parts[1])

    # 3. 获取未推送的 commit 列表
    unpushed = []
    if ahead > 0:
        log_result = subprocess.run(
            ["git", "log", f"{remote_ref}..{local_ref}", "--oneline"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if log_result.returncode == 0:
            unpushed = [
                line.strip() for line in log_result.stdout.splitlines()
                if line.strip()
            ]

    # 4. 获取远程新增的 commit 列表
    unpulled = []
    if behind > 0:
        log_result = subprocess.run(
            ["git", "log", f"{local_ref}..{remote_ref}", "--oneline"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if log_result.returncode == 0:
            unpulled = [
                line.strip() for line in log_result.stdout.splitlines()
                if line.strip()
            ]

    # 5. 检查远程是否有新 tag
    new_remote_tags = []
    tags_result = subprocess.run(
        ["git", "tag", "-l"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=5,
    )
    local_tags = set(tags_result.stdout.splitlines()) if tags_result.returncode == 0 else set()

    remote_tags_result = subprocess.run(
        ["git", "ls-remote", "--tags", remote],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if remote_tags_result.returncode == 0:
        remote_tag_refs = set()
        for line in remote_tags_result.stdout.splitlines():
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    tag_ref = parts[1]
                    # refs/tags/v1.0.0 -> v1.0.0
                    if tag_ref.startswith("refs/tags/"):
                        tag_name = tag_ref[len("refs/tags/"):]
                        # 排除 ^{} 引用
                        if not tag_name.endswith("^{}"):
                            remote_tag_refs.add(tag_name)

        new_remote_tags = sorted(remote_tag_refs - local_tags)[:10]

    # 6. 确定状态
    if ahead > 0 and behind > 0:
        status = "diverged"
    elif ahead > 0:
        status = "ahead"
    elif behind > 0:
        status = "behind"
    else:
        status = "synced"

    return {
        "remote": remote,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "status": status,
        "unpushed_commits": unpushed[:20],  # 最多显示 20 条
        "unpulled_commits": unpulled[:20],
        "new_remote_tags": new_remote_tags,
    }


def format_remote_diff_table(data: dict[str, Any]) -> str:
    """将远程差异数据格式化为可读表格。

    Args:
        data: analyze_remote_diff 返回的数据

    Returns:
        格式化的表格字符串
    """
    if "error" in data:
        return f"错误: {data['error']}"

    lines = []
    lines.append(f"远程差异报告 · {data['remote']}/{data['branch']}")
    lines.append("=" * 60)

    # 状态图标
    status_icons = {
        "synced": "✓ 已同步",
        "ahead": f"📤 领先远程 {data['ahead']} 个提交",
        "behind": f"📥 落后远程 {data['behind']} 个提交",
        "diverged": f"⚠️  双向偏离 (领先 {data['ahead']} / 落后 {data['behind']})",
    }
    lines.append(f"状态: {status_icons.get(data['status'], data['status'])}")
    lines.append("")

    # 未推送的提交
    if data["unpushed_commits"]:
        lines.append(f"未推送的提交 ({len(data['unpushed_commits'])}):")
        for i, commit in enumerate(data["unpushed_commits"][:10], 1):
            lines.append(f"  {i}. {commit}")
        if len(data["unpushed_commits"]) > 10:
            lines.append(f"  ... 还有 {len(data['unpushed_commits']) - 10} 个")
        lines.append("")

    # 未拉取的提交
    if data["unpulled_commits"]:
        lines.append(f"远程新增提交 ({len(data['unpulled_commits'])}):")
        for i, commit in enumerate(data["unpulled_commits"][:10], 1):
            lines.append(f"  {i}. {commit}")
        if len(data["unpulled_commits"]) > 10:
            lines.append(f"  ... 还有 {len(data['unpulled_commits']) - 10} 个")
        lines.append("")

    # 远程新标签
    if data["new_remote_tags"]:
        lines.append(f"远程新标签 ({len(data['new_remote_tags'])}):")
        for tag in data["new_remote_tags"]:
            lines.append(f"  - {tag}")

    return "\n".join(lines)
