# -*- coding: utf-8 -*-
"""规则引擎基础类型与共享工具（阶段 F / 2.0.0）。

- Finding：单条检查结果。
- AnalysisContext：规则输入（分析结果 data + 仓库配置 cfg + 插件 .py 清单）。
- scan_ast / iter_plugin_py_files：规则引擎内部对插件源码的只读派生工具，
  不与任何 analyzer 共享状态、不修改 data。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


@dataclass
class Finding:
    rule_id: str           # 如 "SEC001"
    severity: str          # error | warning | info
    category: str          # spec | security | architecture | complexity
    target: str            # 插件 identifier / 模块名 / 文件路径
    file: str | None
    line: int | None
    message: str
    evidence: str          # 触发依据：命中的代码片段或字段值


def sort_key(f: "Finding") -> tuple:
    return (SEVERITY_ORDER.get(f.severity, 9), f.rule_id, f.target, f.file or "")


@dataclass
class AnalysisContext:
    data: dict
    cfg: object
    plugin_py: list = field(default_factory=list)  # [(rel: str, abspath: Path), ...]


def _const_repr(node) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def scan_ast(text: str) -> dict:
    """轻量 AST 派生：提取函数跨度、调用点（含关键字实参）、导入。

    仅供 security/architecture/complexity 规则判定调用模式使用；
    标准库 ast，无外部依赖，对语法错误文件返回空结构。
    """
    res = {"functions": [], "calls": [], "imports": []}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return res
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            res["functions"].append((node.name, node.lineno, end))
        elif isinstance(node, ast.Import):
            for n in node.names:
                res["imports"].append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                res["imports"].append(node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                base = fn.value
                pre = base.id if isinstance(base, ast.Name) else ""
                name = f"{pre}.{fn.attr}" if pre else fn.attr
            else:
                name = ""
            kws = {}
            for kw in node.keywords:
                if kw.arg is not None:
                    kws[kw.arg] = _const_repr(kw.value)
            res["calls"].append((name, node.lineno, kws))
    return res


def iter_plugin_py_files(cfg) -> list:
    """收集 plugins/ 目录下全部 .py（rel, abspath），排除 exclude_dirs 与 .git。"""
    if cfg is None:
        return []
    root = cfg.repo_root / config.PLUGINS_DIR
    out = []
    if not root.is_dir():
        return out
    for fpath in root.rglob("*.py"):
        rel = fpath.relative_to(cfg.repo_root)
        if ".git" in rel.parts:
            continue
        if any(part in cfg.exclude_dirs for part in rel.parts):
            continue
        out.append((str(rel), fpath))
    return out
