# -*- coding: utf-8 -*-
"""报告文案与章节开关的单一来源。

改造动机：改造前 report_ai / report_md / report_html 三份报告器**各自独立**
硬编码同一批「插件契约 / 交互姿势 / 架构红线 / 开发流程」文案。三个后果：
1) 三处副本必然漂移；2) 任何仓库（哪怕根本没有组件体系）的报告都会带上这些
只对特定项目成立的"规范"，且语气是"必须"——极易被误读为该仓库的架构约束；
3) 改一句文案要改三处。

现在：章节开关与文案全部来自 profile.report_sections；本模块只保留**中性兜底**
（GENERIC），不含任何具体项目的口径。profile 未声明对应键时用 GENERIC；
`section_enabled()` 未声明时回落到"是否声明了组件体系"。

占位符：文案里用 `{name}` 形式的具名占位符，由 fmt / sub / fmt_lines 替换；
未提供对应 kw 的占位符原样保留（因此 `i18n/{locale}.yml` 这类字面量不会被误替换）。
"""
from __future__ import annotations

import re

from . import config

#: 具名占位符：仅匹配 {小写标识符}，`<identifier>` 之类的尖括号写法不受影响。
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: 中性兜底文案（非组件式仓库，或 profile 未声明对应键时使用）。
GENERIC: dict = {
    # ---- 组件契约 ----
    "contract_base_note": "所有组件必须继承 `{base_class}`（{base_file}）：",
    "contract_runtime_note": "运行时引用由组件框架注入。",
    "md_contract_heading": "### 3.2 组件开发契约：{base_class}（{base_file}）",
    "md_contract_base_note": "所有组件必须继承 `{fqn}`：",
    "md_contract_runtime_note": "运行时引用由组件框架注入。",
    "md_manager_heading": "### 3.3 {manager_label} 关键 API（{manager_file}）",
    "md_discovery_heading": "### 3.4 组件发现规则（{discovery_source}）",
    "html_contract_heading": "<h2>组件开发契约 {base_class}</h2>",
    "html_contract_note": "所有组件必须继承 {fqn}；运行时引用由组件框架注入。",
    "manager_label": "Manager",
    "manager_class_fallback": "Manager",
    "no_contract_note": "本仓库未声明组件体系，该节内容不适用。",
    # ---- 发现规则 ----
    "ai_discovery_header": "发现规则（{discovery_source}）：",
    "discovery_rule": ("组件位于 `{component_dir}/<identifier>/`；必须含 `__init__.py`；"
                       "必须含合法 `{manifest}`。"),
    # ---- 清单字段 ----
    "ai_manifest_heading": "## 3. {manifest} 必填字段",
    "md_manifest_heading": "**{manifest} 必填字段**（来源：{manifest_source}）：",
    "html_manifest_heading": "<b>{manifest} 必填字段</b>",
    "manifest_source_fallback": "内置字段清单",
    # ---- 章节正文 ----
    "conventions": [],
    "redlines": [
        "新增文件必须遵循项目既有的目录结构与技术栈；",
        "操作前先方案后执行；对比/分析类指令只输出报告。",
    ],
    "workflow": [],
    "html_workflow": [],
    "no_component_note": "本仓库未声明组件/插件体系，本节省略。",
    # ---- AGENTS.md 命令速查示例（示例命令不得暗示特定技术栈的字段名）----
    "agents_contract_base": "`{base_class}` (`{base_file}`)",
    "agents_context_example":
        "repolucent --repo <repo> context --for <type>:<name> --depth brief",
    "agents_query_example":
        "repolucent --repo <repo> query --select <field> --where <field>=<value>",
}


def _rs() -> dict:
    return config.REPORT_SECTIONS or {}


def _ps() -> dict:
    return config.PLUGIN_SYSTEM or {}


def section_enabled(key: str, default: bool | None = None) -> bool:
    """章节开关：profile 显式声明优先；未声明时回落到"组件体系是否启用"。"""
    v = _rs().get(key)
    if v is None:
        return config.plugin_system_enabled() if default is None else bool(default)
    return bool(v)


def text(key: str, default=None):
    """取具名文案：profile.report_sections.<key> 优先，否则用中性兜底。"""
    v = _rs().get(key)
    if v is not None:
        return v
    return GENERIC.get(key) if default is None else default


def lines(key: str) -> list[str]:
    """取文案行列表（conventions / redlines / workflow 等）。"""
    v = text(key)
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return []


def sub(s, **kw) -> str:
    """对任意字符串做具名占位符替换；未提供的占位符原样保留。"""
    s = "" if s is None else str(s)
    return _PLACEHOLDER.sub(
        lambda m: str(kw[m.group(1)]) if m.group(1) in kw else m.group(0), s)


def fmt(key: str, **kw) -> str:
    """取具名文案并替换 `{name}` 占位符。"""
    return sub(text(key), **kw)


def fmt_lines(key: str, **kw) -> list[str]:
    """取文案行列表并逐行替换占位符。"""
    return [sub(x, **kw) for x in lines(key)]


def plugin_ctx(**extra) -> dict:
    """组件体系相关的具名占位符取值（值取自 profile.plugin_system）。

    刻意**不含** base_file / fqn / manager_file —— 这些依赖运行时分析结果
    （base_plugin["file"]、manager_api["file"]），由调用点显式传入，避免猜测。
    """
    ps = _ps()
    files = ps.get("files") or {}
    core_dir = str(ps.get("core_dir") or "")
    disc_py = str(files.get("discovery") or "discovery.py")
    ctx = {
        "component_dir": str(config.PLUGINS_DIR or ""),
        "manifest": str(config.MANIFEST_NAME or ""),
        "base_class": str(ps.get("base_class") or ""),
        "manager_label": str(_rs().get("manager_label")
                             or GENERIC["manager_label"]),
        "discovery_source": f"{core_dir}/{disc_py}" if core_dir else disc_py,
    }
    ctx.update(extra)
    return ctx


def fqn(rel_file: str, base_class: str) -> str:
    """由 `pkg/base.py` + `Base` 得到 `pkg.base.Base`（Markdown/HTML 里的全限定名）。"""
    rel = str(rel_file or "").replace("\\", "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    return ".".join(x for x in (rel.replace("/", "."), str(base_class or "")) if x)
