# -*- coding: utf-8 -*-
"""轻量代码量速查（通用化自 AI 脚本 sloc.py）。cloc 风格 code/comment/blank 统计。

面向"任意目录的快速代码量画像"，按语言 + 顶层目录双维度聚合。
注意：与 repo_lucent 主命令的 overview LOC 口径不同（那是 VeroRun 全仓、含本地脚本排除规则）；
本脚本是通用速查，勿与主命令统计混用（overlap）。

复用工具排除知识（config.DEFAULT_EXCLUDE_DIRS），独立运行时优雅降级。

用法：
    python -m repo_lucent.scriptlib.sloc_summary --root <目录> [--top 15] [--json] [--out out.json]
    repolucent.py script run sloc_summary --root <目录>

退出码：0=成功；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    from ..config import DEFAULT_EXCLUDE_DIRS
    _SKIP = set(DEFAULT_EXCLUDE_DIRS)
except Exception:  # noqa: BLE001 - 降级提示：取不到工具配置时用兜底排除集
    _SKIP = {"__pycache__", ".git", ".idea", ".vscode", "node_modules",
             "venv", ".venv", "env", "backups", "tmp", "dist", "build", ".mypy_cache"}

EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JSX", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TSX", ".vue": "Vue",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".md": "Markdown",
    ".txt": "Text", ".sql": "SQL", ".sh": "Shell", ".bat": "Batch",
    ".ps1": "PowerShell", ".xml": "XML", ".svg": "SVG", ".ini": "INI", ".cfg": "Config",
}
# lang -> (line_prefixes, block_open, block_close)
COMMENT = {
    "Python": (["#"], None, None), "Shell": (["#"], None, None),
    "YAML": (["#"], None, None), "Text": ([], None, None),
    "INI": ([";", "#"], None, None), "Config": ([";", "#"], None, None),
    "Batch": (["@echo off", "::", "rem "], None, None), "PowerShell": (["#"], None, None),
    "JavaScript": (["//"], "/*", "*/"), "TypeScript": (["//"], "/*", "*/"),
    "JSX": (["//"], "/*", "*/"), "TSX": (["//"], "/*", "*/"), "Vue": (["//"], "/*", "*/"),
    "CSS": ([], "/*", "*/"), "SCSS": (["//"], "/*", "*/"), "SQL": (["--"], "/*", "*/"),
    "HTML": ([], "<!--", "-->"), "JSON": ([], None, None), "Markdown": ([], None, None),
    "XML": ([], "<!--", "-->"), "SVG": ([], "<!--", "-->"),
}
_SOURCE = {"Python", "JavaScript", "JSX", "TypeScript", "TSX", "Vue",
           "HTML", "CSS", "SCSS", "SQL", "Shell"}


def count_root(root: Path) -> dict:
    lang = defaultdict(lambda: {"files": 0, "code": 0, "comment": 0, "blank": 0})
    mod = defaultdict(lambda: {"code": 0, "files": 0})
    total_files = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in _SKIP or part.startswith(".") for part in rel_parts[:-1]):
            continue
        ext = p.suffix.lower()
        lname = EXT_LANG.get(ext)
        if not lname:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        line_pre, bopen, bclose = COMMENT.get(lname, ([], None, None))
        in_block = False
        c_code = c_com = c_blank = 0
        for ln in lines:
            s = ln.strip()
            if in_block:
                c_com += 1
                if bclose and bclose in s:
                    in_block = False
                continue
            if not s:
                c_blank += 1
                continue
            if bopen and s.startswith(bopen):
                c_com += 1
                if not (bclose and bclose in s[len(bopen):]):
                    in_block = True
                continue
            if any(s.startswith(pp) for pp in line_pre):
                c_com += 1
                continue
            c_code += 1
        lang[lname]["files"] += 1
        lang[lname]["code"] += c_code
        lang[lname]["comment"] += c_com
        lang[lname]["blank"] += c_blank
        total_files += 1
        top = rel_parts[0] if len(rel_parts) > 1 else "(root)"
        if lname in _SOURCE:
            mod[top]["code"] += c_code
            mod[top]["files"] += 1
    tot_code = sum(s["code"] for s in lang.values())
    return {
        "by_language": {k: dict(v) for k, v in lang.items()},
        "by_module": {k: dict(v) for k, v in mod.items()},
        "total_code": tot_code,
        "total_comment": sum(s["comment"] for s in lang.values()),
        "total_blank": sum(s["blank"] for s in lang.values()),
        "total_files": total_files,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sloc_summary",
                                 description="cloc 风格代码量速查（按语言+顶层目录）。")
    ap.add_argument("--root", default=".", help="统计根目录（默认当前目录）")
    ap.add_argument("--top", type=int, default=15, help="模块榜单显示条数")
    ap.add_argument("--json", action="store_true", help="仅输出结构化 JSON")
    ap.add_argument("--out", help="把 JSON 结果写入文件")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[sloc_summary] 目录不存在: {root}", file=sys.stderr)
        return 2
    res = count_root(root)
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    print(f"=== 按语言 (code/comment/blank/files)  root={root} ===")
    for l, s in sorted(res["by_language"].items(), key=lambda kv: -kv[1]["code"]):
        print(f"{l:14s} {s['code']:8d} {s['comment']:8d} {s['blank']:8d} {s['files']:6d}")
    print(f"{'TOTAL':14s} {res['total_code']:8d} {res['total_comment']:8d} "
          f"{res['total_blank']:8d} {res['total_files']:6d}")
    print("\n=== 源码行按顶层目录 Top {} ===".format(args.top))
    for m, s in sorted(res["by_module"].items(), key=lambda kv: -kv[1]["code"])[:args.top]:
        print(f"{m:20s} {s['code']:8d} lines  {s['files']:5d} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
