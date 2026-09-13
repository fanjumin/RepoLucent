# -*- coding: utf-8 -*-
"""仓库组配置管理与批量操作支持。

通过 ~/.verorun/repos.yaml 管理多仓库分组，支持批量执行 git 相关命令。
配置文件使用简易 YAML 格式（纯文本解析，零第三方依赖）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".verorun"
CONFIG_PATH = CONFIG_DIR / "repos.yaml"


@dataclass
class RepoConfig:
    """单个仓库的配置。"""
    path: Path
    remotes: dict[str, str] = field(default_factory=dict)  # {"github": url, "gitee": url}


@dataclass
class GroupConfig:
    """仓库组的配置。"""
    name: str
    repos: list[RepoConfig] = field(default_factory=list)


def _ensure_config_dir() -> None:
    """确保配置目录存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_groups() -> dict[str, GroupConfig]:
    """加载仓库组配置。

    Returns:
        {group_name: GroupConfig} 字典
    """
    if not CONFIG_PATH.exists():
        return {}

    try:
        content = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}

    groups: dict[str, GroupConfig] = {}
    current_group: str | None = None
    current_repo: RepoConfig | None = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # 组级别：groups:
        if indent == 0 and stripped == "groups:":
            continue

        # 组名：  group-name:
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            current_group = stripped[:-1].strip()
            groups[current_group] = GroupConfig(name=current_group)
            current_repo = None
            continue

        # 仓库列表项：    - path: /path/to/repo
        if indent == 4 and stripped.startswith("- path:"):
            repo_path_str = stripped.split(":", 1)[1].strip()
            repo_path = Path(repo_path_str).expanduser()
            current_repo = RepoConfig(path=repo_path)
            if current_group and current_group in groups:
                groups[current_group].repos.append(current_repo)
            continue

        # Remote 配置：      github: https://...
        if indent == 6 and current_repo and ":" in stripped:
            key, url = stripped.split(":", 1)
            key = key.strip()
            url = url.strip()
            if key in ("github", "gitee"):
                current_repo.remotes[key] = url
            continue

    return groups


def save_groups(groups: dict[str, GroupConfig]) -> None:
    """保存仓库组配置到文件。

    Args:
        groups: {group_name: GroupConfig} 字典
    """
    _ensure_config_dir()

    lines = ["groups:"]
    for group_name, group in sorted(groups.items()):
        lines.append(f"  {group_name}:")
        for repo in group.repos:
            lines.append(f"    - path: {repo.path}")
            if repo.remotes:
                for remote_name in sorted(repo.remotes.keys()):
                    url = repo.remotes[remote_name]
                    lines.append(f"      {remote_name}: {url}")

    content = "\n".join(lines) + "\n"
    CONFIG_PATH.write_text(content, encoding="utf-8")


def add_repo_to_group(group_name: str, repo_path: Path,
                      remotes: dict[str, str] | None = None) -> None:
    """向指定组添加仓库。

    Args:
        group_name: 组名
        repo_path: 仓库路径
        remotes: remote 名称到 URL 的映射
    """
    groups = load_groups()

    if group_name not in groups:
        groups[group_name] = GroupConfig(name=group_name)

    # 检查是否已存在
    for repo in groups[group_name].repos:
        if repo.path.resolve() == repo_path.resolve():
            # 更新 remotes
            if remotes:
                repo.remotes.update(remotes)
            save_groups(groups)
            return

    groups[group_name].repos.append(RepoConfig(
        path=repo_path,
        remotes=remotes or {},
    ))
    save_groups(groups)


def remove_repo_from_group(group_name: str, repo_path: Path) -> bool:
    """从指定组移除仓库。

    Returns:
        是否成功移除
    """
    groups = load_groups()

    if group_name not in groups:
        return False

    original_count = len(groups[group_name].repos)
    groups[group_name].repos = [
        repo for repo in groups[group_name].repos
        if repo.path.resolve() != repo_path.resolve()
    ]

    if len(groups[group_name].repos) < original_count:
        save_groups(groups)
        return True
    return False


def list_groups(filter_group: str | None = None) -> dict[str, GroupConfig]:
    """列出仓库组。

    Args:
        filter_group: 如果指定，只返回该组

    Returns:
        {group_name: GroupConfig} 字典
    """
    groups = load_groups()
    if filter_group:
        if filter_group in groups:
            return {filter_group: groups[filter_group]}
        return {}
    return groups


def format_group_table(groups: dict[str, GroupConfig]) -> str:
    """将仓库组信息格式化为表格字符串。

    Returns:
        可读的表格字符串
    """
    if not groups:
        return "未配置任何仓库组"

    lines = []
    for group_name, group in sorted(groups.items()):
        lines.append(f"\n组: {group_name}")
        lines.append("=" * (len(f"组: {group_name}")))

        if not group.repos:
            lines.append("  (空)")
            continue

        for repo in group.repos:
            remote_str = ", ".join(
                f"{name}: {url}" for name, url in sorted(repo.remotes.items())
            ) if repo.remotes else "(无 remote)"

            lines.append(f"  - {repo.path}")
            lines.append(f"    Remotes: {remote_str}")

    return "\n".join(lines)
