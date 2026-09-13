# -*- coding: utf-8 -*-
"""前端/桌面端仓库分析器：把 VeroRun 的第二个仓库（verorun-workplace）纳入洞察范围。

设计边界：
- 复用 fs_scan 的通用扫描与统计口径（CODE_EXTS / 排除规则 / 编码安全读取），
  不引入第二套统计逻辑；
- 特化指标来自仓库的真实结构（package.json / electron/ / src/），数法见各函数注释；
- 任何子结构缺失（如非 Electron 项目）都降级为 0 / 缺省，绝不中断主流程。
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import RepoConfig
from .fs_scan import scan_overview, iter_repo_files, read_text_safe

#: 仓库名 → 角色的自动识别约定
#: 档位 A：实际生效值在 discover_frontend_repos() 内运行时读 profile.frontend_repo_names
#: （本模块被 cli 顶层导入，早于 apply_profile，模块级绑定拿不到覆盖值）。
#: 空列表 = 关闭约定发现，只认 --extra-repo 显式传入的仓库。
FRONTEND_REPO_NAMES = ("verorun-workplace",)


def _frontend_repo_names() -> tuple[str, ...]:
    from .settings import profile_get
    v = profile_get("frontend_repo_names", list(FRONTEND_REPO_NAMES))
    return tuple(str(n) for n in (v or []) if n)


def discover_frontend_repos(cfg: RepoConfig) -> list[Path]:
    """发现应纳入分析的前端/桌面端仓库。

    1) 显式 --extra-repo（cfg.extra_repos）；
    2) 约定发现：主仓库同级存在 verorun-workplace 且含 package.json；
    3) 扩展发现：常见父目录下的 verorun-workplace（D:/projects、用户文档目录等）。
    去重并校验目录存在。
    """
    found: list[Path] = []
    for p in cfg.extra_repos:
        p = Path(p).resolve()
        if (p / "package.json").is_file() and p not in found:
            found.append(p)
    fe_names = _frontend_repo_names()
    if cfg.autodiscover_frontend and fe_names:
        # 策略 2：主仓库同级目录
        for name in fe_names:
            p = (cfg.repo_root.parent / name).resolve()
            if (p / "package.json").is_file() and p != cfg.repo_root and p not in found:
                found.append(p)

        # 策略 3：扩展发现 - 常见项目根目录
        extra_search_roots = [
            cfg.repo_root.parent.parent,  # 祖父目录
            Path("D:/projects"),  # Windows 常见项目目录
            Path.home() / "Documents",  # 用户文档目录
            Path.home() / "Projects",  # 用户 Projects 目录
            Path.home() / "Desktop",  # 桌面目录
        ]
        for root in extra_search_roots:
            if not root.is_dir():
                continue
            for name in fe_names:
                p = (root / name).resolve()
                if (p / "package.json").is_file() and p != cfg.repo_root and p not in found:
                    found.append(p)
    return found


def analyze_frontend(cfg: RepoConfig, fe_root: Path) -> dict | None:
    """分析一个前端/桌面端仓库。返回结构化 dict；仓库无效返回 None。"""
    pkg_path = fe_root / "package.json"
    if not pkg_path.is_file():
        return None
    try:
        pkg = json.loads(read_text_safe(pkg_path))
    except json.JSONDecodeError:
        pkg = {}
    if not isinstance(pkg, dict):
        pkg = {}

    fe_cfg = RepoConfig(repo_root=fe_root, out_dir=cfg.out_dir)
    overview = scan_overview(fe_cfg)

    deps: dict = pkg.get("dependencies") or {}
    dev_deps: dict = pkg.get("devDependencies") or {}
    scripts: dict = pkg.get("scripts") or {}

    return {
        "root": fe_root.name,
        "path": str(fe_root),
        "kind": _detect_kind(pkg, fe_root),
        "package": {
            "name": pkg.get("name", fe_root.name),
            "version": str(pkg.get("version", "")),
            "description": pkg.get("description", ""),
            "main": pkg.get("main", ""),
            "deps_count": len(deps),
            "dev_deps_count": len(dev_deps),
            "scripts_count": len(scripts),
            "scripts_disabled": sorted(
                k for k, v in scripts.items()
                if isinstance(v, str) and "DISABLED" in v
            ),
        },
        "stack": _detect_stack(deps, dev_deps),
        "overview": {
            "total_files": overview["total_files"],
            "total_asset_files": overview["total_asset_files"],
            "total_lines": overview["total_lines"],
            "total_code_lines": overview["total_code_lines"],
            "code_scope_note": overview["code_scope_note"],
            "by_language": overview["by_language"],
            "by_top_dir": overview["by_top_dir"][:12],
            "top_files": overview["top_files"][:10],
        },
        "metrics": _count_metrics(fe_root),
        "build_editions": sorted(
            p.name for p in fe_root.glob("electron-builder*.yml")
        ),
    }


def _detect_kind(pkg: dict, fe_root: Path) -> str:
    deps = {** (pkg.get("dependencies") or {}), ** (pkg.get("devDependencies") or {})}
    if "electron" in deps or (fe_root / "electron").is_dir():
        return "electron-desktop"
    if "react" in deps or "vue" in deps:
        return "web-frontend"
    return "node-project"


def _detect_stack(deps: dict, dev_deps: dict) -> list[str]:
    all_deps = {**deps, **dev_deps}
    markers = [
        ("electron", "Electron"), ("react", "React"), ("vue", "Vue"),
        ("vite", "Vite"), ("antd", "Ant Design"), ("zustand", "Zustand"),
        ("i18next", "i18next"), ("echarts", "ECharts"),
        ("typescript", "TypeScript"), ("playwright-core", "Playwright"),
        ("better-sqlite3", "SQLite(主进程)"), ("ssh2", "SSH2"),
    ]
    return [label for key, label in markers if key in all_deps]


def _count_files(fe_root: Path, pattern: str) -> int:
    return sum(1 for _ in fe_root.glob(pattern))


def _count_metrics(fe_root: Path) -> dict:
    """前端特化指标。数法与仓库真实结构对齐（见各注释）。"""
    src = fe_root / "src"
    electron = fe_root / "electron"

    def rcount(base: Path, *exts: str) -> int:
        if not base.is_dir():
            return 0
        return sum(1 for p in base.rglob("*") if p.suffix.lower() in exts)

    locales = []
    loc_dir = src / "i18n" / "locales"
    if loc_dir.is_dir():
        locales = sorted(p.stem for p in loc_dir.glob("*.json"))
    if not locales:
        for alt in (src / "i18n", fe_root / "locales"):
            if alt.is_dir():
                locales = sorted(p.stem for p in alt.glob("*.json"))
                break

    unit_tests = _count_files(fe_root / "tests" / "unit", "*.test.ts")
    e2e_tests = rcount(fe_root / "tests", ".spec.ts")

    return {
        "pages": rcount(src / "pages", ".tsx", ".ts") if (src / "pages").is_dir() else 0,
        "components": rcount(src / "components", ".tsx", ".ts") if (src / "components").is_dir() else 0,
        "stores": _count_files(src / "stores", "*.ts"),
        "services": _count_files(src / "services", "*.ts"),
        "electron_main_ts": rcount(electron, ".ts"),
        "native_py": rcount(electron / "services" / "native", ".py"),
        "i18n_locales": locales,
        "tests_unit": unit_tests,
        "tests_e2e": e2e_tests,
        "tool_scripts": _count_files(fe_root / "scripts", "*.mjs"),
    }
