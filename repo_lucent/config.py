# -*- coding: utf-8 -*-
"""集中配置：仓库定位、排除规则、核心模块知识库、输出上限。

扩展指引：
- 新增核心模块职责描述 → 改 KNOWN_CORE_MODULES
- 调整目录/文件排除 → 改 DEFAULT_EXCLUDE_DIRS / BINARY_EXTS
- 调整输出体量上限 → 改 RepoConfig 各 max_* 字段
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .settings import load_settings  # P0 §4.1 配置外部化：工具级配置读取（单向，无循环依赖）

#: 工具版本号统一由 repo_lucent/__init__.py 的 TOOL_VERSION 提供（单一事实源），
#: 本模块不再重复定义，避免双源分叉（历史遗留已在 v1.4.0 修复）。

# ---------------------------------------------------------------- 排除规则 ----

#: 目录级排除（仓库内不属于"系统核心 / 业务插件"阅读范围的内容）
DEFAULT_EXCLUDE_DIRS = {
    # 版本控制 / 工具链缓存
    ".git", ".github", ".pytest_cache", ".cache", ".mypy_cache",
    ".idea", ".vscode", ".trae", ".openclaw", ".stock_deps",
    ".trae-html-share-packages",
    # 虚拟环境 / 依赖
    "venv", ".venv", "env", "node_modules",
    # 本工具自身目录（tools/dev_insight 及 out/ 自产报告不入统计）
    "dev_insight",
    # Python 运行产物
    "__pycache__",
    # 运行时数据 / 备份 / 实验代码 / 构建产物 / 临时
    "data", "backups", "server_backup", "tmp", "temp",
    "poc", "poc2", "dist", "dist-electron", "dist-new",
    "build", "release", "release-staging",
    "verorun-plugin-test-report",
    # 文档目录（开发规范文档不参与代码量统计；规范索引仍按需单独读取）
    "docs",
}

#: 根目录本地调试/一次性脚本文件名前缀（如 run_final_test*.py / debug_*.py）
#: 这些是开发者留在仓库根的本地工具，不算系统代码，不参与统计。
ROOT_LOCAL_SCRIPT_PREFIXES = (
    "run_final_test", "run_full_test", "run_comprehensive_test",
    "debug_", "verify_", "check_", "inspect_", "find_", "fetch_",
    "get_", "test_", "deploy_patch", "do_",
)

#: 根目录保留的系统入口脚本（白名单，防止被前缀规则误伤）
ROOT_ENTRY_ALLOWLIST = {
    "auth_server.py", "health_guardian.py",
    "run_gunicorn.py", "run_auth_wsgi.py", "version.py",
}

#: 根目录临时产物扩展名（抓包/令牌/测试结果/签名等，不参与统计）
ROOT_TEMP_EXTS = {".json", ".sig", ".log", ".bak", ".tmp"}

#: 计入"代码行"的扩展名（真实代码口径）
CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue",
    ".html", ".htm", ".css", ".scss", ".sh", ".sql",
}

#: 文档/文案/数据类扩展名：文件数照算，但不计代码行（资产文件）
TEXT_ASSET_EXTS = {
    ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
}

#: 二进制 / 非代码文件扩展名（不参与 LOC 与 AST 解析）
BINARY_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".pdf",
    ".db", ".sqlite", ".sqlite3", ".lock", ".sig",
    ".mp3", ".mp4", ".wav", ".webm",
}

#: 参与统计的代码/配置/文档扩展名 → 已拆分为 CODE_EXTS（计代码行）
#: 与 TEXT_ASSET_EXTS（计文件数、不计行），见上方定义。

# ------------------------------------------------------ 核心模块知识库 ----
# 描述用于报告可读性；未收录的顶层目录将按"自动识别的 Python 代码模块"处理。

KNOWN_CORE_MODULES = {
    "plugin_manager": "插件框架：发现/加载/生命周期/Hooks/事件总线/技能注册/商店",
    "orchestrator": "工作流编排引擎：DAG 节点、调度器、触发分发、安全求值",
    "agent_matrix": "Agent 矩阵：系统核心角色（is_system=1）与能力聚合注册",
    "admin": "管理后台服务（Flask 应用）：插件管理、商店、系统设置",
    "main_site": "主站服务（Flask 应用）：门户页面与站点能力",
    "auth-center": "认证中心：登录、会话、双因素、系统管理 API",
    "health_service": "健康检查服务：部署自检与就绪探测",
    "health_guardian": "守护进程：系统看护、完整性校验",
    "providers": "Provider 接入层：LLM/模型/外部服务统一适配",
    "shared": "共享基础设施：HTTP 客户端、日志、可观测性",
    "i18n": "国际化：系统级翻译函数 _() 与语言管理",
    "veroguard": "授权防护：许可证校验、防篡改（授权资产保护）",
    "prompts": "系统提示词资源库",
    "sdks": "对外 SDK",
    "themes": "主题资源（design-system / themes.css）",
    "templates": "服务端 Jinja2 页面模板",
    "static": "静态资源",
}

#: 自动识别核心目录时排除的辅助/工具类顶层目录（不算“系统核心”）
AUTO_CORE_EXCLUDE = {"docs", "deploy", "scripts", "tools"}

#: 插件根目录与清单文件名（与 plugin_manager/discovery.py 保持一致）
PLUGINS_DIR = "plugins"
MANIFEST_NAME = "plugin.json"

#: manifest 必填字段回退值（优先从 docs/plugin-manifest.schema.json 动态读取）
MANIFEST_REQUIRED_FALLBACK = [
    "identifier", "name", "version", "description",
    "author", "min_app_version", "agent_role", "capabilities",
]

#: 开发规范相关重点文档（存在则收录索引）
KEY_DOCS = [
    "AGENTS.md",
    "GUIDE.md",
    "CHANGELOG.md",
    "README.md",
    "docs/developer-guide.md",
    "docs/plugin-standard-v1.7.md",
    "docs/plugin-manifest.schema.json",
    "docs/i18n-standard.md",
    "docs/official-api-security-spec.md",
]

#: 规范文档标题正则（plugin-standard-v*.md）
PLUGIN_STANDARD_RE = "docs/plugin-standard-v*.md"


# ------------------------------------------------------ 档位 A 通用化 ----
# 分析口径（仓库签名/组件/核心知识库/排除集等）可由 settings.json 的 profile 段覆盖。
# 硬约束：无 profile 段（或字段为 null）时，以下 apply_profile() 不改变任何常量值，
# 行为与改造前完全等价（既有 47 个验证用例必须全绿）。

def apply_profile() -> str:
    """按 settings.profile 覆盖模块级常量；返回生效的 profile 名。

    必须在 cli._setup 中最早调用，且不早于任何 analyzer 的导入绑定——
    因此所有「会受 profile 影响」的常量，使用方一律运行时查 config.X
    （from .config import X 是值绑定，apply_profile 后不会自动更新）。

    回落基线：首次调用时快照内置原始值（_PROFILE_BASELINE）。后续调用中，
    未被 profile 覆盖的字段一律从快照回落而非沿用当前值——保证同一进程内
    多次切换 profile（测试/长驻服务热改配置）不会发生跨 profile 值污染。
    """
    global PLUGINS_DIR, MANIFEST_NAME, MANIFEST_REQUIRED_FALLBACK, KNOWN_CORE_MODULES
    global AUTO_CORE_EXCLUDE, DEFAULT_EXCLUDE_DIRS, ROOT_ENTRY_ALLOWLIST
    global KEY_DOCS, PLUGIN_STANDARD_RE

    from .settings import profile_get
    name = profile_get("name", "verorun")

    # ---- 内置基线快照（仅首次）----
    global _PROFILE_BASELINE
    if _PROFILE_BASELINE is None:
        _PROFILE_BASELINE = {
            "PLUGINS_DIR": PLUGINS_DIR,
            "MANIFEST_NAME": MANIFEST_NAME,
            "MANIFEST_REQUIRED_FALLBACK": list(MANIFEST_REQUIRED_FALLBACK),
            "KNOWN_CORE_MODULES": dict(KNOWN_CORE_MODULES),
            "AUTO_CORE_EXCLUDE": set(AUTO_CORE_EXCLUDE),
            "DEFAULT_EXCLUDE_DIRS": set(DEFAULT_EXCLUDE_DIRS),
            "ROOT_ENTRY_ALLOWLIST": set(ROOT_ENTRY_ALLOWLIST),
            "KEY_DOCS": list(KEY_DOCS),
            "PLUGIN_STANDARD_RE": PLUGIN_STANDARD_RE,
        }
    base = _PROFILE_BASELINE

    comp = profile_get("component", None) or {}
    PLUGINS_DIR = str(comp.get("dir", base["PLUGINS_DIR"]) or "")
    MANIFEST_NAME = str(comp.get("manifest", base["MANIFEST_NAME"]) or "")
    if comp.get("required_fields") is not None:
        MANIFEST_REQUIRED_FALLBACK = list(comp["required_fields"])
    else:
        MANIFEST_REQUIRED_FALLBACK = list(base["MANIFEST_REQUIRED_FALLBACK"])

    # core_modules：增量合并语义——profile 里出现的键覆盖内置，未出现的保留内置；
    # null / 空 dict = 完全使用内置知识库（拷贝后合并，绝不原地改内置 dict）。
    extra_cm = profile_get("core_modules", None)
    merged = dict(base["KNOWN_CORE_MODULES"])
    if isinstance(extra_cm, dict):
        merged.update({k: v for k, v in extra_cm.items() if isinstance(v, str)})
    KNOWN_CORE_MODULES = merged

    AUTO_CORE_EXCLUDE = set(profile_get("auto_core_exclude", base["AUTO_CORE_EXCLUDE"]))
    DEFAULT_EXCLUDE_DIRS = set(profile_get("exclude_dirs", base["DEFAULT_EXCLUDE_DIRS"]))
    ROOT_ENTRY_ALLOWLIST = set(profile_get("root_entry_allowlist", base["ROOT_ENTRY_ALLOWLIST"]))
    KEY_DOCS = list(profile_get("key_docs", base["KEY_DOCS"]))
    psr = profile_get("standard_doc_pattern", base["PLUGIN_STANDARD_RE"])
    PLUGIN_STANDARD_RE = str(psr or "")
    return str(name)


#: 内置分析口径基线快照（apply_profile 首次调用时填充）
_PROFILE_BASELINE: dict | None = None


def repo_signature() -> dict:
    """仓库定位特征（autodetect_repo 用）：{"dirs": [...], "files": [...]}。

    未配置时回落到 VeroRun 现状签名（dirs+files 均空 = 接受任意显式 --repo 目录）。
    """
    from .settings import profile_get
    sig = profile_get("repo_signature", None)
    if not isinstance(sig, dict):
        sig = {"dirs": ["plugins", "plugin_manager"], "files": []}
    return {"dirs": list(sig.get("dirs") or []),
            "files": list(sig.get("files") or [])}


# ------------------------------------------------- 产物按日期归档（DONE-15） ----
# 需求：产物目录加日期层（out/<仓库>/<YYYY-MM-DD>/），便于历史回溯与跨日对比。
# 设计要点：
#   * 只有「产物」下沉到日期目录；AST 缓存与快照基线留在稳定根 stable_out_dir，
#     否则每天新建目录会导致缓存全失效、基线无法跨日 diff（见 cache_dir/snapshot_dir）。
#   * 开关：settings.output.date_dir（默认 true）> 环境变量 REPO_LUCENT_DATE_DIR
#     > CLI --no-date-dir（最高优先，单次关闭）。
#   * 稳定根写 _latest.txt 记录最近一次产物目录，供脚本/UI 定位。

_DATE_TRUE = {"1", "true", "yes", "on"}
_DATE_FALSE = {"0", "false", "no", "off"}
LATEST_POINTER_NAME = "_latest.txt"


def date_dir_setting() -> tuple[bool, str]:
    """读取日期归档开关与格式：(enabled, strftime_fmt)。"""
    import os
    out = load_settings().get("output") or {}
    if not isinstance(out, dict):
        out = {}
    enabled = bool(out.get("date_dir", True))
    fmt = str(out.get("date_format") or "%Y-%m-%d")
    env = os.environ.get("REPO_LUCENT_DATE_DIR") or os.environ.get("VR_INSIGHT_DATE_DIR")
    if env is not None:
        v = str(env).strip().lower()
        if v in _DATE_TRUE:
            enabled = True
        elif v in _DATE_FALSE:
            enabled = False
    return enabled, fmt


def apply_date_dir(cfg: "RepoConfig", base_out: "Path",
                   enabled: bool | None = None) -> "RepoConfig":
    """把 cfg.out_dir 下沉到 <base_out>/<日期>，稳定根记入 cfg.stable_out_dir。

    enabled=None → 取 settings/env；显式 False → 保持 base_out（历史行为）。
    """
    import datetime as _dt
    base = Path(base_out)
    auto_enabled, fmt = date_dir_setting()
    if enabled is None:
        enabled = auto_enabled
    cfg.stable_out_dir = base
    if enabled:
        try:
            stamp = _dt.datetime.now().strftime(fmt)
        except (ValueError, TypeError):
            stamp = _dt.datetime.now().strftime("%Y-%m-%d")
        cfg.out_dir = base / stamp
    else:
        cfg.out_dir = base
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    if enabled:
        try:  # 指针写入失败不影响主流程（只读磁盘 / 权限受限场景）
            (base / LATEST_POINTER_NAME).write_text(
                f"{cfg.out_dir.name}\n{cfg.out_dir}\n", encoding="utf-8")
        except OSError:
            pass
    return cfg


def latest_artifact_dir(base_out: "Path") -> "Path | None":
    """读取稳定根下的最近一次产物目录（不存在/指针缺失返回 None）。"""
    base = Path(base_out)
    p = base / LATEST_POINTER_NAME
    try:
        name = p.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not name:
        return None
    cand = base / name
    return cand if cand.is_dir() else None


def list_artifact_dirs(base_out: "Path") -> list[dict]:
    """列出稳定根下全部产物目录（按名称倒序），供 UI/脚本做历史回溯。"""
    base = Path(base_out)
    if not base.is_dir():
        return []
    items = []
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        try:
            files = sorted(f.name for f in d.iterdir() if f.is_file())
        except OSError:
            files = []
        try:
            mtime = int(d.stat().st_mtime)
        except OSError:
            mtime = 0
        items.append({"name": d.name, "path": str(d), "mtime": mtime,
                      "files": files, "file_count": len(files)})
    return items


# ------------------------------------------------- AGENTS.md 输出模式（v1.6.0） ----
# 三态（阶段一 1-A）：
#   workspace（默认）—— 写到产物目录，随日期归档，绝不触碰仓库根，零意外；
#   repo            —— 写仓库根 AGENTS.md，令所有 Agent 共享上下文（会改变 git status，
#                      属有意行为，必须显式选择）；
#   off             —— 不生成。
# 解析优先级：CLI --agents-md > 环境变量 REPO_LUCENT_AGENTS_MD > settings.output.agents_md
#             > 内置默认 workspace。非法值一律回落默认，绝不因配置写错而中断分析。

AGENTS_MD_MODES = ("workspace", "repo", "off")
AGENTS_MD_DEFAULT = "workspace"


def agents_md_setting() -> str:
    """返回生效的 AGENTS.md 输出模式（CLI 覆盖由调用方先行处理）。"""
    import os
    out = load_settings().get("output") or {}
    mode = out.get("agents_md") if isinstance(out, dict) else None
    env = (os.environ.get("REPO_LUCENT_AGENTS_MD")
           or os.environ.get("VR_INSIGHT_AGENTS_MD"))
    if env is not None and str(env).strip():
        mode = env
    m = str(mode or "").strip().lower()
    return m if m in AGENTS_MD_MODES else AGENTS_MD_DEFAULT


# ---------------------------------------------------------------- 配置对象 ----

@dataclass
class RepoConfig:
    """一次分析任务的全部参数。"""

    repo_root: Path
    out_dir: Path
    exclude_dirs: set = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    max_tree_depth: int = 2                 # 目录树展示深度
    max_routes_per_plugin: int = 60         # 每插件路由明细上限（防 JSON 膨胀）
    max_funcs_per_module: int = 40          # 每核心模块函数列表上限
    max_classes_per_module: int = 40        # 每核心模块类列表上限
    max_methods_per_class: int = 30         # 每类方法列表上限
    max_plugins_in_ai_context: int = 60     # AI 上下文插件表行数上限
    workers: int = 0                         # AST 并行进程数（2-A）：0=auto，1=串行
    follow_symlinks: bool = False
    target: str | None = None                # --module / --plugin 指定分析对象
    target_type: str | None = None           # 'module' | 'plugin' | None
    extra_repos: list = field(default_factory=list)   # --extra-repo 传入的额外仓库
    autodiscover_frontend: bool = True       # 自动发现同级的 verorun-workplace
    stable_out_dir: Path | None = None       # 产物按日期归档时的稳定根（缓存/基线落此处）
    max_file_lines: int = 2000               # 规则 CMP001：单文件代码行上限
    max_func_lines: int = 120                # 规则 CMP002：单函数体行数上限

    @property
    def cache_dir(self) -> Path:
        """AST 缓存目录所在的稳定根。

        产物按日期归档后 out_dir 每天变化，若缓存跟着走会导致每天首次分析
        都退化为全量解析（大仓库 30s+）。故缓存恒定落在稳定根。
        """
        return Path(self.stable_out_dir) if self.stable_out_dir else self.out_dir

    @property
    def snapshot_dir(self) -> Path:
        """快照/基线目录所在的稳定根：基线需跨日期可对比，不能随归档漂移。"""
        return Path(self.stable_out_dir) if self.stable_out_dir else self.out_dir

    @classmethod
    def autodetect_repo(cls, explicit: str | None, script_file: str) -> "RepoConfig":
        """定位仓库根目录：显式参数 > 脚本位置推断（<repo>/tools/dev_insight/）> CWD。

        档位 A：匹配特征来自 profile.repo_signature（默认 = VeroRun 现状：
        需同时存在 plugins/ 与 plugin_manager/；dirs+files 均为空则接受任意目录）。
        """
        sig = repo_signature()
        need_dirs, need_files = sig["dirs"], sig["files"]

        def _match(c: Path) -> bool:
            return (all((c / d).is_dir() for d in need_dirs)
                    and all((c / f).is_file() for f in need_files))

        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).resolve())
        here = Path(script_file).resolve().parent
        # 部署位置 <repo>/tools/dev_insight/ → 向上两级
        candidates.append(here.parent.parent)
        # 工作区开发位置（工具独立目录）→ 向上一级
        candidates.append(here.parent)
        for c in candidates:
            if _match(c):
                return cls(repo_root=c, out_dir=here / "out")
        # 兜底：CWD
        cwd = Path.cwd()
        if _match(cwd):
            return cls(repo_root=cwd, out_dir=here / "out")
        req: list[str] = [f"目录: {', '.join(need_dirs)}"] if need_dirs else []
        req += [f"文件: {', '.join(need_files)}"] if need_files else []
        raise SystemExit(
            "[repolucent] 未找到匹配的仓库根目录（要求"
            + ("；".join(req) if req else "显式 --repo 指向的任意目录")
            + "）。\n请用 --repo 参数显式指定，例如：\n"
            "  python repolucent.py --repo D:\\projects\\verorun-code"
        )

    def is_excluded_dir(self, name: str) -> bool:
        return name in self.exclude_dirs or name.startswith(".git")


# --------------------------------------------------- 工具级配置（P0 §4.1） ----
# 区别于任务级 RepoConfig：ToolConfig 跨任务共享，来自 settings.json / 环境变量，
# 承载 mcp/llm 开关、max_* 上限、models[] 等多模型元数据。
# 纯增量、向后兼容：无 settings.json 时所有字段回退到下方默认值（等价旧行为）。

@dataclass
class ToolConfig:
    """工具/部署级配置（跨任务共享），来自 settings.json / 环境变量。"""

    mcp_enabled: bool = True
    llm_enabled: bool = False
    default_model: str | None = None
    models: list[dict] = field(default_factory=list)   # P2 使用：[{name, provider, model, key_env, base_url_env}]
    max_plugins_in_ai_context: int = 60
    max_tree_depth: int = 2
    workers: int = 0            # AST 并行进程数（2-A）：0=auto，1=串行（兼容开关）

    @classmethod
    def from_settings(cls) -> "ToolConfig":
        s = load_settings()
        # analysis 段为 2-A 新增；缺失/类型不对时回退 auto，绝不因配置异常而中断。
        analysis = s.get("analysis")
        analysis = analysis if isinstance(analysis, dict) else {}
        try:
            workers = int(analysis.get("workers", 0))
        except (TypeError, ValueError):
            workers = 0
        return cls(
            mcp_enabled=bool(s.get("mcp_enabled", True)),
            llm_enabled=bool(s.get("llm_enabled", False) or s.get("models")),
            default_model=s.get("default_model"),
            models=list(s.get("models", [])),
            max_plugins_in_ai_context=int(s.get("max_plugins_in_ai_context", 60)),
            max_tree_depth=int(s.get("max_tree_depth", 2)),
            workers=workers,
        )
