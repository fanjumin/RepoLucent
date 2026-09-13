# -*- coding: utf-8 -*-
"""文件系统扫描：编码安全读取、目录树、文件分类与 LOC 统计。

VeroRun 仓库中同时存在 UTF-8 与 GBK 系编码文本（如 AGENTS.md），
本模块统一提供 read_text_safe()：UTF-8 优先，失败回退 GB18030，最后容错替换，
确保任何文件都不会让分析中断。

v1.7.0（阶段二 2-C-1）：修复 UTF-8 BOM 缺陷。此前 read_text_safe() 以 "utf-8"
解码并保留 BOM（U+FEFF），使 `ast.parse(str)` 抛
`SyntaxError: invalid non-printable character U+FEFF`，导致带 BOM 的 .py
**整文件事实缺失**（路由/类/函数全部丢失，进而污染符号索引与报告）。
现改为解码前剥离 UTF-8 BOM，与 "utf-8-sig" 等价但保留对 GB18030 的回退链。
"""
from __future__ import annotations

import codecs
import os
from collections import Counter
from pathlib import Path

from . import config
from .config import (RepoConfig, BINARY_EXTS, CODE_EXTS, TEXT_ASSET_EXTS,
                     ROOT_LOCAL_SCRIPT_PREFIXES, ROOT_TEMP_EXTS)


#: 行注释前缀。注意 "//" 对 JS/TS 是真实行注释，对 .py 理论上不存在，
#: 但为让 scan_overview 与 py_ast 的 LOC 口径严格一致（从而可安全复用），
#: 两者统一由 count_lines() 计算。
COUNT_LINE_COMMENT_PREFIXES = ("#", "//")


def count_lines(text: str) -> tuple[int, int]:
    """返回 (总行数, 代码行)。全仓唯一的行数口径，任何统计都必须走这里。

    - 总行数：按 "\\n" 计数，末尾无换行且非空则补 1。
    - 代码行：剔除空行与行注释行（未处理块注释，故 CSS/HTML 会略偏高）。
    """
    total = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    code = sum(1 for ln in text.splitlines()
               if ln.strip() and not ln.strip().startswith(COUNT_LINE_COMMENT_PREFIXES))
    return total, code


#: 单文件读取上限（字节）。读取与哈希共用同一上限，保证「哈希内容 == 被解析内容」，
#: 避免截断点之后的差异造成无意义的缓存失效。
MAX_TEXT_BYTES = 2_000_000


def decode_text(raw: bytes) -> str:
    """编码安全解码：剥离 UTF-8 BOM → UTF-8 → GB18030 → 容错替换。

    剥离 BOM 是 v1.7.0 的关键修复：BOM 残留会让 `ast.parse()` 直接判语法错误，
    使整个文件的分析事实被静默丢弃。剥离对 LOC 统计无影响（BOM 不产生换行）。
    """
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_text_safe(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """编码安全读取文本文件。返回字符串；二进制/超大文件返回空串。"""
    try:
        raw = path.read_bytes()[:max_bytes]
    except OSError:
        return ""
    return decode_text(raw)


def _is_root_local_script(rel: Path) -> bool:
    """根目录本地调试/一次性脚本（run_final_test*.py、debug_*.py …）不参与统计。"""
    if len(rel.parts) != 1:
        return False
    name = rel.name
    if name in config.ROOT_ENTRY_ALLOWLIST:
        return False
    if rel.suffix.lower() in ROOT_TEMP_EXTS:
        return True
    if rel.suffix.lower() == ".py":
        for prefix in ROOT_LOCAL_SCRIPT_PREFIXES:
            if name.lower().startswith(prefix):
                return True
    return False


def _walk_repo_files(cfg: RepoConfig):
    """实际执行 os.walk 并产出 (相对路径, 绝对路径)。仅由 iter_repo_files 调用。"""
    root = cfg.repo_root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=cfg.follow_symlinks):
        rel_dir = Path(dirpath).relative_to(root)
        # 原地剪枝：排除目录不再下钻（_base/_templates 属插件框架资源，保留）
        dirnames[:] = [d for d in sorted(dirnames) if not cfg.is_excluded_dir(d)]
        for fname in sorted(filenames):
            rel = (rel_dir / fname)
            if _is_root_local_script(rel):
                continue  # 根目录本地调试脚本/临时产物
            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            if ext in BINARY_EXTS:
                continue
            yield rel, fpath


#: 单次运行内的遍历索引缓存：{repo_root 字符串: [(rel, fpath), ...]}。
#: 各 analyzer 原本各自调用 iter_repo_files 触发一次全仓遍历（O(模块数 × 文件数)），
#: 这是除 AST 解析外的第二大开销。同一次运行中仓库内容不会变，故可安全复用。
_WALK_INDEX: dict[str, list] = {}


def iter_repo_files(cfg: RepoConfig):
    """遍历仓库内参与分析的文件（已排除目录与二进制扩展名）。

    首次调用执行 os.walk 并在模块级索引中缓存结果；后续调用直接复用。
    """
    key = str(cfg.repo_root)
    idx = _WALK_INDEX.get(key)
    if idx is None:
        idx = list(_walk_repo_files(cfg))
        _WALK_INDEX[key] = idx
    yield from idx


def invalidate_walk_index() -> None:
    """清空遍历索引缓存（一般无需调用；供长驻进程或测试使用）。"""
    _WALK_INDEX.clear()


def scan_overview(cfg: RepoConfig, parse_cache: dict | None = None) -> dict:
    """总览统计：顶层目录、语言分布、代码量、文件 TOP。

    parse_cache 传入时，.py 文件的行数直接复用 AST 解析结果，不再二次读取全文。
    两者的 LOC 口径一致（见 COUNT_LINE_COMMENT_PREFIXES），复用不改变任何统计值。
    """
    by_top: dict[str, dict] = {}
    ext_counter: Counter[str] = Counter()
    code_by_ext: Counter[str] = Counter()
    files_meta: list[dict] = []
    total_files = total_lines = total_code = total_assets = 0

    for rel, fpath in iter_repo_files(cfg):
        top = rel.parts[0] if len(rel.parts) > 1 else "<root>"
        ext = fpath.suffix.lower()
        lines = code = 0
        is_asset = False
        if ext in CODE_EXTS:
            entry = parse_cache.get(str(rel)) if parse_cache else None
            if entry is not None and isinstance(entry.get("loc_total"), int):
                # 复用 AST 解析结果：避免重复 read + splitlines
                lines, code = entry["loc_total"], entry["loc_code"]
            else:
                text = read_text_safe(fpath)
                lines, code = count_lines(text)
            code_by_ext[ext] += code
        elif ext in TEXT_ASSET_EXTS:
            is_asset = True  # 文档/文案/数据：计文件数，不计代码行
        entry = by_top.setdefault(top, {"files": 0, "lines": 0, "code": 0, "exts": {}})
        entry["files"] += 1
        entry["lines"] += lines
        entry["code"] += code
        entry["exts"][ext] = entry["exts"].get(ext, 0) + 1
        ext_counter[ext] += 1
        total_files += 1
        total_lines += lines
        total_code += code
        if is_asset:
            total_assets += 1
        elif ext in CODE_EXTS:
            files_meta.append({"file": str(rel), "lines": lines, "code": code})

    return {
        "total_files": total_files,
        "total_asset_files": total_assets,
        "total_lines": total_lines,
        "total_code_lines": total_code,
        "code_scope_note": (
            "代码行统计口径：真实代码扩展名 "
            "(.py/.js/.ts/.vue/.html/.css/.sh/.sql)；已排除 docs/ 目录、根目录本地调试脚本、"
            "临时文件；.md/.yml/.json 等文档/文案仅计入资产文件数，不计代码行。"
        ),
        "ext_distribution": dict(ext_counter.most_common(15)),
        "by_language": [
            {"ext": ext, "files": ext_counter[ext], "code": code_by_ext[ext]}
            for ext in sorted(code_by_ext, key=lambda e: -code_by_ext[e])
        ],
        "by_top_dir": [
            {"dir": d, "files": v["files"], "lines": v["lines"], "code": v["code"]}
            for d, v in sorted(by_top.items(), key=lambda kv: -kv[1]["code"])
        ],
        "top_files": sorted(files_meta, key=lambda x: -x["code"])[:25],
    }


def render_tree(cfg: RepoConfig) -> str:
    """渲染限定深度的 ASCII 目录树，标注文件数量与关键标记。"""
    root = cfg.repo_root
    lines = [root.name + "/"]
    depth = cfg.max_tree_depth

    def children(d: Path) -> list[Path]:
        out = []
        try:
            for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                if p.is_dir() and cfg.is_excluded_dir(p.name):
                    continue
                out.append(p)
        except OSError:
            pass
        return out

    def walk(d: Path, prefix: str, level: int) -> None:
        entries = children(d)
        dirs = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        for i, p in enumerate(dirs + files):
            is_dir = p.is_dir()
            last = (i == len(dirs + files) - 1)
            branch = "└── " if last else "├── "
            if is_dir:
                n_py = sum(1 for c in p.rglob("*.py"))
                mark = ""
                if p.name == "plugins" or (d.name == "plugins" and not p.name.startswith("_")):
                    mark = "  ← 插件"
                elif p.name in ("plugin_manager", "orchestrator", "agent_matrix", "shared",
                                 "admin", "main_site", "auth-center", "providers", "i18n"):
                    mark = "  ← 核心"
                lines.append(f"{prefix}{branch}{p.name}/  ({n_py} .py){mark}")
                if level < depth:
                    walk(p, prefix + ("    " if last else "│   "), level + 1)
            else:
                lines.append(f"{prefix}{branch}{p.name}")

    walk(root, "", 1)
    return "\n".join(lines)
