# -*- coding: utf-8 -*-
"""受限查询：让 Agent 不必自己 grep 730KB 的 JSON。

设计边界（重要）：

- **不做通用表达式解析器**。自造 DSL 会变成长期维护负担，这里只支持两件事：
  取字段（`--select`）与等值过滤（`--where`）。
- 路径语法仅三项：点分字段（`a.b`）、列表展开（`routes[].rule`）、下标（`routes[0].rule`）。
  不支持过滤表达式、函数、通配。
- 输出默认 JSON 且**确定性**（无时间戳），便于 Agent 解析与两次运行对比。
"""
from __future__ import annotations

import re
from typing import Any

#: 可用的行集合（scope）
SCOPES: dict[str, tuple[str, ...]] = {
    "plugins": ("plugins", "items"),
    "core": ("core", "modules"),
}

# 合法路径步骤：字段名 / 列表展开 `[]` / 整数下标 `[n]`
_PATH_STEP = re.compile(r"[^.\[\]]+|\[\]|\[\d+\]")

# 全路径形态：一个或多个「字段 / 下标」步骤，步骤间用 `.` 分隔。
# 字段后可紧跟 `[]` 或 `[n]`（无点），其后若再接字段则必须有点。
# 例：`routes[].rule`、`routes[0].rule`、`plugins[].routes[].methods`。
_PATH_FULL = re.compile(r"^[^.\[]+(?:\[\d+\]|\[\])?(?:\.[^.\[]+(?:\[\d+\]|\[\])?)*$")


class QueryError(ValueError):
    """查询参数非法（由 CLI 转成退出码 2）。"""


def resolve_rows(data: dict, scope: str) -> list[dict]:
    if scope not in SCOPES:
        raise QueryError(f"未知的查询范围：{scope}（可选：{', '.join(SCOPES)}）")
    node: Any = data
    for key in SCOPES[scope]:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return []
    return node if isinstance(node, list) else []


def infer_scope(tokens: list[str], default: str = "plugins") -> str:
    """从首个 select token 的根名推断 scope（如 `core[].name` → core）。"""
    if not tokens:
        return default
    steps = _parse_path(tokens[0])
    if steps and steps[0] in SCOPES:
        return steps[0]
    return default


def _parse_path(path: str) -> list[str]:
    """把字段路径解析为步骤列表；语法受限于三式（点分 / `[]` / `[n]`）。

    对契约之外的输入（非整数下标、不配对括号、缺失点分隔等）显式抛 QueryError，
    而不是静默重解释成错误路径——后者会让 Agent 拿到"看起来合理实则为空"的结果，
    最难排查。宁可一次明确的 rc=2，也不要默默出错。
    """
    path = path.strip()
    if not path:
        raise QueryError(f"字段路径不能为空")
    if not _PATH_FULL.match(path):
        raise QueryError(f"字段路径语法错误：`{path}`"
                         f"（仅支持 a.b / list[].field / list[0].field，"
                         f"下标必须是整数，如 [0]）")
    steps = _PATH_STEP.findall(path)
    if not steps:
        raise QueryError(f"无法解析的字段路径：{path}")
    return steps


def parse_query_path(path: str) -> None:
    """公开的路径校验入口：供 CLI 在全量分析前预检 `--select` / `--where` 字段。

    只做语法校验（抛 QueryError），不取值，使参数错误能稳定归入 rc=2，
    而非漏到顶层兜底被误判为 rc=3 内部错误。
    """
    _parse_path(path)


def _strip_scope(steps: list[str]) -> list[str]:
    """去掉 token 里的 scope 前缀：`plugins[].identifier` → `identifier`。"""
    if steps and steps[0] in SCOPES:
        steps = steps[1:]
        if steps and steps[0] == "[]":
            steps = steps[1:]
    return steps


def _col_name(steps: list[str]) -> str:
    """把解析出的路径步骤拼成干净列名：bracket 步骤（`[]` / `[n]`）前不加 `.`。

    例如 `routes[].rule` 应原样呈现，而非被错拼成 `routes.[].rule`。
    拼接结果仍为合法路径，可被 `get_path` 直接复用。
    """
    out: list[str] = []
    for i, s in enumerate(steps):
        if s.startswith("[") and s.endswith("]"):
            out.append(s)                      # 直接贴附前一段，如 routes[]
        else:
            out.append(("." + s) if i else s)
    return "".join(out)


def _walk(value: Any, steps: list[str]) -> Any:
    if not steps:
        return value
    step, rest = steps[0], steps[1:]
    if step == "[]":
        if not isinstance(value, list):
            return None
        return [_walk(v, rest) for v in value]
    if step.startswith("[") and step.endswith("]"):
        if not isinstance(value, list):
            return None
        try:
            idx = int(step[1:-1])
        except ValueError:
            return None
        return _walk(value[idx], rest) if 0 <= idx < len(value) else None
    if isinstance(value, dict) and step in value:
        return _walk(value[step], rest)
    return None


def get_path(row: Any, path: str) -> Any:
    return _walk(row, _strip_scope(_parse_path(path)))


# ---------------------------------------------------------------- 过滤 ----

def _matches(actual: Any, expected: str, negate: bool = False) -> bool:
    """等值比较，按实际类型做合理放宽（bool / 数字 / 列表包含 / 字典键存在）。"""
    hit = _eq(actual, expected)
    return (not hit) if negate else hit


def _eq(actual: Any, expected: str) -> bool:
    exp = expected.strip()
    if isinstance(actual, bool):
        return actual == (exp.lower() in ("1", "true", "yes", "y", "on"))
    if isinstance(actual, (int, float)):
        try:
            return float(actual) == float(exp)
        except ValueError:
            return False
    if actual is None:
        return exp.lower() in ("", "null", "none")
    if isinstance(actual, dict):
        return exp in actual                       # 语义：是否含有该键
    if isinstance(actual, (list, tuple, set)):
        items = [str(x) for x in actual]
        if exp in items:
            return True
        return any(isinstance(x, dict) and exp in x for x in actual)
    return str(actual) == exp


def parse_where(expr: str | None) -> list[tuple[str, str, bool]]:
    """`key=value,key!=value` → [(path, expected, negate)]，多项之间为 AND。"""
    if not expr or not expr.strip():
        return []
    conds: list[tuple[str, str, bool]] = []
    for raw in expr.split(","):
        part = raw.strip()
        if not part:
            continue
        if "!=" in part:
            key, val = part.split("!=", 1)
            negate = True
        elif "=" in part:
            key, val = part.split("=", 1)
            negate = False
        else:
            raise QueryError(f"--where 条件缺少 `=`：`{part}`（仅支持 key=value 与 key!=value）")
        key = key.strip()
        if not key:
            raise QueryError(f"--where 条件的字段名不能为空：`{part}`")
        conds.append((key, val.strip(), negate))
    return conds


def match_row(row: dict, conds: list[tuple[str, str, bool]]) -> bool:
    for path, expected, negate in conds:
        if not _matches(get_path(row, path), expected, negate):
            return False
    return True


# ---------------------------------------------------------------- 执行 ----

def run_query(data: dict, scope: str, select: list[str] | None,
              where: str | None, rows: list[dict] | None = None) -> dict:
    """执行查询，返回可 JSON 序列化的结果。

    ``rows`` 非 None 时直接作为行集合使用（v1.8.0 3-B）：SQLite 加速层从
    index.db 取出持久化的行后复用本函数，故「加速路径」与「全量分析路径」的
    投影与过滤走的是**同一段代码**，结果必然逐字段一致——无需两套实现对齐。
    """
    conds = parse_where(where)
    matched = [r for r in (rows if rows is not None else resolve_rows(data, scope))
               if match_row(r, conds)]

    fields = select or []
    if fields:
        clean = []
        for f in fields:
            steps = _strip_scope(_parse_path(f.strip()))
            clean.append(_col_name(steps))
        projected = [{c: get_path(r, c) for c in clean} for r in matched]
    else:
        clean = []
        projected = matched

    return {
        "scope": scope,
        "count": len(projected),
        "select": clean or None,
        "where": where or None,
        "rows": projected,
    }


# ---------------------------------------------------------------- 渲染 ----

def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return ", ".join(map(str, value.keys())) or "—"
    if isinstance(value, (list, tuple)):
        items = [_cell(v) for v in value]
        return ", ".join(x for x in items if x != "—") or "—"
    return str(value)


def render_table(result: dict) -> str:
    """渲染为对齐文本表格（人看）。"""
    rows = result["rows"]
    if not rows:
        return "（无匹配结果）"
    columns = result["select"] or sorted({k for r in rows for k in r})
    header = [c for c in columns]
    body = [[_cell(r.get(c)) for c in columns] for r in rows]
    widths = [max(len(header[i]), *(len(r[i]) for r in body)) for i in range(len(header))]
    sep = "-+-".join("-" * w for w in widths)

    def fmt(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    L = [fmt(header), sep]
    L += [fmt(r) for r in body]
    return "\n".join(L)
