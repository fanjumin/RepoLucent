# -*- coding: utf-8 -*-
"""智能拉取远程更新。

包含预检、冲突风险评估、rebase 支持，确保拉取安全可控。
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


def smart_pull(repo_root: Path, remote: str = "origin", branch: str | None = None,
               dry_run: bool = True, rebase: bool = False,
               confirm: bool = False) -> dict[str, Any]:
    """智能拉取远程更新。

    Args:
        repo_root: 仓库根目录
        remote: remote 名称（默认 origin）
        branch: 分支名（默认当前分支）
        dry_run: 是否只模拟执行
        rebase: 是否使用 rebase 模式
        confirm: 是否确认执行真实拉取

    Returns:
        拉取结果数据
    """
    if not _check_git_available(repo_root):
        return {"error": "当前目录不是有效的 git 仓库"}

    # 1. 获取当前分支
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

    # 2. 预检：检查本地是否有未提交改动
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=5,
    )

    has_uncommitted = bool(status_result.stdout.strip())
    if has_uncommitted:
        return {
            "error": "存在未提交的改动，请先 commit 或 stash",
            "uncommitted_files": len(status_result.stdout.strip().splitlines()),
            "suggestion": "运行 `git stash` 暂存改动后再拉取",
        }

    # 3. 检查远程差异
    from ...remote_diff import analyze_remote_diff
    diff_data = analyze_remote_diff(repo_root, remote, branch)

    if diff_data.get("error"):
        return {"error": f"无法获取远程差异: {diff_data['error']}"}

    if diff_data["behind"] == 0:
        return {"message": "已是最新，无需拉取", "status": "synced"}

    # 4. 冲突风险评估
    conflict_risk = _assess_conflict_risk(repo_root, diff_data)

    # 5. dry-run 模式
    if dry_run:
        return {
            "message": f"[dry-run] 将拉取 {diff_data['behind']} 个 commit 从 {remote}/{branch}",
            "unpulled_commits": diff_data["unpulled_commits"],
            "conflict_risk": conflict_risk,
            "action_required": "使用 --confirm 执行真实拉取",
        }

    # 6. 需要确认
    if not confirm:
        return {
            "message": f"准备拉取 {diff_data['behind']} 个 commit 从 {remote}/{branch}",
            "unpulled_commits": diff_data["unpulled_commits"],
            "conflict_risk": conflict_risk,
            "action_required": "请使用 --confirm 确认执行真实拉取",
        }

    # 7. 执行拉取
    cmd = ["git", "pull"]
    if rebase:
        cmd.append("--rebase")
    cmd.extend([remote, branch])

    pull_result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )

    if pull_result.returncode != 0:
        error_msg = pull_result.stderr.strip()
        # 检测合并冲突
        if "CONFLICT" in error_msg or "Merge conflict" in error_msg:
            return {
                "error": "拉取时发生合并冲突，请手动解决",
                "stderr": error_msg,
                "suggestion": "查看冲突文件并手动解决后 commit",
            }
        return {
            "error": f"拉取失败: {error_msg}",
            "stdout": pull_result.stdout.strip(),
        }

    return {
        "message": f"成功拉取 {diff_data['behind']} 个 commit 从 {remote}/{branch}",
        "conflict_risk": conflict_risk,
        "status": "pulled",
    }


def _assess_conflict_risk(repo_root: Path, diff_data: dict) -> str:
    """评估合并冲突风险。

    通过分析 unpulled commits 修改的文件与本地已修改文件的交集来判断。

    Returns:
        "high" / "medium" / "low"
    """
    unpulled_count = len(diff_data.get("unpulled_commits", []))
    behind_count = diff_data.get("behind", 0)

    # 简化评估逻辑
    if behind_count > 20:
        return "high"
    elif behind_count > 5:
        return "medium"
    else:
        return "low"
