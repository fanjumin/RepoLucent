# -*- coding: utf-8 -*-
"""v1.6.0 阶段一 1-D 验收：JSON Schema 产物契约 + 轻校验器。

覆盖矩阵：
  1) 契约文件可加载，顶层 required 与实际产物顶层齐平
  2) 真实产物（fixture 全量分析，含 symbols 段）通过校验
  3) 缺少 required 字段被抓（精确定位到 meta.tool_version）
  4) 字段类型错误被抓（overview.total_files 改成字符串）
  5) 联合类型正确放行（meta.generated_at 为 null 或 string 均合法）
  6) 未知字段被忽略（MINOR 兼容策略：消费方必须忽略未识别字段）
  7) 可选段缺失不影响（无 frontend / hotspots / deep_dive 时通过）
  8) 本地 $ref 定义生效（plugins.items[0] 缺必填 → 报错路径精确到下标）
  9) 数组元素类型错误被抓（overview.by_language[0].code 改成字符串）
 10) bool 不被当作 integer（Python 里 bool 是 int 子类，必须显式排除）
 11) CLI 入口：python -m repo_lucent.schema_check rc=0（合法）/ rc=1（不合法）

启动方式：python verify_schema.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/schema_verify.json。
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent import cli                                     # noqa: E402
from repo_lucent.config import RepoConfig                       # noqa: E402
from repo_lucent.schema_check import (load_schema, validate,    # noqa: E402
                                    validate_report, SCHEMA_PATH)

RESULTS: list[dict] = []

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:220]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

def _report(repo: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    cfg = RepoConfig(repo_root=repo, out_dir=out, stable_out_dir=out)
    ns = types.SimpleNamespace(no_cache=True, deterministic=True,
                               module=None, plugin=None)
    data, _dur, pc = cli._analyze(ns, cfg)
    cli._write_reports(cfg, data, "json,symbols", parse_cache=pc)
    return json.loads((cfg.out_dir / "repo_lucent.json").read_text(encoding="utf-8"))

def _has(errs: list[str], needle: str) -> bool:
    return any(needle in e for e in errs)

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="schema_verify_"))
    repo = HERE / "tests" / "fixture_repo"

    # ---- 1) 契约可加载且顶层 required 与产物齐平 ----
    schema = load_schema()
    top_required = set(schema.get("required") or [])
    report = _report(repo, tmp / "out1")
    check("schema_loadable_and_top_required_present",
          SCHEMA_PATH.is_file() and top_required
          and top_required <= set(report),
          f"required={sorted(top_required)} "
          f"present={sorted(top_required & set(report))}")

    # ---- 2) 真实产物通过校验 ----
    errs = validate_report(report)
    check("real_report_passes_contract", not errs,
          f"errors={len(errs)}" + (f" first={errs[0]}" if errs else ""))

    # ---- 3) 缺 required 字段 ----
    bad = copy.deepcopy(report)
    bad["meta"].pop("tool_version", None)
    e3 = validate_report(bad)
    check("missing_required_field_detected",
          _has(e3, "meta") and _has(e3, "tool_version"), f"errors={e3[:2]}")

    # ---- 4) 类型错误 ----
    bad = copy.deepcopy(report)
    bad["overview"]["total_files"] = "13"
    e4 = validate_report(bad)
    check("wrong_type_detected",
          _has(e4, "overview.total_files") and _has(e4, "integer"),
          f"errors={e4[:2]}")

    # ---- 5) 联合类型放行（null / string）----
    ok_null = validate_report(report)                       # deterministic → generated_at=null
    nz = copy.deepcopy(report)
    nz["meta"]["generated_at"] = "2026-09-13 10:00:00"
    ok_str = validate_report(nz)
    check("union_type_nullable_accepted", not ok_null and not ok_str,
          f"null_errors={len(ok_null)} str_errors={len(ok_str)}")

    # ---- 6) 未知字段被忽略 ----
    extra = copy.deepcopy(report)
    extra["future_segment"] = {"whatever": [1, 2, 3]}
    extra["meta"]["some_new_optional"] = True
    check("unknown_fields_ignored", not validate_report(extra),
          "MINOR 兼容：未识别字段必须被忽略")

    # ---- 7) 可选段缺失不影响 ----
    minimal = copy.deepcopy(report)
    for k in ("frontend", "hotspots", "deep_dive", "symbols"):
        minimal.pop(k, None)
    check("optional_segments_truly_optional", not validate_report(minimal),
          f"top_keys={sorted(minimal)}")

    # ---- 8) $ref 定义生效（下标精确）----
    bad = copy.deepcopy(report)
    if bad["plugins"]["items"]:
        bad["plugins"]["items"][0].pop("version", None)
        e8 = validate_report(bad)
        check("local_ref_definition_applied",
              _has(e8, "plugins.items[0]") and _has(e8, "version"),
              f"errors={e8[:2]}")
    else:
        check("local_ref_definition_applied", False, "fixture 无插件，用例无效")

    # ---- 9) 数组元素类型错误 ----
    bad = copy.deepcopy(report)
    langs = bad["overview"].get("by_language") or []
    if langs:
        bad["overview"]["by_language"][0]["code"] = "lots"
        e9 = validate_report(bad)
        check("array_item_type_detected",
              _has(e9, "by_language[0].code"), f"errors={e9[:2]}")
    else:
        check("array_item_type_detected", False, "产物无 by_language，用例无效")

    # ---- 10) bool 不当作 integer ----
    bad = copy.deepcopy(report)
    bad["overview"]["total_files"] = True
    e10 = validate_report(bad)
    check("bool_is_not_integer",
          _has(e10, "overview.total_files") and _has(e10, "integer"),
          f"errors={e10[:2]}")

    # ---- 11) CLI 入口 ----
    good_p = tmp / "good.json"
    good_p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    bad_p = tmp / "bad.json"
    bad_p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(HERE))
    r_good = subprocess.run([sys.executable, "-m", "repo_lucent.schema_check",
                             str(good_p)], capture_output=True, text=True,
                            env=env, cwd=str(HERE), timeout=120)
    r_bad = subprocess.run([sys.executable, "-m", "repo_lucent.schema_check",
                            str(bad_p)], capture_output=True, text=True,
                           env=env, cwd=str(HERE), timeout=120)
    check("cli_entry_rc_semantics",
          r_good.returncode == 0 and r_bad.returncode == 1,
          f"good_rc={r_good.returncode} bad_rc={r_bad.returncode} "
          f"bad_msg={(r_bad.stdout or '').splitlines()[:1]}")

    # ---- 附：校验器自身的健壮性（空 schema / 非 dict 载荷不崩）----
    ok_robust = True
    try:
        validate(123, {"type": "object"})
        validate({}, {})
        validate({"a": 1}, {"type": "object", "properties": {"a": {"type": "integer"}}})
    except Exception as e:  # noqa: BLE001
        ok_robust = False
        print(f"        robustness exception: {e}")
    check("validator_robust_on_edge_inputs", ok_robust,
          "空 schema / 标量载荷 / 无 items 均不抛异常")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "schema_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
