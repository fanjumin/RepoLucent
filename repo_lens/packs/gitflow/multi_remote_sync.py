# -*- coding: utf-8 -*-
"""多仓库同步引擎（GitHub ↔ Gitee）。

支持双向同步、dry-run 模式、批量操作。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .repo_group import load_groups
from .git_push import smart_push
from .git_pull import smart_pull


def sync_repos(group_name: str, direction: str = "bidirectional",
               dry_run: bool = True, confirm: bool = False) -> list[dict[str, Any]]:
    """同步多仓库到 GitHub 和 Gitee。

    Args:
        group_name: 仓库组名
        direction: 同步方向
            - "github-to-gitee": 从 GitHub 同步到 Gitee
            - "gitee-to-github": 从 Gitee 同步到 GitHub
            - "bidirectional": 双向同步
        dry_run: 是否只模拟执行
        confirm: 是否确认执行真实同步

    Returns:
        每个仓库的同步结果列表
    """
    groups = load_groups()
    if group_name not in groups:
        return [{"error": f"仓库组 '{group_name}' 不存在"}]

    group = groups[group_name]
    results = []

    for repo in group.repos:
        if not repo.path.exists():
            results.append({
                "repo": str(repo.path),
                "error": "路径不存在",
            })
            continue

        # 检查是否配置了双 remote
        has_github = "github" in repo.remotes
        has_gitee = "gitee" in repo.remotes

        if not (has_github and has_gitee):
            results.append({
                "repo": str(repo.path),
                "error": "未配置双 remote（需要 github 和 gitee）",
                "remotes_configured": list(repo.remotes.keys()),
            })
            continue

        # 根据方向执行同步
        sync_results = {}

        if direction in ["github-to-gitee", "bidirectional"]:
            result = _sync_to_remote(
                repo.path, "github", "gitee", dry_run, confirm
            )
            sync_results["github_to_gitee"] = result

        if direction in ["gitee-to-github", "bidirectional"]:
            result = _sync_to_remote(
                repo.path, "gitee", "github", dry_run, confirm
            )
            sync_results["gitee_to_github"] = result

        results.append({
            "repo": str(repo.path),
            **sync_results,
        })

    return results


def _sync_to_remote(repo_root: Path, source_remote: str, target_remote: str,
                    dry_run: bool, confirm: bool) -> dict[str, Any]:
    """从一个 remote 同步到另一个 remote。

    流程：
    1. 从 source remote 拉取最新代码
    2. 推送到 target remote

    Args:
        repo_root: 仓库根目录
        source_remote: 源 remote 名称
        target_remote: 目标 remote 名称
        dry_run: 是否只模拟执行
        confirm: 是否确认执行

    Returns:
        同步结果
    """
    # 1. 从 source 拉取最新
    pull_result = smart_pull(
        repo_root,
        remote=source_remote,
        dry_run=False,  # 同步时必须真实拉取
        confirm=True,   # 同步时自动确认
    )

    if pull_result.get("error"):
        return {
            "error": f"从 {source_remote} 拉取失败: {pull_result['error']}",
            "stage": "pull",
        }

    # 2. 推送到 target
    push_result = smart_push(
        repo_root,
        remote=target_remote,
        dry_run=dry_run,
        confirm=confirm,
    )

    return {
        **push_result,
        "stage": "push",
        "source_remote": source_remote,
        "target_remote": target_remote,
    }


def format_sync_results(results: list[dict[str, Any]]) -> str:
    """将同步结果格式化为可读文本。

    Args:
        results: sync_repos 返回的结果列表

    Returns:
        格式化的字符串
    """
    lines = []
    lines.append("多仓库同步结果")
    lines.append("=" * 60)

    for r in results:
        if "error" in r and len(r) == 1:
            lines.append(f"\n❌ 错误: {r['error']}")
            continue

        repo = r.get("repo", "未知")
        lines.append(f"\n仓库: {repo}")
        lines.append("-" * 40)

        for direction_key in ["github_to_gitee", "gitee_to_github"]:
            if direction_key in r:
                result = r[direction_key]
                direction_label = "GitHub → Gitee" if direction_key == "github_to_gitee" else "Gitee → GitHub"
                lines.append(f"\n  {direction_label}:")

                if "error" in result:
                    lines.append(f"    ❌ {result['error']}")
                elif "message" in result:
                    lines.append(f"    ✅ {result['message']}")
                else:
                    lines.append(f"    ℹ️  {result}")

    return "\n".join(lines)
