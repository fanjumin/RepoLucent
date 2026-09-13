# -*- coding: utf-8 -*-
"""完整墙钟压测（阶段四 2a，零产品改动）。

对 REPOLUCENT_REAL_REPO 指向的真实仓做冷/热两遍全流水线计时：
  - 冷：--no-cache（禁用 AST + hotspot 增量缓存，全新分析，隔离目录）
  - 热：复用预热写入的同一 cache_dir（hotspot HEAD 短路 / 祖先增量合并生效）

计时边界用 perf_counter 包住 _analyze（含前端/热点/符号）与 _write_reports（落盘），
分别报 analyze_ms / write_ms / total_ms，并并排列出工具自报的 duration_ms，
以数值化暴露 cli.py 的 duration_ms 口径陷阱（不含前端/热点/符号/落盘）。

方法论要点：冷与热必须共享 cache_dir 才能测出热点增量缓存的加速；故先在同目录预热
（写入缓存，不计时），再于隔离目录测冷（--no-cache），最后回同一目录测热（缓存已暖）。
冷/热各用独立 out_dir 会令 cache_dir 也不同，热跑吃不到冷缓存 → 得出错误结论。

未设置 REPOLUCENT_REAL_REPO → 打印指引并以 0 退出（CI skip）。
全程只读分析 + 写临时 out，不改动仓库（cache_dir 随 --out 落在临时目录）。
"""
import json
import os
import sys
import time
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _analyze_full(repo: str, out_dir: Path, no_cache: bool) -> dict:
    from repo_lucent.cli import (_build_argparser, _setup, _analyze, _write_reports)
    argv = ["--repo", str(repo), "--out", str(out_dir)]
    if no_cache:
        argv.append("--no-cache")
    args = _build_argparser().parse_args(argv)
    cfg = _setup(args)

    t0 = time.perf_counter()
    data, tool_dur, parse_cache = _analyze(args, cfg, deep=False)   # 含前端/热点/符号
    t1 = time.perf_counter()
    _write_reports(cfg, data, "json,md,html,ai,symbols",
                   parse_cache=parse_cache)                          # 落盘
    t2 = time.perf_counter()

    return {
        "tool_duration_ms": tool_dur,                 # 工具自报（口径陷阱：仅前半段）
        "analyze_ms": round((t1 - t0) * 1000),        # 真实分析全段（含热点/符号）
        "write_ms": round((t2 - t1) * 1000),          # 落盘
        "total_ms": round((t2 - t0) * 1000),          # 端到端墙钟
        "plugins": data.get("plugins", {}).get("count", 0),
        "hotspots_present": bool(data.get("hotspots")),
    }


def main() -> int:
    repo = os.environ.get("REPOLUCENT_REAL_REPO")
    if not (repo and Path(repo).is_dir()):
        print("[wallclock] SKIPPED: 未设置 REPOLUCENT_REAL_REPO "
              "（设为真实仓根后重跑即执行冷/热墙钟压测）")
        return 0

    base = Path(tempfile.mkdtemp(prefix="wallclock_"))
    shared = base / "shared"          # 预热 + 热跑共用：缓存落此处
    cold_dir = base / "cold"          # 冷跑隔离：--no-cache，不污染 shared 缓存

    # 1) 预热（写入缓存，不计时）
    _analyze_full(repo, shared, no_cache=False)
    # 2) 冷跑（全新分析，隔离目录）
    cold = _analyze_full(repo, cold_dir, no_cache=True)
    # 3) 热跑（复用 shared 已暖缓存 → 热点增量 HEAD 短路 / AST 缓存命中）
    hot = _analyze_full(repo, shared, no_cache=False)

    report = {
        "repo": repo,
        "baseline_v170": {"cold_ms": 23100, "hot_ms": 16400,
                          "note": "v1.7.0 实测（hotspot 增量缓存上线前，口径同陷阱）"},
        "cold": cold,
        "hot": hot,
        "delta_total_ms": cold["total_ms"] - hot["total_ms"],
    }
    out = HERE / "out" / "wallclock_verify.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 完整墙钟压测（{repo}）===")
    print(f"冷跑: total={cold['total_ms']}ms  analyze={cold['analyze_ms']}ms  "
          f"write={cold['write_ms']}ms  工具自报={cold['tool_duration_ms']}ms  "
          f"plugins={cold['plugins']} hotspots={cold['hotspots_present']}")
    print(f"热跑: total={hot['total_ms']}ms  analyze={hot['analyze_ms']}ms  "
          f"write={hot['write_ms']}ms  工具自报={hot['tool_duration_ms']}ms")
    print(f"冷-热差: {report['delta_total_ms']}ms"
          f"（{'热跑更快' if report['delta_total_ms'] > 0 else '未观测到加速'}）")
    print(f"口径陷阱: 工具自报 duration_ms 仅覆盖 analyze 前半段，"
          f"遗漏前端/热点/符号/落盘（详见 {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
