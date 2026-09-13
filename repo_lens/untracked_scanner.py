# -*- coding: utf-8 -*-
"""未跟踪文件检测与分类。

通过 git status --porcelain 识别 untracked files，
按文件类型自动分类（源码/配置/数据/构建产物/日志/文档等），
并生成 .gitignore 建议。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _check_git_available(repo_root: Path) -> bool:
    """检查目录是否为有效的 git 仓库。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def scan_untracked(repo_root: Path, classify: bool = True,
                   suggest_ignore: bool = True) -> dict[str, Any]:
    """扫描未跟踪文件并分类。

    Args:
        repo_root: 仓库根目录
        classify: 是否对文件进行分类
        suggest_ignore: 是否生成 .gitignore 建议

    Returns:
        结构化的未跟踪文件数据
    """
    if not _check_git_available(repo_root):
        return {"error": "当前目录不是有效的 git 仓库"}

    # 获取未跟踪文件列表
    result = subprocess.run(
        ["git", "status", "--porcelain", "-u"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0:
        return {"error": f"git status 失败: {result.stderr}"}

    untracked = []
    for line in result.stdout.splitlines():
        if line.startswith("?? "):
            filepath = line[3:].strip()
            if filepath:  # 跳过空行
                untracked.append(filepath)

    if not untracked:
        return {
            "total_untracked": 0,
            "categories": {},
            "ignore_suggestions": [],
        }

    # 分类逻辑
    categories: dict[str, list[str]] = {}
    if classify:
        categories = _classify_files(untracked)
    else:
        categories = {"all": untracked}

    # 生成 .gitignore 建议
    ignore_suggestions = []
    if suggest_ignore:
        ignore_suggestions = _generate_ignore_suggestions(categories)

    total = sum(len(v) for v in categories.values())

    return {
        "total_untracked": total,
        "categories": {k: v for k, v in categories.items() if v},
        "ignore_suggestions": ignore_suggestions,
    }


def _classify_files(files: list[str]) -> dict[str, list[str]]:
    """将文件按类型分类。

    Args:
        files: 文件路径列表

    Returns:
        {category: [files]} 字典
    """
    categories: dict[str, list[str]] = {
        "source_code": [],
        "config": [],
        "data": [],
        "build_artifacts": [],
        "logs": [],
        "docs": [],
        "other": [],
    }

    # 扩展名到类别的映射
    extension_map = {
        ".py": "source_code", ".js": "source_code", ".ts": "source_code",
        ".jsx": "source_code", ".tsx": "source_code", ".go": "source_code",
        ".rs": "source_code", ".java": "source_code", ".c": "source_code",
        ".cpp": "source_code", ".h": "source_code", ".cs": "source_code",
        ".rb": "source_code", ".php": "source_code", ".swift": "source_code",

        ".env": "config", ".yaml": "config", ".yml": "config",
        ".toml": "config", ".ini": "config", ".cfg": "config",
        ".conf": "config", ".properties": "config",

        ".csv": "data", ".json": "data", ".db": "data", ".sqlite": "data",
        ".xlsx": "data", ".xls": "data", ".parquet": "data",

        ".log": "logs", ".out": "logs",

        ".md": "docs", ".rst": "docs", ".txt": "docs",
        ".doc": "docs", ".docx": "docs", ".pdf": "docs",
    }

    # 构建产物模式
    build_patterns = [
        "__pycache__", ".pyc", ".pyo", ".egg-info",
        "dist/", "build/", ".venv/", "venv/", "node_modules/",
        ".next/", ".nuxt/", "target/", "out/",
    ]

    for f in files:
        categorized = False

        # 检查是否为构建产物
        for pattern in build_patterns:
            clean_pattern = pattern.rstrip("/")
            if clean_pattern in f or f.endswith(pattern):
                categories["build_artifacts"].append(f)
                categorized = True
                break

        if not categorized:
            ext = Path(f).suffix.lower()
            if ext in extension_map:
                categories[extension_map[ext]].append(f)
            else:
                categories["other"].append(f)

    return categories


def _generate_ignore_suggestions(categories: dict[str, list[str]]) -> list[str]:
    """根据分类生成 .gitignore 建议。

    Args:
        categories: 分类后的文件字典

    Returns:
        建议添加到 .gitignore 的模式列表
    """
    suggestions = set()

    # 构建产物应该被忽略
    for f in categories.get("build_artifacts", []):
        if "__pycache__" in f:
            suggestions.add("__pycache__/")
        elif f.endswith(".pyc") or f.endswith(".pyo"):
            suggestions.add("*.pyc")
        elif "dist/" in f or f.startswith("dist/"):
            suggestions.add("dist/")
        elif "build/" in f or f.startswith("build/"):
            suggestions.add("build/")
        elif ".venv/" in f or "venv/" in f:
            suggestions.add(".venv/")
        elif "node_modules/" in f:
            suggestions.add("node_modules/")
        elif ".egg-info" in f:
            suggestions.add("*.egg-info/")

    # 日志文件应该被忽略
    for f in categories.get("logs", []):
        if f.endswith(".log"):
            suggestions.add("*.log")
        elif f.endswith(".out"):
            suggestions.add("*.out")

    # 某些数据文件可能需要忽略（但不包括配置文件）
    for f in categories.get("data", []):
        if f.endswith(".db") or f.endswith(".sqlite"):
            suggestions.add("*.db")
            suggestions.add("*.sqlite")

    return sorted(suggestions)


def format_untracked_table(data: dict[str, Any]) -> str:
    """将未跟踪文件数据格式化为可读表格。

    Args:
        data: scan_untracked 返回的数据

    Returns:
        格式化的表格字符串
    """
    if "error" in data:
        return f"错误: {data['error']}"

    if data["total_untracked"] == 0:
        return "未发现未跟踪文件"

    lines = []
    lines.append(f"未跟踪文件 (总计: {data['total_untracked']})")
    lines.append("=" * 60)

    # 分类显示
    category_names = {
        "source_code": "源代码",
        "config": "配置文件",
        "data": "数据文件",
        "build_artifacts": "构建产物",
        "logs": "日志文件",
        "docs": "文档",
        "other": "其他",
    }

    for cat_key, files in data["categories"].items():
        if not files:
            continue

        cat_name = category_names.get(cat_key, cat_key)
        lines.append(f"\n{cat_name} ({len(files)}):")
        for f in files[:15]:  # 每类最多显示 15 个
            lines.append(f"  - {f}")
        if len(files) > 15:
            lines.append(f"  ... 还有 {len(files) - 15} 个")

    # .gitignore 建议
    if data["ignore_suggestions"]:
        lines.append("\n建议添加到 .gitignore:")
        for pattern in data["ignore_suggestions"]:
            lines.append(f"  {pattern}")

    return "\n".join(lines)
