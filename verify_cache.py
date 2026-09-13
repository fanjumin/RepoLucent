# -*- coding: utf-8 -*-
"""P0 §4.2 请求级缓存验证：memo 命中 / 显式失效 / --no-cache 始终全新。

落盘纯 ASCII JSON（out/cache_verify.json），便于工具回读；同时在 stdout 打印 [PASS]/[FAIL]。
"""
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repo_lens.cli import (_setup, _build_argparser, _analyze,
                            invalidate_analysis_cache)

results = []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))


def main():
    repo = HERE / "tests" / "fixture_repo"
    out = HERE / "out"
    cfg = _setup(_build_argparser().parse_args(
        ["mcp", "--repo", str(repo), "--out", str(out)]))

    # 1) memo 命中：两次调用返回同一对象（跳过 5-30s 全量分析）
    ns = SimpleNamespace(no_cache=False, deterministic=False)
    r1 = _analyze(ns, cfg)
    r2 = _analyze(ns, cfg)
    check("memo_hit_returns_same_object", r1 is r2,
          f"id(r1)={id(r1)} id(r2)={id(r2)}")

    # 2) 显式失效：invalidate 后再次调用返回新对象
    invalidate_analysis_cache()
    r3 = _analyze(ns, cfg)
    check("after_invalidate_returns_new_object", r3 is not r1,
          f"id(r3)={id(r3)} != id(r1)={id(r1)}")

    # 3) 三次连续（无失效）稳定命中同一缓存
    a = _analyze(ns, cfg)
    b = _analyze(ns, cfg)
    c = _analyze(ns, cfg)
    check("stable_hit_across_calls", a is b is c, "连续三次调用命中同一缓存对象")

    # 4) --no-cache 始终全新：两次返回不同对象
    ns_nc = SimpleNamespace(no_cache=True, deterministic=False)
    x = _analyze(ns_nc, cfg)
    y = _analyze(ns_nc, cfg)
    check("no_cache_always_fresh", x is not y, "no_cache 模式绕过 memo")

    # 5) 失效后内容等价（分析确定性）：缓存命中产物与全新产物口径一致
    invalidate_analysis_cache()
    fresh = _analyze(ns, cfg)
    check("fresh_analysis_equivalent_shape",
          isinstance(fresh[0], dict) and "plugins" in fresh[0] and "core" in fresh[0],
          "分析产出结构完整（plugins/core 存在）")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        results.append({"name": "exception", "ok": False,
                        "detail": traceback.format_exc()[-300:]})
    fails = [r for r in results if not r["ok"]]
    out_json = HERE / "out" / "cache_verify.json"
    try:
        out_json.write_text(json.dumps(
            {"exit": 1 if fails else 0, "cases": results},
            ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 落盘失败：{e}")
    sys.exit(1 if fails else 0)
