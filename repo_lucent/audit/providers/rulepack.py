# -*- coding: utf-8 -*-
"""数据驱动的审计规则包引擎（阶段 2）：把源码级规则"数据化"，可内置默认 + 随仓库外置。

三类匹配器（全确定性、coverage=rule_only）：
- regex_line：逐行正则（含可选"整文件需/不需含守卫"）——如 SEC003 fail-open、SEC002 SSRF。
- ast：结构化检查，按 name 分派到 AST_CHECKS——如 QLT002 吞异常 / QLT003 超长函数 / QLT004 未用 import / AIB001 兜底吞真因。
- （pairing 复用 dangerous_api_scan 的窗口模型，留作后续扩展位）

规则可来自仓库文件（默认名 audit_rules.json 或 .verorun/audit_rules.json），字段同内置，
实现"规则随仓库走、零改代码新增规则"——兑现代码审计规划 §9-3。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path


# ---------------------------------------------------------------- AST 检查器 ----
def _a_bare_except(tree: ast.AST, src: str):
    """QLT002：except 体仅 pass/仅注释（吞异常）。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            body = n.body
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                out.append({"line": n.lineno, "snippet": "except …: pass（异常被吞）"})
    return out


def _a_except_return_fallback(tree: ast.AST, src: str):
    """AIB001：except 体直接 return 字面量/None/空容器且不记录异常对象 → 兜底吞真因。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            if not any(isinstance(s, ast.Return) for s in n.body):
                continue
            # 是否引用了异常名 / 是否 raise / 是否 log 该异常
            names_in_handler = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
            exc_name = n.name
            logs_exc = (exc_name and exc_name in names_in_handler) or any(
                isinstance(s, ast.Raise) for s in n.body)
            for s in n.body:
                if isinstance(s, ast.Return) and isinstance(
                        s.value, (ast.Constant, ast.List, ast.Dict, ast.Tuple)):
                    if not logs_exc:
                        out.append({"line": n.lineno,
                                    "snippet": "except 中 return 兜底常量/空值，未记录原始异常"})
                        break
    return out


def _a_long_function(tree: ast.AST, src: str, threshold: int = 80):
    """QLT003：函数体行数超阈值。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(n, "end_lineno", n.lineno)
            if end - n.lineno >= threshold:
                out.append({"line": n.lineno,
                            "snippet": f"def {n.name}() 体 {end - n.lineno} 行 ≥ {threshold}"})
    return out


def _a_unused_imports(tree: ast.AST, src: str):
    """QLT004：顶层 import 的名字在本模块从未被引用（保守：忽略 __all__ 与条件导入）。"""
    imported = {}   # 绑定名 -> lineno
    for n in ast.iter_child_nodes(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                nm = a.asname or a.name.split(".")[0]
                imported[nm] = n.lineno
        elif isinstance(n, ast.ImportFrom):
            if n.module == "__future__":
                continue
            for a in n.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = n.lineno
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            b = n
            while isinstance(b, ast.Attribute):
                b = b.value
            if isinstance(b, ast.Name):
                used.add(b.id)
    # 若声明了 __all__ 含字符串名，视为使用
    allnames = set()
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "") == "__all__" for t in n.targets):
            if isinstance(n.value, (ast.List, ast.Tuple)):
                allnames = {e.value for e in n.value.elts if isinstance(e, ast.Constant)}
    out = []
    for nm, ln in imported.items():
        if nm not in used and nm not in allnames:
            out.append({"line": ln, "snippet": f"import '{nm}' 未被引用"})
    return out


AST_CHECKS = {
    "bare_except": _a_bare_except,
    "except_return_fallback": _a_except_return_fallback,
    "long_function": _a_long_function,
    "unused_imports": _a_unused_imports,
}


# ---------------------------------------------------------------- 内置规则包 ----
# 说明：guard_anywhere=文件若含任一守卫名则整条规则跳过（降噪，如 SSRF 守卫）。
BUILTIN_RULES: list[dict] = [
    {
        "rule_id": "SEC002", "dimension": "security", "severity": "major", "confidence": "medium",
        "title": "SSRF：请求外部可控 URL 未见主机白名单守卫",
        "fix": "对外发 URL 先过主机/内网地址校验（如 _assert_public_host），拒绝内网/回环/元数据地址。",
        "references": ["OWASP:A10", "CWE-918"],
        "matcher": {"type": "regex_line", "pattern": r"\b(requests\.(get|post|put|delete|request)|urlopen)\(",
                    "guard_anywhere": ["_assert_public_host", "validate_url", "url_guard", "is_safe_url"]},
    },
    {
        "rule_id": "SEC003", "dimension": "security", "severity": "major", "confidence": "medium",
        "title": "鉴权 fail-open：`if secret and got != secret` 在 secret 为空时放行",
        "fix": "缺密钥应视为错误配置并拒绝（if not secret or got != secret: 拒绝），不得静默通过。",
        "references": ["内部缺陷 fail-open"],
        "matcher": {"type": "regex_line",
                    "pattern": r"\bif\s+\w*(secret|token|expected|key)\w*\s+and\b",
                    "guard_anywhere": []},
    },
    {"rule_id": "QLT002", "dimension": "quality", "severity": "minor", "confidence": "high",
     "title": "吞异常：except 体仅 pass", "fix": "至少记录异常或转为可诊断错误；勿静默 pass。",
     "references": ["CWE-703"],
     "matcher": {"type": "ast", "check": "bare_except"}},
    {"rule_id": "QLT003", "dimension": "quality", "severity": "minor", "confidence": "high",
     "title": "超长函数：可读性/可测性下降", "fix": "按职责拆分函数；单函数建议 <80 行。",
     "references": [],
     "matcher": {"type": "ast", "check": "long_function", "threshold": 80}},
    {"rule_id": "QLT004", "dimension": "quality", "severity": "info", "confidence": "medium",
     "title": "未使用的顶层 import", "fix": "删除未引用导入或标注 re-export（加入 __all__）。",
     "references": ["CWE-589"],
     "matcher": {"type": "ast", "check": "unused_imports"}},
    {"rule_id": "AIB001", "dimension": "ai_business", "severity": "major", "confidence": "medium",
     "title": "兜底吞真因：except 中 return 常量/空值且未记录原始异常",
     "fix": "保留异常上下文（记录/重抛带因），避免用通用兜底值掩盖真实错误根因。",
     "references": ["审计方法论：错误兜底吞真因"],
     "matcher": {"type": "ast", "check": "except_return_fallback"}},
]

_REPO_RULE_FILES = ("audit_rules.json", ".verorun/audit_rules.json")


def load_repo_rules(repo_root: Path) -> list[dict]:
    """从仓库读取外置规则（若存在），实现"规则随仓库走"。"""
    for rel in _REPO_RULE_FILES:
        p = repo_root / rel
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                rules = data.get("rules", data) if isinstance(data, dict) else data
                return [r for r in rules if isinstance(r, dict) and r.get("rule_id")]
            except (OSError, json.JSONDecodeError):
                return []
    return []


def run_line_rule(rule: dict, lines: list[str], filetext: str) -> list[dict]:
    m = rule["matcher"]
    guard = m.get("guard_anywhere") or []
    if guard and any(g in filetext for g in guard):
        return []
    rx = re.compile(m["pattern"])
    out = []
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("#"):
            continue
        if rx.search(line):
            out.append({"line": i, "snippet": s[:120]})
    return out


def run_ast_rule(rule: dict, tree: ast.AST, src: str) -> list[dict]:
    m = rule["matcher"]
    fn = AST_CHECKS.get(m["check"])
    if not fn:
        return []
    kw = {}
    if m.get("threshold") and "threshold" in fn.__code__.co_varnames:
        kw["threshold"] = int(m["threshold"])
    try:
        return fn(tree, src, **kw)
    except TypeError:
        return fn(tree, src)
