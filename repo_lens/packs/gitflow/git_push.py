# -*- coding: utf-8 -*-
"""智能推送到远程仓库。

包含预检、CI 状态检查、dry-run 模式，确保推送安全可控。
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


def smart_push(repo_root: Path, remote: str = "origin", branch: str | None = None,
               dry_run: bool = True, check_ci: bool = False,
               confirm: bool = False) -> dict[str, Any]:
    """智能推送到远程仓库。

    Args:
        repo_root: 仓库根目录
        remote: remote 名称（默认 origin）
        branch: 分支名（默认当前分支）
        dry_run: 是否只模拟执行
        check_ci: 是否检查 CI 状态
        confirm: 是否确认执行真实推送

    Returns:
        推送结果数据
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
        }

    # 3. 检查本地-远程差异
    from ...remote_diff import analyze_remote_diff
    diff_data = analyze_remote_diff(repo_root, remote, branch)

    if diff_data.get("error"):
        return {"error": f"无法获取远程差异: {diff_data['error']}"}

    if diff_data["status"] == "diverged":
        return {
            "error": "本地与远程已分叉，请先 pull 或 rebase",
            "ahead": diff_data["ahead"],
            "behind": diff_data["behind"],
            "suggestion": "建议先执行 repolens pull 拉取远程更新",
        }

    if diff_data["ahead"] == 0:
        return {"message": "已是最新，无需推送", "status": "synced"}

    # 4. CI 状态检查（可选）
    ci_status = None
    if check_ci:
        ci_status = _check_last_ci_status(repo_root, remote, branch)
        if ci_status == "failure":
            return {
                "error": "上次 CI 构建失败，建议修复后再推送",
                "ci_status": ci_status,
                "suggestion": "请检查 CI 日志并修复问题",
            }
        elif ci_status == "pending":
            return {
                "error": "CI 正在运行中，建议等待完成后再推送",
                "ci_status": ci_status,
            }

    # 5. dry-run 模式
    if dry_run:
        return {
            "message": f"[dry-run] 将推送 {diff_data['ahead']} 个 commit 到 {remote}/{branch}",
            "unpushed_commits": diff_data["unpushed_commits"],
            "ci_status": ci_status,
            "action_required": "使用 --confirm 执行真实推送",
        }

    # 6. 需要确认
    if not confirm:
        return {
            "message": f"准备推送 {diff_data['ahead']} 个 commit 到 {remote}/{branch}",
            "unpushed_commits": diff_data["unpushed_commits"],
            "ci_status": ci_status,
            "action_required": "请使用 --confirm 确认执行真实推送",
        }

    # 7. 执行推送
    push_result = subprocess.run(
        ["git", "push", remote, branch],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )

    if push_result.returncode != 0:
        return {
            "error": f"推送失败: {push_result.stderr.strip()}",
            "stdout": push_result.stdout.strip(),
        }

    return {
        "message": f"成功推送 {diff_data['ahead']} 个 commit 到 {remote}/{branch}",
        "ci_status": ci_status,
        "status": "pushed",
    }


def _check_last_ci_status(repo_root: Path, remote: str, branch: str) -> str | None:
    """检查最近一次 CI 构建状态。

    简化实现：通过读取远程仓库的 CI badge 或 API 查询。
    实际生产中可能需要调用 GitHub REST API 或 Gitee OpenAPI。

    Returns:
        "success" / "failure" / "pending" / None（跳过检查）
    """
    # TODO: 实现真实的 CI 状态查询
    # 目前返回 None 表示跳过检查
    return None
