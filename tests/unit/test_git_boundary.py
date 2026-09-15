# -*- coding: utf-8 -*-
"""DEF-02 回归防线：git 分析须界定在目标仓根，绝不向上冒泡误用外层仓库。

历史缺陷：各 git 探测用 `rev-parse --git-dir` + `cwd=repo_root`，而 git 默认向上
查找 `.git`，导致对"位于某仓之内的非 git 子目录"误判为有效仓库、读到父仓历史
（错误事实），并使产物随绝对路径变化而破坏确定性。修复后统一以
`rev-parse --show-toplevel == repo_root` 为准。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from repo_lucent import git_log_analyzer, hotspot_analyzer, remote_diff, untracked_scanner


def _have_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, timeout=30)


@pytest.fixture()
def outer_repo(tmp_path: Path) -> Path:
    """一个真实的 git 仓库根，内含一个非 git 子目录。"""
    if not _have_git():
        pytest.skip("环境无 git")
    repo = tmp_path / "outer"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "seed")
    (repo / "subdir").mkdir()
    return repo


def test_true_repo_root_is_detected(outer_repo):
    assert git_log_analyzer._check_git_available(outer_repo) is True
    assert hotspot_analyzer.git_available(outer_repo) is True
    assert remote_diff._check_git_available(outer_repo) is True
    assert untracked_scanner._check_git_available(outer_repo) is True


def test_non_git_subdir_not_mistaken_for_repo(outer_repo):
    """关键回归点：子目录自身无 .git，旧实现会冒泡判为有效仓库（True）——现必须 False。"""
    sub = outer_repo / "subdir"
    assert git_log_analyzer._check_git_available(sub) is False
    assert hotspot_analyzer.git_available(sub) is False
    assert remote_diff._check_git_available(sub) is False
    assert untracked_scanner._check_git_available(sub) is False


def test_plain_non_git_dir_is_false(tmp_path):
    assert hotspot_analyzer.git_available(tmp_path) is False
    assert git_log_analyzer._check_git_available(tmp_path) is False
