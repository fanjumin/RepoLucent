# -*- coding: utf-8 -*-
"""危险 API / 调用模式静态扫描器（通用化自 AI 审计脚本 scan_raw_conn_misuse.py）。

核心可泛化模式（源自真实缺陷：裸 psycopg2 连接被直接 .execute()）：
    某变量被"取原始资源"的调用赋值后，在同一作用域窗口内被"危险方法"直接调用，
    且中途未出现"包装/转义/换句柄"标记 → 判为误用。

规则以「数据」表达（见 DEFAULT_RULES），可用 --rules 注入外部 JSON 适配任意框架，零改代码。
仅用标准库；遍历目录时复用工具的排除知识（config.DEFAULT_EXCLUDE_DIRS）。

用法：
    python -m repo_lucent.scriptlib.dangerous_api_scan --root <目录> [--rules rules.json] [--json] [--fail-on-hit]
    repolucent.py script run dangerous_api_scan --root <目录>

退出码：0=无命中；1=有命中（--fail-on-hit 或默认命中即非零，供 CI 红线）；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:  # 复用工具排除规则；独立运行时优雅降级到内置集
    from ..config import DEFAULT_EXCLUDE_DIRS
    _SKIP_DIRS = set(DEFAULT_EXCLUDE_DIRS) | {"tests", "test"}
except Exception:  # noqa: BLE001 - 降级提示：拿不到工具配置时用兜底排除集
    _SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv",
                  "tests", "test", "dist", "build", ".mypy_cache"}

# 每条规则：
#   name    规则名（输出中回显）
#   assign  匹配"取原始资源"的赋值；须含捕获组 1 = 变量名
#   use     危险使用；模板串，{var} 会被替换为捕获到的变量名
#   escape  出现即认为已安全包装/换句柄 → 停止该命中链（可为空串表示无逃逸）
#   window  向后扫描的行窗口
DEFAULT_RULES: list[dict] = [
    {
        "name": "raw_db_conn_execute",
        "desc": "裸数据库连接（get_raw_connection/_base_raw_connection）未经 PgConnection/cursor 包装直接 .execute()",
        "assign": r"^\s*(\w+)\s*=\s*(?:[\w.]+\.)?(?:get_raw_connection|_base_raw_connection)\(\)",
        "use": r"\b{var}\.execute\(",
        "escape": r"PgConnection\(|MiniAppConnection\(|WrappedConnection|\.cursor\(\)",
        "window": 40,
    },
]

_CONT_BLOCK = ("try:", "if ", "except", "for ", "with ", "else", "elif", "while", "finally")


def _load_rules(path: str | None) -> list[dict]:
    if not path:
        return DEFAULT_RULES
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    req = {"name", "assign", "use"}
    out = []
    for i, r in enumerate(data):
        missing = req - set(r)
        if missing:
            raise ValueError(f"rules[{i}] 缺字段 {sorted(missing)}")
        r.setdefault("escape", "")
        r.setdefault("window", 40)
        r.setdefault("desc", r["name"])
        out.append(r)
    if not out:
        raise ValueError("rules 文件为空")
    return out


def scan(root: Path, rules: list[dict]) -> list[dict]:
    """返回命中列表，每项 {rule, file, line, snippet}。root 为 Path。"""
    compiled = [
        {
            "rule": r,
            "assign_re": re.compile(r["assign"]),
            "escape_re": re.compile(r["escape"]) if r["escape"] else None,
            "window": int(r["window"]),
        }
        for r in rules
    ]
    hits: list[dict] = []
    for py in sorted(root.rglob("*.py")):
        rel_parts = set(py.relative_to(root).parts)
        if rel_parts & _SKIP_DIRS:
            continue
        try:
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for c in compiled:
            ar = c["assign_re"]
            for i, line in enumerate(lines):
                m = ar.match(line)
                if not m:
                    continue
                var = m.group(1)
                indent = len(line) - len(line.lstrip())
                use_re = re.compile(c["rule"]["use"].format(var=re.escape(var)))
                end = min(i + c["window"], len(lines))
                for j in range(i + 1, end):
                    nl = lines[j]
                    s = nl.strip()
                    if not s:
                        continue
                    cur_indent = len(nl) - len(nl.lstrip())
                    if cur_indent < indent and not s.startswith(_CONT_BLOCK):
                        break  # 离开该赋值的作用域
                    if c["escape_re"] and c["escape_re"].search(s):
                        break
                    if use_re.search(s):
                        hits.append({
                            "rule": c["rule"]["name"],
                            "file": str(py.relative_to(root)).replace("\\", "/"),
                            "line": j + 1,
                            "snippet": s[:120],
                        })
                        break
    return hits


def _iter_py_roots(root: Path):
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"需要目录: {root}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dangerous_api_scan",
        description="危险 API/调用模式静态扫描（通用、规则可配置）。")
    ap.add_argument("--root", default=".", help="扫描根目录（默认当前目录）")
    ap.add_argument("--rules", help="外部规则 JSON 文件；缺省用内置规则集")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON（供机器消费）")
    ap.add_argument("--fail-on-hit", action="store_true",
                    help="有命中即以退出码 1 结束（默认行为即如此；此开关用于 CI 语义显式化）")
    try:
        args = ap.parse_args(argv)
        root = Path(args.root).resolve()
        _iter_py_roots(root)
        rules = _load_rules(args.rules)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError, ValueError) as e:
        print(f"[dangerous_api_scan] 参数/环境错误：{e}", file=sys.stderr)
        return 2

    hits = scan(root, rules)
    if args.json:
        print(json.dumps({"root": str(root), "count": len(hits), "hits": hits},
                         ensure_ascii=False))
    else:
        if hits:
            print(f"命中 {len(hits)} 处危险调用模式：")
            for h in hits:
                print(f"  [{h['rule']}] {h['file']}:{h['line']}: {h['snippet']}")
        else:
            print("未发现危险调用模式（0 命中）")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
