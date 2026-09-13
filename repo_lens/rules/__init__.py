# -*- coding: utf-8 -*-
"""规则引擎注册表与汇总入口（阶段 F / 2.0.0）。

设计边界（与 gate.py 一致）：只读 data / cfg / 插件源码，不修改任何 analyzer
产物，也不写文件。规则签名统一 `rule(ctx: AnalysisContext) -> list[Finding]`，
由 run_rules 汇总排序后写入 data["findings"]。新增规则只需在 RULES 登记。
"""
from __future__ import annotations

from dataclasses import asdict

from ._types import (Finding, sort_key, AnalysisContext, iter_plugin_py_files)
from .spec import run_spec
from .security import run_security
from .architecture import run_architecture
from .complexity import run_complexity

RULES = [run_spec, run_security, run_architecture, run_complexity]

FINDINGS_SCHEMA_VERSION = "1.0"


def run_rules(data: dict, cfg) -> dict:
    """汇总全部规则，返回 findings 字典（可直接赋值给 data["findings"]）。"""
    ctx = AnalysisContext(data=data, cfg=cfg, plugin_py=iter_plugin_py_files(cfg))
    findings: list[Finding] = []
    for rule in RULES:
        try:
            findings.extend(rule(ctx))
        except Exception:
            # 单条规则异常不应拖垮主分析
            continue
    findings.sort(key=sort_key)
    summary = {
        "error": sum(1 for f in findings if f.severity == "error"),
        "warning": sum(1 for f in findings if f.severity == "warning"),
        "info": sum(1 for f in findings if f.severity == "info"),
    }
    return {
        "schema_version": FINDINGS_SCHEMA_VERSION,
        "summary": summary,
        "items": [asdict(f) for f in findings],
    }
