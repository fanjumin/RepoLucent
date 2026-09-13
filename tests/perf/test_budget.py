# -*- coding: utf-8 -*-
"""性能门禁（阶段二 2-C 性能层）。

门禁策略（方案 2-C 明确要求）
----------------------------
- **CI 硬门禁只针对 fixture 仓库**，且阈值刻意宽松（数量级门），
  目的是拦住「灾难性退化」，不追求精细卡点——共享盘 / 网络盘上的
  绝对耗时抖动可达数倍，精细阈值必然误报。
- **真实仓库指标不进 CI**：仅在设置 ``REPOLENS_PERF_REPO`` 时采集并打印，
  供人工记录进 CHANGELOG。

另外固化两条**结构性**门禁（不依赖机器性能，因此绝对稳定）：
  1. 小批量（< PARALLEL_MIN_FILES）**不得**启动进程池——否则小仓库会因
     进程启动开销反而变慢（本机实测：363 个微型文件时并行比串行慢 14%）；
  2. 命中缓存时**不得读盘**（由单元层 test_cache 覆盖，此处复验端到端行为）。
"""
from __future__ import annotations

import os
import re

import pytest

from repo_lens.py_ast import PARALLEL_MIN_FILES, parse_files_parallel

pytestmark = pytest.mark.perf

#: fixture 级别宽松上限：正常 < 6s，超过 5 倍即视为灾难性退化。
FIXTURE_BUDGET_MS = 30_000

PERF_REPO_ENV = "REPOLENS_PERF_REPO"


def _summary(text: str) -> dict:
    """把 `--summary-only` 的 key=value 输出解析成字典。"""
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def test_small_batch_never_starts_process_pool(monkeypatch, tmp_path):
    """结构性门禁：小批量必须走串行，绝不因「优化」给自身加进程启动开销。"""
    import repo_lens.py_ast as A

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("小批量不应启动进程池（PARALLEL_MIN_FILES 失效）")

    items = []
    for i in range(5):
        p = tmp_path / f"s{i}.py"
        p.write_bytes(f"x{i} = {i}\n".encode())
        items.append((str(p), f"s{i}.py"))
    assert len(items) < PARALLEL_MIN_FILES

    monkeypatch.setattr(A, "ProcessPoolExecutor", _Boom)
    result = parse_files_parallel(items, workers=4)          # 沿用默认 min_files
    assert [r[0] for r in result] == [it[1] for it in items]


def test_workers_one_is_serial(monkeypatch, tmp_path):
    """workers=1 是兼容开关：即使批量很大也必须串行。"""
    import repo_lens.py_ast as A

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("workers=1 不应启动进程池")

    p = tmp_path / "x.py"
    p.write_bytes(b"x = 1\n")
    monkeypatch.setattr(A, "ProcessPoolExecutor", _Boom)
    assert len(parse_files_parallel([(str(p), "x.py")] * 300, workers=1)) == 300


def test_fixture_cold_run_within_budget(cli_runner, fixture_repo, tmp_path):
    """fixture 冷跑（禁用缓存）在宽松预算内完成。"""
    r = cli_runner(fixture_repo, tmp_path / "cold", "--no-cache", "--summary-only")
    assert r.returncode == 0, r.stderr
    dur = int(_summary(r.stdout)["duration_ms"])
    print(f"\n    fixture 冷跑内部耗时 = {dur} ms（预算 {FIXTURE_BUDGET_MS} ms）")
    assert dur < FIXTURE_BUDGET_MS, f"fixture 冷跑 {dur} ms 超出预算"


def test_fixture_hot_run_not_slower_than_cold(cli_runner, fixture_repo, tmp_path):
    """热跑必须不快于冷跑，且**重解析数为 0**（缓存确实生效，而非仅不报错）。

    这里刻意不用 `--summary-only`：该模式在打印摘要前就 return，
    既不写产物也不打印「重解析 N/M」完成行，无法验证缓存是否真的命中。
    """
    out = tmp_path / "hot"
    r1 = cli_runner(fixture_repo, out)          # 冷：解析并写缓存
    r2 = cli_runner(fixture_repo, out)          # 热：应全部命中
    assert r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr

    c1, rep1 = _completion(r1.stdout)
    c2, rep2 = _completion(r2.stdout)
    print(f"\n    fixture 冷 {c1} ms（重解析 {rep1}）→ 热 {c2} ms（重解析 {rep2}）")

    assert rep1.split("/")[0] != "0", "冷跑竟然全命中，缓存未被清空，测试前提不成立"
    assert rep2.startswith("0/"), f"热跑仍重解析 {rep2}，缓存未生效"
    assert c2 <= c1 + 2000, f"热跑 {c2} ms 显著慢于冷跑 {c1} ms"


_COMPLETION = re.compile(r"重解析 (\d+)/(\d+) 文件 · 耗时 (\d+) ms")


def _completion(stdout: str) -> tuple[int, str]:
    """从完成行抽取 (耗时ms, "重解析 N/M")。缺失时报错而非静默兜底。"""
    m = _COMPLETION.search(stdout)
    assert m, f"未找到完成行，stdout 尾部：\n{stdout[-400:]}"
    return int(m.group(3)), f"{m.group(1)}/{m.group(2)}"


@pytest.mark.skipif(not os.environ.get(PERF_REPO_ENV),
                    reason=f"未设置 {PERF_REPO_ENV}，跳过真实仓库指标采集")
def test_real_repo_metrics_are_reported():
    """真实仓库指标采集（不进 CI 硬门禁，仅供人工记录进 CHANGELOG）。

    运行方式：
        set REPOLENS_PERF_REPO=F:\\Sites\\VeroRun
        pytest tests/perf -s
    """
    from pathlib import Path
    import subprocess
    import sys

    repo = Path(os.environ[PERF_REPO_ENV])
    assert repo.is_dir(), f"{PERF_REPO_ENV} 指向的不是目录：{repo}"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    for tag in ("cold", "hot"):
        r = subprocess.run(
            [sys.executable, "-m", "repo_lens", "--repo", str(repo),
             "--out", str(Path(os.environ.get("TEMP", "/tmp")) / "repolens_perf"),
             "--no-date-dir", "--summary-only"],
            capture_output=True, text=True, env=env)
        s = _summary(r.stdout)
        print(f"\n    {tag}: duration_ms={s.get('duration_ms')} "
              f"files={s.get('files')} lines={s.get('lines_total')} "
              f"plugins={s.get('plugins')} routes={s.get('routes_total')}")
