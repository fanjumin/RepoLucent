# -*- coding: utf-8 -*-
"""golden 层：fixture 仓库 ``--deterministic`` 产物的逐字节快照（阶段二 2-C）。

定位（对标 Google Tricorder 的 golden 思路）
--------------------------------------------
产物契约的**持续性**验证。单元层只覆盖 `py_ast` / `cache` 这类纯函数，
集成层（`verify_*.py`）只断言若干字段；而 `repo_lens.json` / 报告 / AGENTS.md
的真实形态一旦变化（字段增删、排序变动、文案改动），只有逐字节对比能发现。

更新流程
--------
    pytest --update-golden        # 显式覆盖快照，产物变化随之进入 PR diff

设计约束
--------
``--deterministic`` 已剥离时间戳 / 耗时 / **绝对路径**（`repo_root` 降为目录名）。
本文件额外验证「同内容仓库在不同绝对路径下产出逐字节相同」，这是快照能进 Git、
能跨机器复现的前提。
"""
from __future__ import annotations

import difflib

import pytest

#: golden 层覆盖的产物清单（五件：契约 + 人读 + 静态报告 + Agent 读 + 符号索引）。
#: 刻意在本文件内重复声明而非从 conftest 导入——parametrize 需要收集期常量，
#: 且避免依赖 conftest 的模块导入路径（该行为受 pytest import mode 影响）。
GOLDEN_ARTIFACTS = (
    "repo_lens.json",
    "repo_lens_report.md",
    "AI_CONTEXT.md",
    "AGENTS.md",
    "repo_lens_symbols.json",
)

pytestmark = pytest.mark.golden


@pytest.fixture(scope="session")
def golden_output(cli_runner, fixture_repo, tmp_path_factory):
    """在 fixture 仓库上跑一次确定性分析（全 session 复用，避免重复跑 CLI）。"""
    out = tmp_path_factory.mktemp("golden_out") / "out"
    r = cli_runner(fixture_repo, out, "--deterministic")
    assert r.returncode == 0, f"CLI 退出码 {r.returncode}\n{r.stdout}\n{r.stderr}"
    return out


def _diff_hint(ref: bytes, cur: bytes, name: str, limit: int = 12) -> str:
    """给出「首个差异 + 前后文」的紧凑提示，避免 pytest 打印整份产物。"""
    a = ref.decode("utf-8", errors="replace").splitlines()
    b = cur.decode("utf-8", errors="replace").splitlines()
    lines = list(difflib.unified_diff(a, b, fromfile=f"golden/{name}",
                                      tofile=f"current/{name}", lineterm="",
                                      n=2))
    head = "\n".join(lines[:limit])
    return (f"{name} 快照不一致（golden {len(ref)}B / current {len(cur)}B，"
            f"行数 {len(a)} → {len(b)}）\n"
            f"若变化是预期的，用 `pytest --update-golden` 更新快照。\n{head}")


@pytest.mark.parametrize("name", GOLDEN_ARTIFACTS)
def test_artifact_matches_golden(name, golden_output, golden_dir, update_golden):
    cur = (golden_output / name).read_bytes()
    ref = golden_dir / name
    if update_golden:
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_bytes(cur)
        pytest.skip(f"已更新 golden 快照：{name}")
    assert ref.exists(), (f"缺少 golden 快照 tests/golden/{name}；"
                          f"首次生成请执行 pytest --update-golden")
    expected = ref.read_bytes()
    assert expected == cur, _diff_hint(expected, cur, name)


def test_all_golden_artifacts_present(golden_output):
    """五件产物必须齐备（少任何一件都是产出回归，而非快照问题）。"""
    missing = [n for n in GOLDEN_ARTIFACTS if not (golden_output / n).exists()]
    assert not missing, f"缺少产物：{missing}"


def test_deterministic_strips_volatile_fields(golden_output):
    """确定性模式必须剥离易变字段，否则快照无法稳定。"""
    import json
    meta = json.loads((golden_output / "repo_lens.json").read_bytes())["meta"]
    assert meta["generated_at"] is None
    assert meta["duration_ms"] is None
    assert "/" not in meta["repo_root"] and "\\" not in meta["repo_root"]


def test_golden_stable_across_absolute_paths(golden_output, copied_fixture,
                                            cli_runner, tmp_path):
    """同一份内容、不同绝对路径 → 产物必须逐字节一致（快照可跨机器/可进 Git）。"""
    out = tmp_path / "copy_out"
    r = cli_runner(copied_fixture, out, "--deterministic")
    assert r.returncode == 0, f"CLI 退出码 {r.returncode}\n{r.stderr}"
    for name in GOLDEN_ARTIFACTS:
        a = (golden_output / name).read_bytes()
        b = (out / name).read_bytes()
        assert a == b, _diff_hint(a, b, name)
