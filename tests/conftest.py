# -*- coding: utf-8 -*-
"""pytest 全局夹具与自定义选项（阶段二 2-C 测试金字塔）。

分层约定
--------
- ``tests/unit/``   纯函数边界用例（临时文件驱动，秒级，无外部依赖）
- ``tests/golden/`` fixture 仓库 ``--deterministic`` 产物快照（逐字节契约验证）
- ``tests/perf/``   耗时预算（阈值刻意宽松，防环境抖动误报）
- 集成层仍是仓库根的 ``verify_*.py``，统一入口 ``python _run_regress.py``

本项目**运行时零第三方依赖**；pytest 仅出现在 ``pyproject.toml`` 的 ``test``
可选依赖组中，供 CI / 本地跑测试金字塔使用。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent                 # 工具自身仓库根
FIXTURE_REPO = TESTS_DIR / "fixture_repo"
GOLDEN_DIR = TESTS_DIR / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", default=False,
        help="用当前产物覆盖 tests/golden/ 下的快照（显式更新流程；"
             "产物变化会体现在 PR diff 中）")


@pytest.fixture(scope="session")
def update_golden(request) -> bool:
    return bool(request.config.getoption("--update-golden"))


def run_cli(repo: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    """以子进程执行 CLI（与 ``_run_regress.py`` 同口径，避免进程内状态串扰）。

    ``--no-date-dir`` 恒开：让产物直接落在 ``out``，便于按固定路径断言。
    ``REPOLUCENT_SETTINGS`` 一律剔除，避免外部环境变量把本机配置带进测试。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.pop("REPOLUCENT_SETTINGS", None)
    return subprocess.run(
        [sys.executable, "-m", "repo_lucent", "--repo", str(repo),
         "--out", str(out), "--no-date-dir", *extra],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def cli_runner():
    return run_cli


@pytest.fixture(scope="session")
def fixture_repo() -> Path:
    return FIXTURE_REPO


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture(scope="session")
def copied_fixture(tmp_path_factory) -> Path:
    """fixture 仓库的临时副本。

    用途：验证 ``--deterministic`` 产物**不泄漏绝对路径**——
    同内容仓库在不同绝对路径下必须产出逐字节相同的产物，
    这是 golden 快照能进 Git、能跨机器复现的前提。
    """
    dst = tmp_path_factory.mktemp("fixture_copy") / "fixture_repo"
    shutil.copytree(FIXTURE_REPO, dst)
    return dst
