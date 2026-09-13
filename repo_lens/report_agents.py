# -*- coding: utf-8 -*-
"""AGENTS.md 兼容产物（阶段一 1-A）：把仓库既有事实编译成 Agent 原生可读的约束文件。

依据与定位：
AGENTS.md 是 2026 年主流编码 Agent（OpenAI Codex / Cursor / Google Jules /
Claude Code / GitHub Copilot / Devin 等）原生读取的开放约定，已覆盖 6 万+ 开源项目。
本模块把 repo_lens.json 里**已有**的事实（overview / core / plugins / standards /
interactions）重排成该文件所需的「速览 + 契约 + 惯例 + 红线 + 命令 + 文档索引」。

设计决策（对应方案 1-A）：
1. **三态输出**：AGENTS.md 必须写在仓库根才会被 Agent 发现，而这会改变 git status。
   因此默认 `workspace`（写产物目录，绝不触碰仓库），`repo` 需显式选择，`off` 不生成。
2. **幂等**：`repo` 模式在 `<!-- ...:begin auto -->` / `<!-- ...:end auto -->` 标记块内
   原块替换，块外用户手写内容一字不动；内容不含时间戳/耗时/绝对路径，
   因此同一仓库连跑两次产物逐字节一致（可进 Git、可 review diff）。
3. **标记匹配与品牌解耦**：标记块的正则只依赖稳定的 `:begin auto` / `:end auto` 词形，
   不绑定 "repolens" 字样——v2.0.0 更名后，旧仓库里既有的标记块仍能被识别与更新。
4. **事实与判断分离**（约束 3）：本文件不产出"建议"。所谓「惯例」一律以实测统计
   呈现（如"路由前缀分布 Top N""插件最常导入的核心设施"），而非拟人化论断。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import ARTIFACT_AGENTS_MD, TOOL_VERSION

#: 标记块。BEGIN/END 文本随当前品牌生成；**匹配**用下方品牌无关正则。
BEGIN = "<!-- repolens:begin auto（本块由 RepoLens 生成，勿手改） -->"
END = "<!-- repolens:end auto -->"

_BEGIN_RE = re.compile(r"<!--\s*[\w.\-]+:begin auto[^>]*-->")
_END_RE = re.compile(r"<!--\s*[\w.\-]+:end auto[^>]*-->")

#: 三态模式
MODES = ("workspace", "repo", "off")
DEFAULT_MODE = "workspace"

#: 分档路由前缀列出的条数
_PREFIX_TOP = 5

_L = {
    "zh": {
        "title": "# AGENTS.md",
        "note": "> 本文件由 RepoLens 自动生成的标记块维护；块外内容为仓库自有约定，"
                "不随工具运行变化。",
        "overview": "项目速览",
        "contract": "组件契约",
        "manifest": "清单必填字段与枚举",
        "conventions": "既定惯例（实测统计）",
        "redlines": "架构红线",
        "commands": "命令速查",
        "docs": "文档索引",
        "none": "（无）",
    },
    "en": {
        "title": "# AGENTS.md",
        "note": "> Maintained by a RepoLens-generated marker block; everything outside "
                "the block is the repository's own convention and never rewritten.",
        "overview": "Project at a glance",
        "contract": "Component contract",
        "manifest": "Manifest required fields and enums",
        "conventions": "Established conventions (measured)",
        "redlines": "Architectural red lines",
        "commands": "Commands",
        "docs": "Documentation index",
        "none": "(none)",
    },
}


def _lang() -> str:
    """输出语言跟随 profile（默认中文）。profile 未配置时回落 zh。"""
    try:
        from .settings import profile_get
        v = str(profile_get("lang", "zh") or "zh").lower()
    except Exception:  # noqa: BLE001 —— 配置读取失败不应阻断产物生成
        v = "zh"
    return v if v in _L else "zh"


def _route_prefix_stats(plugins: dict) -> list[tuple[str, int]]:
    """从插件路由事实统计 url_prefix 分布（事实，不做规范判断）。"""
    counter: dict[str, int] = {}
    for p in plugins.get("items") or []:
        for r in (p.get("routes") or []):
            pref = (r.get("url_prefix") or "").strip() or "(无前缀)"
            counter[pref] = counter.get(pref, 0) + 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _gate_items() -> list[str]:
    """可用门禁项清单（取自 gate 模块，单一事实源）。"""
    try:
        from .gate import GATE_CHOICES
        return list(GATE_CHOICES)
    except Exception:  # noqa: BLE001
        return []


def _default_gates() -> list[str]:
    """profile 声明的默认门禁集（未声明则空列表 = 不输出该行）。"""
    try:
        from .gate import default_gates
        return list(default_gates() or [])
    except Exception:  # noqa: BLE001
        return []


def render_agents_block(data: dict, cfg=None) -> str:
    """渲染标记块正文。**不得**引入时间戳/耗时/绝对路径，否则幂等性被破坏。"""
    lang = _lang()
    t = _L[lang]
    ov = data.get("overview") or {}
    core = data.get("core") or {}
    plugins = data.get("plugins") or {}
    std = data.get("standards") or {}
    inter = data.get("interactions") or {}
    items = plugins.get("items") or []
    repo_name = (cfg.repo_root.name if cfg is not None
                 else Path(str((data.get("meta") or {}).get("repo_root") or ".")).name)

    L: list[str] = []
    w = L.append

    # ---- 1. 项目速览 ----
    w(f"## {t['overview']}")
    w("")
    w("| metric | value |")
    w("|---|---:|")
    w(f"| repository | `{repo_name}` |")
    w(f"| core_modules | {core.get('module_count', 0)} |")
    w(f"| plugins | {plugins.get('count', 0)} |")
    w(f"| routes | {sum(int(p.get('route_count') or 0) for p in items)} |")
    w(f"| files | {ov.get('total_files', 0)} |")
    w(f"| lines_code | {ov.get('total_code_lines', 0)} |")
    w(f"| schema_version | {((data.get('meta') or {}).get('schema_version')) or '—'} |")
    w("")
    if lang == "zh":
        w(t["note"])
        w("")

    # ---- 2. 组件契约（BasePlugin 必实现方法 + 发现规则）----
    ps = core.get("plugin_system") or {}
    bp = ps.get("base_plugin")
    disc = [ln for ln in (ps.get("discovery_rules") or []) if ln.strip()][:6]
    if bp or disc:
        w(f"## {t['contract']}")
        w("")
        if bp:
            w(f"`BasePlugin` (`{bp.get('file')}`)")
            w("")
            w("| method | required |")
            w("|---|---|")
            for m in (bp.get("methods") or []):
                req = "yes" if m.get("abstract") else "no"
                w(f"| `{m.get('signature', '')}` | {req} |")
            w("")
        if disc:
            if lang == "zh":
                w("发现规则（来自框架源码 docstring 原文）：")
            else:
                w("Discovery rules (verbatim from framework docstrings):")
            w("")
            for ln in disc:
                w(f"- {ln.strip()}")
            w("")

    # ---- 3. 清单必填字段与枚举 ----
    req = std.get("manifest_required") or []
    enums = std.get("manifest_enums") or {}
    if req or enums:
        w(f"## {t['manifest']}")
        w("")
        w(f"- file: `{std.get('manifest_schema_file') or '—'}`")
        w(f"- required: `{', '.join(req) if req else '—'}`")
        for k, v in sorted(enums.items()):
            w(f"- enum `{k}`: {' / '.join(map(str, v))}")
        w("")

    # ---- 4. 惯例（实测统计，非规范论断）----
    conv: list[str] = []
    stats = _route_prefix_stats(plugins)
    if stats:
        top = "、".join(f"`{p}`×{n}" for p, n in stats[:_PREFIX_TOP])
        if lang == "zh":
            conv.append(f"路由 url_prefix 分布 Top {min(_PREFIX_TOP, len(stats))}：{top}"
                        f"（合计 {sum(n for _, n in stats)} 条路由）")
        else:
            conv.append(f"route url_prefix distribution (top "
                        f"{min(_PREFIX_TOP, len(stats))}): {top} "
                        f"(total {sum(n for _, n in stats)} routes)")
    helpers = inter.get("plugins_use_base_helpers") or []
    if helpers:
        seg = "、".join(f"`{h.get('helper')}`×{h.get('import_count')}" for h in helpers[:8])
        conv.append(("插件共用的 `_base` 辅助库" if lang == "zh"
                     else "shared `_base` helpers") + f"：{seg}")
    core_dep = inter.get("plugins_import_core") or []
    if core_dep:
        seg = "、".join(f"`{c.get('module')}`×{c.get('import_count')}" for c in core_dep[:8])
        conv.append(("插件最常导入的核心设施" if lang == "zh"
                     else "most-imported core facilities") + f"：{seg}")
    tpl = (std.get("plugin_templates") or [])
    if tpl:
        seg = "、".join(f"`{x.get('dir')}`" for x in tpl[:5])
        conv.append(("官方插件模板" if lang == "zh" else "official plugin templates")
                    + f"：{seg}")
    if conv:
        w(f"## {t['conventions']}")
        w("")
        for c in conv:
            w(f"- {c}")
        w("")

    # ---- 5. 架构红线 ----
    bo = inter.get("boundary_observations") or {}
    rule = bo.get("rule")
    if rule:
        w(f"## {t['redlines']}")
        w("")
        w(f"- {rule}；")
        w(f"- checked_files: {bo.get('checked_files', 0)}；"
          f"observed_violations: {len(bo.get('violations') or [])}")
        if (bo.get("violations") or []) and lang == "zh":
            w("- 现有观察项（仅供评审，不等价于结论）：")
            for v in (bo.get("violations") or [])[:5]:
                w(f"    - `{v.get('file')}` → `{v.get('imports')}`")
        w("")

    # ---- 6. 命令速查 ----
    gate_items = _gate_items()
    dg = _default_gates()
    w(f"## {t['commands']}")
    w("")
    w("```bash")
    w("# 全量分析并落盘产物（json / md / html / ai 上下文 / 符号索引 / AGENTS.md）")
    w("repolens --repo <repo>")
    w("")
    w("# 只打印关键指标（stdout key=value，不落盘）")
    w("repolens --repo <repo> --summary-only")
    w("")
    if gate_items:
        w("# 架构门禁：命中即退出码 1（CI 可编程依赖）")
        w("repolens --repo <repo> --fail-on " + ",".join(dg or gate_items[:3]))
        w("")
    w("# 按需上下文切片（Agent 专用，不落盘）")
    w("repolens --repo <repo> context --for plugin:<identifier> --depth brief")
    w("")
    w("# 结构化查询与符号定位")
    w("repolens --repo <repo> query --select identifier,version --where agent_role=<role>")
    w("repolens --repo <repo> search <symbol>")
    w("```")
    w("")
    if gate_items:
        w(f"- 可用门禁项（`--fail-on`）：`{'`, `'.join(gate_items)}`")
        w("")

    # ---- 7. 文档索引 ----
    docs = std.get("key_docs") or []
    ps_std = std.get("plugin_standard")
    if docs or ps_std:
        w(f"## {t['docs']}")
        w("")
        if ps_std:
            w(f"- `{ps_std.get('file')}` —— {ps_std.get('title') or ''}"
              f"（{len(ps_std.get('sections') or [])} 章）")
        for d in docs:
            w(f"- `{d.get('file')}`：{d.get('title')}")
        w("")

    w("---")
    w(f"*block generated by RepoLens v{TOOL_VERSION}; "
      f"schema {(data.get('meta') or {}).get('schema_version') or '—'}*")
    return "\n".join(L)


def normalize_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODES else DEFAULT_MODE


def target_path(cfg, mode: str) -> Path | None:
    """按模式给出目标路径；off 返回 None。"""
    m = normalize_mode(mode)
    if m == "off":
        return None
    if m == "repo":
        return Path(cfg.repo_root) / ARTIFACT_AGENTS_MD
    return Path(cfg.out_dir) / ARTIFACT_AGENTS_MD


def emit_agents_md(cfg, data: dict, mode: str = DEFAULT_MODE) -> Path | None:
    """生成 AGENTS.md，返回写入路径；mode=off 或 cfg 缺失时返回 None。

    - workspace：整文件生成到产物目录，每次覆盖（不触碰仓库，零意外）。
    - repo：写入仓库根。已存在标记块 → 块内替换（保留用户内容）；
      无标记块 → 追加到文末；文件不存在 → 新建。
    """
    if cfg is None:
        return None
    m = normalize_mode(mode)
    if m == "off":
        return None

    # 块的尾部**不带**换行：标记块在既有文件中被替换时，紧随 END 的换行属于
    # 「块外文本」，由 tail 原样带回。若块自带尾换行，替换后会与残留换行叠加，
    # 每次运行多出一个空行——这正是幂等性（跑两次逐字节一致）被破坏的唯一入口。
    block = BEGIN + "\n" + render_agents_block(data, cfg) + "\n" + END
    p = target_path(cfg, m)
    if p is None:
        return None

    if m == "workspace":
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_L[_lang()]["title"] + "\n\n" + block + "\n",
                     encoding="utf-8", newline="\n")
        return p

    # ---- repo 模式：幂等合并 ----
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
        mb, me = _BEGIN_RE.search(text), _END_RE.search(text)
        if mb and me and me.start() > mb.end():
            text = text[:mb.start()] + block + text[me.end():]
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
    else:
        text = _L[_lang()]["title"] + "\n\n" + block + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p
