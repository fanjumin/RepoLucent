# -*- coding: utf-8 -*-
"""命令行入口：编排各分析器并落盘输出。

用法：
    python repolens.py                          # 自动定位仓库（脚本部署在 <repo>/tools/dev_insight/ 时）
    python repolens.py --repo D:\\projects\\verorun-code
    python repolens.py --out D:\\tmp\\repolens_out  # 自定义输出目录
    python repolens.py --only md,json           # 只生成部分输出（json/md/html/ai/agents/symbols）
    python repolens.py --agents-md repo         # 额外把 AGENTS.md 写入仓库根（默认只写产物目录）
    python repolens.py snapshot --save baseline  # 生成基线快照（out/history/baseline.json）
    python repolens.py diff baseline             # 与基线对比，输出变更报告
    python repolens.py diff baseline --format json --top 20
    python repolens.py search parse_python_file  # 符号定位（file:line / kind / owner）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import os
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import (TOOL_VERSION, SCHEMA_VERSION, ARTIFACT_JSON, ARTIFACT_MD,
               ARTIFACT_HTML, ARTIFACT_AI_CONTEXT, ARTIFACT_SYMBOLS)
from .config import RepoConfig, ToolConfig, agents_md_setting
from .fs_scan import scan_overview, render_tree, iter_repo_files
from .py_ast import parse_files_parallel, parse_python_file, resolve_workers
from .cache import (load as cache_load, load_hashes as cache_load_hashes,
                    resolve as cache_resolve, save as cache_save,
                    signature as file_signature)
from .gate import evaluate as gate_evaluate, GATE_CHOICES, DEFAULT_MAX_FILE_LINES
from .gate import default_gates as gate_default_gates
from .rules import run_rules
from .core_analyzer import analyze_core
from .plugin_analyzer import analyze_plugins
from .interaction_analyzer import analyze_interactions
from .standards_extractor import extract_standards
from .deep_analyzer import analyze_target
from .frontend_analyzer import discover_frontend_repos, analyze_frontend
from .hotspot_analyzer import analyze_hotspots, render_mermaid
from .snapshot import (build_snapshot, save_snapshot, load_snapshot, list_baselines,
                       diff_snapshots, render_diff_md, default_name)
from .report_md import render_md
from .report_html import render_html
from .report_ai import render_ai_context, render_target_context
from .symbol_index import (build_symbol_index, search_symbols, index_from_file,
                           summary as symbol_summary, write_symbol_index,
                           render_table as render_symbol_table)
from .report_agents import emit_agents_md
from .query import (run_query, render_table as render_query_table, parse_where,
                    infer_scope, SCOPES, QueryError, parse_query_path)
from .git_log_analyzer import scan_commits, format_commit_table
from .remote_diff import analyze_remote_diff, format_remote_diff_table
from .untracked_scanner import scan_untracked, format_untracked_table
from .packs.gitflow.repo_group import (load_groups, add_repo_to_group,
                                       remove_repo_from_group, list_groups,
                                       format_group_table)
from .packs.gitflow.git_push import smart_push
from .packs.gitflow.git_pull import smart_pull
from .packs.gitflow.multi_remote_sync import sync_repos, format_sync_results
from .ci_analyzer import analyze_ci_config, format_ci_findings
from .script_cmd import _cmd_script

#: pack 分层（v1.8.0 3-A）：pack 贡献的 CLI 子命令处理器，由 _build_argparser 填充。
from . import packs
#: SQLite 加速层（v1.8.0 3-B）：query 在库新鲜时跳过全量分析。
from . import index_db


def _render_deep_md(dd: dict) -> str:
    """针对单目标生成聚焦深挖 Markdown。"""
    t = "插件" if dd["target_type"] == "plugin" else "核心模块"
    L: list[str] = [f"# 深度分析：{t} `{dd['name']}`\n"]
    w = L.append
    meta = dd.get("manifest")
    if meta:
        w(f"**identifier** {meta.get('identifier')} · **版本** {meta.get('version')} · "
          f"**角色** {meta.get('agent_role')} · **分类** {meta.get('category')}\n")
        w(f"**描述**：{meta.get('description')}\n")
        if meta.get('manifest_errors'):
            w(f"**manifest 问题**：{'；'.join(meta['manifest_errors'])}\n")
    loc = dd.get("locations", {})
    w("\n## 规模\n")
    w(f"- 文件 {dd['file_count']} 个 · 总行 {loc.get('loc_total')} · "
      f"代码行 {loc.get('loc_code')} · 路由 {dd['routes_count']} 条\n")
    w("\n## 文件清单\n\n| 文件 | 行数 | 代码行 | 类 | 函数 | 路由 |\n|---|---|---:|---:|---:|---:|")
    for f in dd["files"]:
        w(f"| `{f['file']}` | {f['loc']} | {f['loc_code']} | {f['classes']} | "
          f"{f['functions']} | {f['routes']} |")
    if dd["classes"]:
        w("\n## 关键类\n")
        for c in dd["classes"][:15]:
            w(f"- `{c['name']}` ({', '.join(c['bases'])}) — {c['docstring'] or '—'}")
    if dd["functions"]:
        w("\n## 关键函数\n")
        for fn in dd["functions"][:20]:
            w(f"- `{fn['name']}` {fn.get('signature', '')} — {fn['docstring'] or '—'}")
    if dd["routes"]:
        w("\n## 路由明细（%d 条）\n\n| 端点 | 前缀 | 路径 | Method | 文件 |\n|---|---|---|---|---|" % dd["routes_count"])
        for r in dd["routes"]:
            w(f"| `{r['endpoint']}` | {r.get('url_prefix','')} | `{r['rule']}` | "
              f"{','.join(r['methods'])} | {r['file']} |")
    if dd.get("imports_aggregated"):
        w("\n## 导入聚合\n")
        for k, v in dd["imports_aggregated"].items():
            w(f"- `{k}` × {v}")
    if dd.get("depended_by_plugins"):
        w("\n## 依赖它的插件\n")
        for dep in dd["depended_by_plugins"]:
            w(f"- `{dep['plugin']}`（{dep['refs']} 处）")
    return "\n".join(L) + "\n"


def _build_meta(cfg: RepoConfig, tree: str, duration_ms: int,
                deterministic: bool) -> dict:
    """构造 meta 段。

    deterministic=True 时剥离易变字段（时间戳 / 耗时 / 绝对路径），
    使同一仓库的两次运行产出逐字节相同的 JSON，从而可 diff、可进 Git。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "repolens",
        "tool_version": TOOL_VERSION,
        "generated_at": None if deterministic
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "repo_root": cfg.repo_root.name if deterministic else str(cfg.repo_root),
        "tree_depth": cfg.max_tree_depth,
        "tree": tree,
        "duration_ms": None if deterministic else duration_ms,
    }


def _summary_pairs(data: dict, duration_ms: int) -> list[tuple]:
    """摘要键值对（机器友好）；文本版/HTTP 版共用，保证口径唯一。

    v1.4 起追加 frontend 三行（schema 1.1，MINOR：新增可选字段）。
    """
    ov, core, plugins = data["overview"], data["core"], data["plugins"]
    inter = data["interactions"]
    fe = data.get("frontend") or []
    pairs = [
        ("repo", Path(data["meta"]["repo_root"]).name),
        ("schema_version", SCHEMA_VERSION),
        ("core_modules", core["module_count"]),
        ("plugins", plugins["count"]),
        ("plugins_manifest_invalid",
         sum(1 for p in plugins["items"] if not p["manifest_valid"])),
        ("routes_total", sum(p["route_count"] for p in plugins["items"])),
        ("boundary_violations",
         len(inter["boundary_observations"]["violations"])),
        ("files", ov["total_files"]),
        ("lines_total", ov["total_lines"]),
        ("lines_code", ov["total_code_lines"]),
        ("frontend_repos", len(fe)),
        ("frontend_files", sum(f["overview"]["total_files"] for f in fe)),
        ("frontend_code", sum(f["overview"]["total_code_lines"] for f in fe)),
        ("duration_ms", duration_ms),
    ]
    return pairs


def _render_summary(data: dict, duration_ms: int) -> str:
    """面向 Agent / 终端的极简摘要：固定 key=value 行，不落盘任何文件。

    摘要属 stdout 输出而非落盘产物，duration_ms 始终取真实值
    （meta 中的同名字段在 --deterministic 下为 None）。
    """
    return "\n".join(f"{k}={v}" for k, v in _summary_pairs(data, duration_ms))


class _PackAwareParser(argparse.ArgumentParser):
    """子命令缺失时，若它属于「存在但未启用」的 pack，给出可操作的启用指引。

    只覆盖 argparse 默认的 invalid choice 文案，其余错误原样交给父类
    （对既有 CLI 行为零影响）。add_subparsers 默认继承 type(self)，故子解析器
    同样具备该行为。
    """

    def error(self, message: str) -> None:          # pragma: no cover - 由 CLI 触发
        m = re.search(r"invalid choice: '([^']+)'", message)
        if m:
            name = m.group(1)
            pack = packs.disabled_command_hints().get(name)
            if pack and pack not in packs.enabled_packs():
                self.exit(2, _pack_disabled_text(name, pack))
        super().error(message)


def _pack_disabled_text(cmd: str, pack: str) -> str:
    """pack 未启用时的启用指引（写 stderr；退出码 2 与参数错误一致）。"""
    cur = ", ".join(packs.enabled_packs()) or "（空 = 纯只读内核）"
    avail = ", ".join(packs.available_packs()) or "（无）"
    return (f"[repolens] 错误：命令 `{cmd}` 属于 pack `{pack}`，当前未启用。\n"
            f"  当前生效 pack：{cur} ｜ 可用 pack：{avail}\n"
            f"  启用方法：在 settings.json 中设置 "
            f"\"packs\": {{\"enabled\": [\"{pack}\"]}}（或用 REPO_LENS_SETTINGS "
            f"指向的配置文件）；显式 \"enabled\": [] 表示纯只读内核。\n")


def _build_argparser() -> argparse.ArgumentParser:
    ap = _PackAwareParser(
        prog="repolens.py",
        description="RepoLens —— 通用 Python 仓库架构洞察工具（内置 verorun profile 提供 VeroRun 增强口径），"
                    "输出结构化架构信息（本地开发辅助工具，不入 Git）。",
    )
    ap.add_argument("--repo", help="VeroRun 仓库根目录（默认自动定位）")
    ap.add_argument("--out", help="输出目录（默认 <脚本目录>/out）")
    ap.add_argument("--only", default="json,md,html,ai,agents,symbols",
                    help="逗号分隔的输出类型：json,md,html,ai,agents,symbols（默认全部）")
    ap.add_argument("--agents-md", choices=("repo", "workspace", "off"), default=None,
                    help="AGENTS.md 输出模式：workspace=写产物目录（默认，绝不触碰仓库"
                         "根）/ repo=写仓库根（会改变 git status，建议 commit 进仓库共享）"
                         "/ off=不生成")
    ap.add_argument("--tree-depth", type=int, default=2, help="目录树深度（默认 2）")
    ap.add_argument("--module", help="指定单核心模块深度分析（如 plugin_manager / orchestrator）")
    ap.add_argument("--plugin", help="指定单插件深度分析（如 shop / stock_analysis）")
    ap.add_argument("--extra-repo", action="append", default=[], metavar="PATH",
                    help="额外纳入分析的仓库（可重复；默认自动发现同级 verorun-workplace）")
    ap.add_argument("--quiet", action="store_true", help="只输出关键信息")
    ap.add_argument("--deterministic", action="store_true",
                    help="确定性输出：剥离时间戳/耗时/绝对路径，使产物可 diff、可进 Git")
    ap.add_argument("--summary-only", action="store_true",
                    help="只在 stdout 打印关键指标摘要（11 行 key=value），不落盘任何文件")
    ap.add_argument("--no-cache", action="store_true",
                    help="禁用 AST 缓存：本次运行不读不写缓存（缓存位于 out/.insight_cache/）")
    ap.add_argument("--workers", type=int, default=None, metavar="N",
                    help="AST 并行进程数（阶段二 2-A）：0=auto（min(CPU,4)），1=串行；"
                         "缺省取 settings.analysis.workers（默认 0）。"
                         "文件数 <200 时始终串行，故小仓库不受影响")
    ap.add_argument("--no-date-dir", action="store_true",
                    help="关闭产物按日期归档，本次运行的产物直接写入 --out 目录")
    ap.add_argument("--fail-on", metavar="ITEMS",
                    help="门禁检查项，逗号分隔；命中则返回退出码 1。可选："
                         + ", ".join(GATE_CHOICES))
    ap.add_argument("--fail-on-warn", action="store_true",
                    help="只打印门禁结果，不返回非零退出码（灰度观察用）")
    ap.add_argument("--max-file-lines", type=int, default=DEFAULT_MAX_FILE_LINES,
                    help=f"file-too-large 检查项阈值，单位代码行"
                         f"（默认 {DEFAULT_MAX_FILE_LINES}）")

    # 子命令：不加 required=True，保持「无子命令即全量分析」的历史行为完全不变。
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    sp = sub.add_parser("serve", help="启动本地可视化控制台（浏览器 UI + Agent HTTP API）")
    _add_shared_args(sp)
    sp.add_argument("--port", type=int, default=8788, help="监听端口（默认 8788，仅绑定 127.0.0.1）")
    sp.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")

    rp = sub.add_parser("repos", help="多仓库注册表：注册本地仓库并绑定分析预设")
    rsub = rp.add_subparsers(dest="repos_cmd")
    rsub.add_parser("list", help="列出已注册仓库")
    ra = rsub.add_parser("add", help="注册仓库")
    ra.add_argument("--name", required=True, metavar="NAME", help="仓库别名（唯一）")
    ra.add_argument("--path", required=True, metavar="PATH", help="仓库根目录")
    ra.add_argument("--profile", default=None, metavar="NAME",
                    help="分析预设（verorun=内置 / generic-python 等 profiles/*.json）")
    rr = rsub.add_parser("remove", help="移除注册仓库")
    rr.add_argument("--name", required=True, metavar="NAME")
    rr.add_argument("--confirm", action="store_true", help="确认移除（缺省仅预览）")

    sp = sub.add_parser("snapshot", help="生成基线快照（供 diff 对比，存 out/history/）")
    _add_shared_args(sp)
    sp.add_argument("--save", metavar="NAME",
                    help="快照名称，存为 <out>/history/<NAME>.json（默认当天日期）")
    sp.add_argument("--stdout", action="store_true", help="打印到 stdout 而不落盘")

    dp = sub.add_parser("diff", help="与基线快照对比，输出变更报告")
    _add_shared_args(dp)
    dp.add_argument("baseline", help="基线名称（history/<NAME>.json）或快照文件路径")
    dp.add_argument("--top", type=int, default=10, help="LOC 变动表显示条数（默认 10）")
    dp.add_argument("--format", choices=("md", "json"), default="md",
                    help="输出格式：md 人看 / json 供 Agent 解析（默认 md）")

    cp = sub.add_parser("context", help="输出面向 Agent 的上下文切片（stdout，不落盘）")
    _add_shared_args(cp)
    cp.add_argument("--for", dest="for_", metavar="TARGET",
                    help="目标：plugin:<identifier> 或 module:<name>"
                         "（省略则输出全局 AI 上下文）")
    cp.add_argument("--depth", choices=("brief", "normal", "full"), default="brief",
                    help="颗粒度：brief≈2-4KB / normal 加类与文件清单 / full 加全部签名"
                         "（默认 brief）")

    qp = sub.add_parser("query", help="受限查询结构化结果（替代手工 grep JSON）")
    _add_shared_args(qp)
    qp.add_argument("--from", dest="scope", choices=tuple(SCOPES), metavar="SCOPE",
                    default=argparse.SUPPRESS,
                    help="查询范围（默认从 --select 首个字段推断，否则 plugins）")
    qp.add_argument("--select", metavar="FIELDS",
                    help="逗号分隔字段路径，支持 a.b / routes[].rule / routes[0].rule")
    qp.add_argument("--where", metavar="COND",
                    help="过滤：key=value 或 key!=value，逗号分隔取 AND")
    qp.add_argument("--format", choices=("json", "table"), default="json",
                    help="输出格式（默认 json）")
    qp.add_argument("--rebuild-index", action="store_true",
                    help="强制走全量分析并重建 SQLite 索引库（默认：库新鲜时直接查库，"
                         "跳过全量分析）")

    srp = sub.add_parser("search", help="符号定位：类/函数/路由 → file:line（只读，替代全仓 grep）")
    _add_shared_args(srp)
    srp.add_argument("symbol", help="符号名；精确命中优先，未命中时按子串（大小写不敏感）回退")
    srp.add_argument("--limit", type=int, default=50, help="结果条数上限（默认 50）")
    srp.add_argument("--format", choices=("json", "table"), default="json",
                     help="输出格式（默认 json）")

    # ---- Git 工作流扩展子命令（阶段 G）----
    lp = sub.add_parser("log", help="扫描提交历史（按作者/时间过滤）")
    _add_shared_args(lp)
    lp.add_argument("--since", type=int, default=90, help="扫描最近多少天（默认 90）")
    lp.add_argument("--author", metavar="NAME", help="只扫描指定作者的提交")
    lp.add_argument("--max-commits", type=int, default=None, help="限制返回 commit 数量")
    lp.add_argument("--format", choices=("json", "md", "table"), default="table",
                    help="输出格式（默认 table）")

    rp = sub.add_parser("remote-diff", help="分析本地与远程仓库差异")
    _add_shared_args(rp)
    rp.add_argument("--remote", default="origin", help="remote 名称（默认 origin）")
    rp.add_argument("--branch", default=None, help="分支名（默认当前分支）")
    rp.add_argument("--format", choices=("json", "md"), default="md",
                    help="输出格式（默认 md）")

    up = sub.add_parser("untracked", help="检测未跟踪文件并分类")
    _add_shared_args(up)
    up.add_argument("--classify", action="store_true", default=True,
                    help="对文件进行分类（默认开启）")
    up.add_argument("--no-classify", action="store_false", dest="classify",
                    help="不分类，只显示列表")
    up.add_argument("--suggest-ignore", action="store_true", default=True,
                    help="生成 .gitignore 建议（默认开启）")
    up.add_argument("--no-suggest-ignore", action="store_false", dest="suggest_ignore",
                    help="不生成 .gitignore 建议")
    up.add_argument("--format", choices=("json", "md", "table"), default="table",
                    help="输出格式（默认 table）")

    # ---- pack 贡献的子命令（v1.8.0 3-A：写能力分层剥离）----
    # 未启用的 pack 不注册其命令，run() 会对其给出「pack disabled」的启用提示。
    # 注册顺序仍在原位（group/batch/push/pull/sync），故 --help 输出与 v1.7.0 一致。
    _PACK_HANDLERS.clear()
    _PACK_HANDLERS.update(packs.register_cli(sub, {"cli": sys.modules[__name__]}))

    cp = sub.add_parser("ci-check", help="分析 CI/CD 配置合规性")
    _add_shared_args(cp)
    cp.add_argument("--platform", choices=("github", "gitee", "auto"),
                    default="auto", help="CI 平台（默认自动检测）")
    cp.add_argument("--format", choices=("json", "md"), default="md",
                    help="输出格式（默认 md）")

    # 脚本资产库子命令（AI 生成脚本的编目/调用/自检）——加法式注册，不改既有分析器
    scp = sub.add_parser("script", help="脚本资产库：编目/调用/自检 AI 生成脚本")
    scp.add_argument("action", choices=("list", "info", "run", "doctor",
                                        "sweep", "snapshot", "diff", "manual"),
                     help="list 列表 / info 详情 / run 调用 / doctor 自检 / "
                          "sweep 普查候选 / snapshot 打库基线 / diff 对比基线 / manual 生成使用手册")
    scp.add_argument("rest", nargs=argparse.REMAINDER,
                     help="动作参数：list --category/--status/--json；info|doctor <id>；"
                          "run <id> [脚本参数...]；sweep <目录>；snapshot [名称] [--out]；"
                          "diff <基线> [--out]；manual [--out 文件]")

    # 代码审计子命令（统一 gate+ci+源码扫描，产放行裁决）——加法式注册
    ap_audit = sub.add_parser("audit", help="代码审计：多维规则→统一 Finding→放行裁决+报告")
    _add_shared_args(ap_audit)
    ap_audit.add_argument("--dims", default=None,
                          help="审计维度 CSV：security,architecture,quality,ai_business"
                               "（默认 security,architecture,quality；ai_business 需 --with-semantic）")
    ap_audit.add_argument("--fail-on-severity", default="blocking",
                          choices=("blocking", "major", "minor", "info"),
                          help="达到该级别即判不放行（默认 blocking）")
    ap_audit.add_argument("--format", default="md,json",
                          help="产物格式 CSV：md,json,annot（写入 out/audit/；annot 打到 stdout 供 CI）")
    ap_audit.add_argument("--with-semantic", action="store_true",
                          help="标注语义通道为『已启用但未回灌』（配合 --semantic-results 实际注入）")
    ap_audit.add_argument("--emit-semantic", default=None, metavar="FILE",
                          help="导出语义审计任务书+AI上下文到该文件，交外部 AI Agent 判定（本工具不接 LLM）")
    ap_audit.add_argument("--semantic-results", default=None, metavar="FILE",
                          help="回灌 Agent 产出的语义结果 JSON（coverage=semantic 咨询性发现）")
    ap_audit.add_argument("--semantic-can-block", action="store_true",
                          help="允许语义发现纳入阻断判定（默认仅咨询、不单独影响放行）")
    ap_audit.add_argument("--audit-rules", default=None, metavar="FILE",
                          help="追加的外置审计规则 JSON（随仓库定制；内置默认仍生效）")
    ap_audit.add_argument("--save-baseline", default=None, metavar="NAME",
                          help="把本次审计结论存为基线（out/history/audit/NAME.json）")
    ap_audit.add_argument("--diff-baseline", default=None, metavar="NAME",
                          help="与指定基线对比审计结论（stdout 输出差异）")
    # P2 需求2：内置 LLM 通道（可选叠加，默认关闭；不可用时自动降级，不影响审计主流程）
    ap_audit.add_argument("--with-llm", action="store_true",
                          help="启用内置 LLM 语义通道（settings.models[] 配置；"
                               "密钥走环境变量）。不可用则自动降级并打印原因")
    ap_audit.add_argument("--llm-model", default=None, metavar="NAME",
                          help="指定 settings.models[] 中的模型名（默认取 default_model）")
    ap_audit.add_argument("--llm-no-tools", action="store_true",
                          help="禁用 LLM 工具调用回环（仅纯文本判定，不启动 MCP 子进程）")

    # MCP 服务化子命令（需求3 主体）：以 stdio JSON-RPC server 运行，供外部 Agent 调用
    mp = sub.add_parser("mcp", help="以 stdio MCP server 运行，供千问办公/WorkBuddy 等 Agent 直接调用")
    _add_shared_args(mp)
    mp.add_argument("--no-browser", action="store_true", help="（占位）stdio 模式不启动浏览器")

    # outbound MCP 客户端（审计 EXT-4）：本工具**作为客户端**去消费外部 MCP Server
    op = sub.add_parser("mcp-out", help="outbound MCP 客户端：发现并调用外部 MCP Server 暴露的工具")
    op.add_argument("action", choices=("servers", "tools", "call"),
                    help="servers 列出已配置 server；tools 发现外部工具；call 调用某个外部工具")
    op.add_argument("rest", nargs=argparse.REMAINDER,
                    help="servers（无参）；tools [--server NAME] [--json]；"
                         "call <server> <tool> [--arg k=v ...] [--confirm] [--json]")

    return ap


def _add_shared_args(ap: argparse.ArgumentParser) -> None:
    """子命令复用的公共参数。

    默认值一律为 argparse.SUPPRESS：子命令未显式给出时不写入 namespace，
    从而不会用 None 覆盖主解析器已解析出的同名参数（argparse 的经典陷阱）。
    """
    ap.add_argument("--repo", default=argparse.SUPPRESS, help="仓库根目录")
    ap.add_argument("--repo-name", default=argparse.SUPPRESS, metavar="NAME",
                    help="使用多仓库注册表（repolens repos add 注册）中的仓库；"
                         "其 profile 预设同时生效（--repo 显式给出时以 --repo 路径为准）")
    ap.add_argument("--out", default=argparse.SUPPRESS, help="输出目录")
    ap.add_argument("--tree-depth", type=int, default=argparse.SUPPRESS, help="目录树深度")
    ap.add_argument("--extra-repo", action="append", default=[], metavar="PATH",
                    help="额外纳入分析的仓库（可重复）")
    ap.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                    help="只输出关键信息")
    ap.add_argument("--deterministic", action="store_true", default=argparse.SUPPRESS,
                    help="确定性输出（快照恒为确定性，此开关仅影响过程输出）")
    ap.add_argument("--no-cache", action="store_true", default=argparse.SUPPRESS,
                    help="禁用 AST 缓存")
    ap.add_argument("--no-date-dir", action="store_true", default=argparse.SUPPRESS,
                    help="关闭产物按日期归档（产物直接写入 --out 目录）")


def _run_gates(args, data: dict, cfg, stream) -> int:
    """评估门禁、打印结果并返回退出码（0 通过 / 1 门禁失败 / 2 门禁项非法）。

    `--summary-only` 时结果写入 stderr，以保证 stdout 仍是纯净的 11 行摘要。
    """
    if not args.fail_on:
        # 档位 A：--fail-on 未给出时尝试 profile.default_gates；
        # 未配置（default_gates() 返回 []）则维持历史行为——不跑门禁直接通过。
        if not gate_default_gates():
            return 0
        args.fail_on = ",".join(gate_default_gates())
    try:
        results = gate_evaluate(args.fail_on, data, cfg, args.max_file_lines)
    except ValueError as e:
        print(f"[repolens] 错误：{e}", file=sys.stderr)
        return 2
    failed = [r for r in results if not r.passed]
    for r in results:
        print(f"[gate] {'PASS' if r.passed else 'FAIL'} {r.name} "
              f"({len(r.hits)} hits)", file=stream)
        if not r.passed:
            for h in r.hits[:5]:
                loc = h.get("plugin") or h.get("file") or "?"
                print(f"       - {loc}: {h['detail']}", file=stream)
            if len(r.hits) > 5:
                print(f"       … 另有 {len(r.hits) - 5} 条", file=stream)
    return 1 if (failed and not args.fail_on_warn) else 0


def _setup(args, with_target: bool = False) -> RepoConfig:
    """解析仓库定位与输出目录。子命令缺少的参数一律走 getattr 兜底。"""
    # 多仓库注册表：--repo-name 解析出路径与按仓 profile（必须在 apply_profile 之前生效）。
    rn = getattr(args, "repo_name", None)
    if rn:
        from .repo_registry import resolve
        ent = resolve(rn)
        if ent is None:
            raise SystemExit(f"[repolens] 注册表中无此仓库：{rn}（先 repolens repos add 注册）")
        if not getattr(args, "repo", None):
            args.repo = ent["path"]
        from .settings import set_profile_override
        set_profile_override(ent.get("profile") or None)

    # 档位 A 通用化：把 settings.profile 的分析口径覆盖到 config 模块级常量。
    # 必须最先调用——早于 autodetect_repo（用 repo_signature）与任何 analyzer 使用。
    from .config import apply_profile
    apply_profile()
    script_file = Path(__file__).resolve().parent.parent / "repolens.py"
    cfg = RepoConfig.autodetect_repo(getattr(args, "repo", None), str(script_file))
    # P0 §4.1 配置外部化：max_* 默认值来自 ToolConfig（可被 settings.json 覆盖），
    # CLI args 仍最高优先（向后兼容：无 settings 时退化为原有内置默认 2 / 60）。
    tc = ToolConfig.from_settings()
    cfg.max_tree_depth = getattr(args, "tree_depth", None) or tc.max_tree_depth
    cfg.max_plugins_in_ai_context = tc.max_plugins_in_ai_context
    # 2.0.0 规则引擎阈值（CMP001/CMP002）：CLI --max-file-lines 优先，否则默认 2000
    _mfl = getattr(args, "max_file_lines", None)
    if _mfl is not None:
        cfg.max_file_lines = _mfl
    # 2-A：AST 并行进程数。优先级 CLI --workers > settings.analysis.workers >默认 auto(0)
    _w = getattr(args, "workers", None)
    cfg.workers = tc.workers if _w is None else _w
    cfg.extra_repos = list(getattr(args, "extra_repos", None) or [])
    if with_target:
        if getattr(args, "module", None):
            cfg.target, cfg.target_type = args.module, "module"
        elif getattr(args, "plugin", None):
            cfg.target, cfg.target_type = args.plugin, "plugin"
    # 产物按日期归档（DONE-15）：out_dir 下沉到 <out>/<YYYY-MM-DD>，
    # 缓存与基线留在稳定根 stable_out_dir（见 config.apply_date_dir）。
    base_out = Path(args.out).resolve() if getattr(args, "out", None) else cfg.out_dir
    from .config import apply_date_dir
    apply_date_dir(cfg, base_out,
                   enabled=False if getattr(args, "no_date_dir", False) else None)
    return cfg


# ---------------------------------------------------------------------------
# P0 §4.2 请求级缓存（审计报告 TD-3）
# 进程内 memo，消除 HTTP / MCP 每次请求重跑 5–30s 全量分析：
#   * 失效信号：TTL 安全网（默认 15s，可用环境变量 REPO_LENS_ANALYSIS_TTL 调整）
#     兜底「无显式失效」场景（如用户在两次刷新间改了代码）；
#   * 仓内容变更由显式写操作（/api/analyze、git pull/sync --confirm、scripts/run --confirm）
#     调用 invalidate_analysis_cache() 立即失效，保证下次读拿到最新数据；
#   * 注：不可直接用 ast_cache.json 的 mtime 作信号——_analyze 每次运行都会重写它，
#     会使 token 自相矛盾、memo 永远不命中；故仅以 TTL + 显式失效为准。
#   * --no-cache 时跳过 memo，每次都是真·全新分析；
#   * 仅缓存分析结果，不缓存任何落盘副作用；线程安全（ThreadingHTTPServer 下并发读共享）。
# ---------------------------------------------------------------------------
_ANALYSIS_MEMO = {"repo_id": None, "token": None, "result": None}
_ANALYSIS_MEMO_LOCK = threading.Lock()       # 保护 memo 读写
_ANALYSIS_COMPUTE_LOCK = threading.Lock()    # 保护「未命中→计算」临界区，防止分析风暴

#: pack 贡献的 CLI 子命令 → handler（由 _build_argparser 在解析器构建时填充；
#: 未启用的 pack 不贡献，故此处自然为空，run() 据此给出启用提示）。
_PACK_HANDLERS: dict = {}

#: 最近一次分析的缓存统计（阶段二 2-B）。仅供 CLI 信息行读取；
#: **刻意不写入任何产物**——冷跑与热跑的该值必然不同，进入 repo_lens.json
#: 会破坏 --deterministic 的可复现性（golden 层依赖）。
_LAST_CACHE_STATS: dict = {}


def last_cache_stats() -> dict:
    """最近一次分析的缓存统计：`reparsed`（本轮重解析）/ `total`（参与分析的 .py 数）。

    无记录时返回空表，调用方据此跳过该项输出，绝不因缺统计而报错。
    """
    return dict(_LAST_CACHE_STATS)


def _analysis_freshness(cfg: "RepoConfig"):
    """失效 token：仓库身份 + TTL 桶（默认 15s，可用 REPO_LENS_ANALYSIS_TTL 调整）。

    仓内容变化不靠轮询，而由显式写操作调用 invalidate_analysis_cache() 立即失效；
    TTL 仅作安全网，避免「用户改完代码却未点重新分析」时无限期陈旧。
    """
    try:
        ttl = max(float(os.environ.get("REPO_LENS_ANALYSIS_TTL")
                        or os.environ.get("VR_INSIGHT_ANALYSIS_TTL") or 15), 1.0)
    except (TypeError, ValueError):
        ttl = 15.0
    return (id(cfg), int(time.monotonic() // ttl))


def invalidate_analysis_cache() -> None:
    """写操作后显式失效：下次分析必为全新计算，读端点立刻拿到最新数据。"""
    with _ANALYSIS_MEMO_LOCK:
        _ANALYSIS_MEMO["repo_id"] = None
        _ANALYSIS_MEMO["token"] = None
        _ANALYSIS_MEMO["result"] = None


def _analyze(args, cfg: RepoConfig,
             deep: bool = False) -> tuple[dict, int, dict]:
    """执行一次完整分析，返回 (data, duration_ms, parse_cache)。

    AST 预热、缓存读写、五类分析器编排都收敛在这里，各命令共用同一条路径，
    保证「当前状态」的口径在 snapshot / diff / context / query 之间完全一致。
    返回 parse_cache 供 context 对单目标做深挖时复用，避免二次解析。

    P0 §4.2：进程内 memo 包裹，命中直接返回，跳过 5–30s 全量分析。
    """
    if getattr(args, "no_cache", False):
        return _analyze_compute(args, cfg, deep)        # 用户显式要求全新分析
    token = _analysis_freshness(cfg)
    with _ANALYSIS_MEMO_LOCK:
        if (_ANALYSIS_MEMO["repo_id"] == id(cfg)
                and _ANALYSIS_MEMO["token"] == token
                and _ANALYSIS_MEMO["result"] is not None):
            return _ANALYSIS_MEMO["result"]             # 命中：直接返回缓存结果
    # 未命中：加计算锁避免并发请求同时重跑（分析风暴）
    with _ANALYSIS_COMPUTE_LOCK:
        with _ANALYSIS_MEMO_LOCK:
            if (_ANALYSIS_MEMO["repo_id"] == id(cfg)
                    and _ANALYSIS_MEMO["token"] == token
                    and _ANALYSIS_MEMO["result"] is not None):
                return _ANALYSIS_MEMO["result"]         # 双重检查：锁内可能已被其他线程填充
        result = _analyze_compute(args, cfg, deep)
        with _ANALYSIS_MEMO_LOCK:
            _ANALYSIS_MEMO["repo_id"] = id(cfg)
            _ANALYSIS_MEMO["token"] = token
            _ANALYSIS_MEMO["result"] = result
        return result


def _analyze_compute(args, cfg: RepoConfig,
                    deep: bool = False) -> tuple[dict, int, dict]:
    """_analyze 的真正计算体（无 memo）。请走 _analyze 入口，勿直接调用。"""
    t0 = time.perf_counter()

    # ---- AST 预热（阶段二 2-A/2-B 重构）----
    # 「判定」与「解析」分离：主进程逐文件做廉价的「签名 → 内容哈希」双层判定，
    # 未命中的文件收集起来交给进程池并行解析（昂贵的部分）。
    # 文件数 < 200 或 workers=1 时自动串行，故 fixture 级小仓库与既有 verify
    # 套件的执行路径与耗时特征完全不变。
    parse_cache: dict = {}
    use_cache = not getattr(args, "no_cache", False)
    old_entries = cache_load(cfg.cache_dir) if use_cache else {}
    old_hashes = cache_load_hashes(cfg.cache_dir) if use_cache else {}
    live_entries: dict = {}
    live_hashes: dict = {}
    todo: list[tuple[str, str]] = []           # 未命中：(绝对路径, 分析器键)
    pending: dict[str, tuple[str, str]] = {}   # 分析器键 → (缓存键, 当前签名)

    # 无论是否启用缓存，都完整预热 parse_cache，保证两种模式的分析行为完全一致：
    # 各 analyzer 依赖 parse_cache 的填充时机（如 core_analyzer 读 __init__.py 的
    # docstring），预热缺失会导致 package_docstring 等字段出现/消失。
    for rel, fpath in iter_repo_files(cfg):
        if rel.suffix != ".py":
            continue
        ckey = rel.as_posix()                  # 缓存键：跨运行稳定
        pkey = str(rel)                        # 分析器键：与 analyzer 内部一致
        if not use_cache:
            todo.append((str(fpath), pkey))
            continue
        try:
            sig = file_signature(fpath)
        except OSError:
            continue                           # 遍历期间文件消失：跳过
        verdict = cache_resolve(old_entries.get(ckey), fpath,
                                prev_hash=old_hashes.get(ckey), sig=sig)
        if verdict.state == "hit":
            parse_cache[pkey] = verdict.entry
            live_entries[ckey] = {
                "sig": sig,
                "hash": verdict.digest or (old_entries.get(ckey) or {}).get("hash"),
                "entry": verdict.entry,
            }
            if verdict.digest:
                live_hashes[ckey] = verdict.digest
        else:
            todo.append((str(fpath), pkey))
            pending[pkey] = (ckey, sig)

    # 未命中文件并行解析。解析结果严格保持传入顺序，故 parse_cache 的填充顺序
    # 与串行路径一致（下游 analyzer 不依赖顺序，但保持一致可避免行为漂移）。
    reparsed_files = len(todo)
    workers = resolve_workers(getattr(cfg, "workers", 0))
    for pkey, entry, digest in parse_files_parallel(todo, workers=workers):
        parse_cache[pkey] = entry
        if not use_cache:
            continue
        meta = pending.get(pkey)
        if meta is None:
            continue
        ckey, sig = meta
        live_entries[ckey] = {"sig": sig, "hash": digest, "entry": entry}
        if digest:
            live_hashes[ckey] = digest

    overview = scan_overview(cfg, parse_cache)
    core = analyze_core(cfg, parse_cache)
    plugins = analyze_plugins(cfg, parse_cache)
    interactions = analyze_interactions(cfg, parse_cache, core, plugins)
    standards = extract_standards(cfg, plugins)

    duration_ms = round((time.perf_counter() - t0) * 1000)
    data = {
        "meta": _build_meta(cfg, render_tree(cfg), duration_ms,
                            getattr(args, "deterministic", False)),
        "overview": overview,
        "core": core,
        "plugins": plugins,
        "interactions": interactions,
        "standards": standards,
    }

    # ---- 前端/桌面端仓库（v1.4：--extra-repo 或自动发现同级 verorun-workplace）----
    fe_roots = discover_frontend_repos(cfg)
    if fe_roots:
        data["frontend"] = [f for f in (analyze_frontend(cfg, r) for r in fe_roots)
                            if f is not None]

    # ---- 变更热点（v1.5：借鉴 code-maat/CodeScene 的 churn×体量 方法论）----
    # v1.8.0 起带增量缓存（HEAD 短路 / 祖先增量合并），与 AST 缓存共用稳定根；
    # --no-cache 时一并禁用，保证「全新分析」语义完整。
    hs = analyze_hotspots(cfg, parse_cache=parse_cache, use_cache=use_cache)
    if hs:
        hs["mermaid"] = render_mermaid(data)
        # source 是**缓存态派生量**（冷跑 full / 热跑 head_hit / 增量 incremental），
        # 刻意不写进事实源：同一输出目录二次运行会得到不同取值，进入 repo_lens.json
        # 即破坏 --deterministic 的可复现性（与 2-B 缓存统计同一处理原则）。
        # 该字段仍保留在 analyze_hotspots 的返回值中，供诊断与 verify_hotspot 断言。
        data["hotspots"] = {k: v for k, v in hs.items() if k != "source"}

    if deep and cfg.target:
        data["deep_dive"] = analyze_target(cfg, cfg.target_type, cfg.target,
                                           parse_cache, plugins)

    # ---- 符号倒排索引（v1.6.0 阶段一 1-C）----
    # 复用本次 parse_cache 的逐文件事实，零新增解析成本。
    # 主 JSON 只放轻量摘要（count + index_file 指针），全量索引由落盘层
    # 写入独立产物 repo_lens_symbols.json，避免继续撑大唯一事实源。
    data["symbols"] = symbol_summary(build_symbol_index(parse_cache, cfg))

    # 分析已全部完成，缓存 + 哈希台账落盘。
    # live_entries 只含本次仍存在的文件，天然淘汰已删除/重命名的残留项。
    if use_cache and live_entries:
        cache_save(cfg.cache_dir, live_entries, live_hashes)

    # 缓存统计供 CLI 信息行输出（不进任何产物，理由见 _LAST_CACHE_STATS 注释）。
    _LAST_CACHE_STATS.update(reparsed=reparsed_files, total=len(parse_cache))

    # ---- 规则引擎（阶段 F / 2.0.0）：只读派生 findings，不修改任何 analyzer 产物 ----
    # 置于收尾以确保 data 各段（plugins/interactions/overview 等）已全部就绪。
    data["findings"] = run_rules(data, cfg)

    return data, duration_ms, parse_cache


def _cmd_analyze(args) -> int:
    """默认路径：全量分析并落盘四类报告。"""
    if getattr(args, "module", None) and getattr(args, "plugin", None):
        print("[repolens] 错误：--module 与 --plugin 不能同时使用", file=sys.stderr)
        return 2
    cfg = _setup(args, with_target=True)
    if not args.quiet and not args.summary_only:
        print(f"[repolens] 仓库: {cfg.repo_root}")

    data, duration_ms, parse_cache = _analyze(args, cfg, deep=True)

    if args.summary_only:
        print(_render_summary(data, duration_ms))
        # 门禁结果走 stderr，保持 stdout 的 key=value 契约不被污染
        return _run_gates(args, data, cfg, sys.stderr)

    written = _write_reports(cfg, data, args.only, parse_cache=parse_cache,
                             agents_md_mode=getattr(args, "agents_md", None))

    invalid = sum(1 for x in data["plugins"]["items"] if not x["manifest_valid"])
    if cfg.target:
        print(f"[repolens] 指定分析：{cfg.target_type} `{cfg.target}` → "
              f"{data['deep_dive']['file_count']} 文件 / "
              f"{data['deep_dive']['routes_count']} 路由")
    if not args.quiet:
        # 缓存增量指标（2-B）：直接给出「本轮重解析 N/M 文件」，
        # 使增量分析的效果可见、可回归。空统计时该段整体省略。
        st = last_cache_stats()
        cache_note = (f"重解析 {st['reparsed']}/{st['total']} 文件 · "
                      if st.get("total") else "")
        print(f"[repolens] 完成：核心模块 {data['core']['module_count']} 个 · "
              f"插件 {data['plugins']['count']} 个（manifest 待修复 {invalid}） · "
              f"路由 {sum(x['route_count'] for x in data['plugins']['items'])} 条 · "
              f"{cache_note}耗时 {duration_ms} ms")
        for p in written:
            print(f"  输出: {p}")
    # AGENTS.md 写入仓库根属"有意改变 git status"的行为，必须显著提示（不在 --quiet 下静默）
    if Path(cfg.repo_root) / "AGENTS.md" in written:
        print("[repolens] 注意：AGENTS.md 已写入仓库根（agents_md=repo），"
              "会改变 git status；建议 commit 进仓库供所有 Agent 共享，"
              "不需要时改用 --agents-md workspace|off。", file=sys.stderr)
    return _run_gates(args, data, cfg, sys.stdout)


def _write_reports(cfg: RepoConfig, data: dict,
                   only: str = "json,md,html,ai,agents,symbols",
                   parse_cache: dict | None = None,
                   agents_md_mode: str | None = None) -> list[Path]:
    """把分析结果落盘为报告产物，返回写入路径列表。

    抽取为独立函数：命令行（--only 过滤）与控制台（/api/analyze）共用同一落盘逻辑。

    v1.6.0 新增两类产物（均受 --only 令牌控制，缺省包含，向后兼容）：
    - `symbols`：符号倒排索引（独立文件，需 parse_cache；未提供时跳过并保持主
      JSON 的 symbols 摘要段不变）；
    - `agents`：AGENTS.md（三态由 settings.output.agents_md / --agents-md 决定）。
    """
    written: list[Path] = []
    only_set = {s.strip().lower() for s in str(only).split(",") if s.strip()}
    if "json" in only_set:
        p = cfg.out_dir / ARTIFACT_JSON
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(p)
    if "md" in only_set:
        p = cfg.out_dir / ARTIFACT_MD
        p.write_text(render_md(data), encoding="utf-8", newline="\n")
        written.append(p)
    if "html" in only_set:
        p = cfg.out_dir / ARTIFACT_HTML
        p.write_text(render_html(data), encoding="utf-8", newline="\n")
        written.append(p)
    if "ai" in only_set:
        p = cfg.out_dir / ARTIFACT_AI_CONTEXT
        p.write_text(render_ai_context(data, cfg.max_plugins_in_ai_context),
                     encoding="utf-8", newline="\n")
        written.append(p)
    if "symbols" in only_set and parse_cache is not None:
        written.append(write_symbol_index(cfg.out_dir,
                                         build_symbol_index(parse_cache, cfg),
                                         filename=ARTIFACT_SYMBOLS))
    if "agents" in only_set:
        mode = agents_md_mode or agents_md_setting()
        p = emit_agents_md(cfg, data, mode)
        if p is not None:
            written.append(p)

    if data.get("deep_dive"):
        dd = data["deep_dive"]
        # 名称消毒：deep 产物文件名来自 --module/--plugin（HTTP API 亦可传入），
        # 阻断路径穿越（如 "../../x"），只允许字母数字下划线连字符。
        safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", dd["name"]) or "target"
        p = cfg.out_dir / f"verorun_deep_{safe_name}.json"
        p.write_text(json.dumps(dd, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(p)
        p = cfg.out_dir / f"repolens_deep_{safe_name}.md"
        p.write_text(_render_deep_md(dd), encoding="utf-8", newline="\n")
        written.append(p)
    return written


def _cmd_repos(args) -> int:
    """多仓库注册表管理（本地 ~/.repolens/repos.json）。"""
    from .repo_registry import list_profiles, list_repos, add_repo, remove_repo
    sub = getattr(args, "repos_cmd", None) or "list"
    if sub == "add":
        try:
            ent = add_repo(args.name, args.path, args.profile)
        except ValueError as e:
            print(f"[repolens] {e}")
            return 2
        prof = ent.get("profile") or "verorun(内置)"
        print(f"[repolens] 已注册：{ent['name']} -> {ent['path']}（profile: {prof}）")
        return 0
    if sub == "remove":
        if not args.confirm:
            print(f"[repolens] 预览：将移除注册项 {args.name}（不影响磁盘文件）。加 --confirm 执行。")
            return 0
        ok = remove_repo(args.name)
        print(f"[repolens] {'已移除' if ok else '未找到'}：{args.name}")
        return 0 if ok else 1
    # list
    rows = list_repos()
    print(f"{'名称':<20} {'profile':<18} 路径")
    for r in rows:
        print(f"{r['name']:<20} {(r.get('profile') or 'verorun'):<18} {r.get('path', '')}")
    print(f"\n可用 profile：{', '.join(list_profiles())}；共 {len(rows)} 个仓库")
    return 0


def _cmd_serve(args) -> int:
    """启动本地可视化控制台：浏览器点按钮执行，Agent 走 HTTP API。"""
    cfg = _setup(args)
    frontends = [f for f in (analyze_frontend(cfg, r)
                             for r in discover_frontend_repos(cfg)) if f]

    def analyzer(_target=None):
        """控制台统一分析入口：与 CLI 同一管线，保证口径一致。

        cfg 从 _Handler.state 动态读取——支持 /api/repos/switch 运行中换仓。
        """
        ns = SimpleNamespace(no_cache=getattr(args, "no_cache", False),
                             deterministic=False)
        try:
            from .server import _Handler
            cur_cfg = _Handler.state["cfg"]
        except Exception:
            cur_cfg = cfg
        data, duration, parse_cache = _analyze(ns, cur_cfg)
        try:
            from .server import _Handler
            with _Handler.state_lock:
                _Handler.state["last_data"] = data
                _Handler.state["last_duration"] = duration
                _Handler.state["last_cache"] = parse_cache
        except Exception:
            pass
        return data, duration, parse_cache

    def summarize(data, duration):
        return dict(_summary_pairs(data, duration))

    def write_reports(cfg_):
        data, _, parse_cache = analyzer()
        return _write_reports(cfg_, data, parse_cache=parse_cache)

    from .server import start
    url = f"http://127.0.0.1:{args.port}"
    if not getattr(args, "no_browser", False):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    start(cfg, frontends, analyzer, summarize, write_reports, port=args.port)
    return 0


def _cmd_mcp(args) -> int:
    """以 stdio MCP server 运行：从 stdin 读 JSON-RPC，向 stdout 写应答。

    供千问办公 / WorkBuddy 等 MCP-capable Agent 直接调用本工具能力。
    所有执行均经 catalog（与 /api/* 同源），写类操作继承 run_script_api 门控，
    红线：127.0.0.1 + kind=tool + confirm，绝不放宽。stdout 仅输出纯 JSON-RPC。
    """
    cfg = _setup(args)
    from .mcp.stdio import serve
    serve(cfg)
    return 0


def _cmd_snapshot(args) -> int:
    """生成基线快照：只存可比较的标量指标，落 out/history/<name>.json。"""
    cfg = _setup(args)
    quiet = getattr(args, "quiet", False)
    if not quiet:
        print(f"[repolens] 仓库: {cfg.repo_root}")
    data, duration_ms, _ = _analyze(args, cfg)
    snap = build_snapshot(data)

    if getattr(args, "stdout", False):
        print(json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    name = getattr(args, "save", None) or default_name()
    path = save_snapshot(cfg.snapshot_dir, name, snap)
    if not quiet:
        print(f"[repolens] 快照已保存：{path} · "
              f"核心 {len(snap['core'])} 个 · 插件 {len(snap['plugins'])} 个 · "
              f"耗时 {duration_ms} ms")
    return 0


def _cmd_diff(args) -> int:
    """与基线快照对比，输出「变了什么」。"""
    cfg = _setup(args)
    try:
        base_name, base = load_snapshot(args.baseline, cfg.snapshot_dir)
    except FileNotFoundError as e:
        print(f"[repolens] 错误：{e}", file=sys.stderr)
        avail = list_baselines(cfg.snapshot_dir)
        if avail:
            print(f"  可用基线：{', '.join(avail)}", file=sys.stderr)
        else:
            print("  尚无基线，请先运行：repolens.py snapshot --save baseline",
                  file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"[repolens] 错误：{e}", file=sys.stderr)
        return 2

    quiet = getattr(args, "quiet", False)
    if not quiet:
        print(f"[repolens] 仓库: {cfg.repo_root}")
    data, _, _ = _analyze(args, cfg)
    result = diff_snapshots(base, build_snapshot(data), top=args.top)

    if args.format == "json":
        print(json.dumps({"baseline": base_name, "current": "current", "diff": result},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_diff_md(result, base_name), end="")
    return 0


def _resolve_target(data: dict, spec: str) -> tuple[str, str] | None:
    """解析 `--for` 目标，返回 (target_type, name)。

    带前缀时按前缀解析（`plugin:x` / `module:x`）；不带前缀时先按插件匹配，
    再按核心模块匹配，都匹配不上返回 None（由调用方报「未找到」并给候选）。
    """
    spec = (spec or "").strip()
    ttype: str | None = None
    name = spec
    if ":" in spec:
        prefix, name = spec.split(":", 1)
        p = prefix.strip().lower()
        if p in ("plugin", "plugins"):
            ttype = "plugin"
        elif p in ("module", "modules", "core"):
            ttype = "module"
        else:
            raise ValueError(f"未知的目标类型 `{prefix}`（可选 plugin / module）")
        name = name.strip()
    if not name:
        raise ValueError("--for 的目标名称不能为空")

    if ttype in (None, "plugin"):
        hit = next((p["dir"] for p in data["plugins"]["items"]
                    if p["dir"] == name or p["identifier"] == name), None)
        if hit:
            return "plugin", hit
        if ttype == "plugin":
            return None
    if any(m["name"] == name for m in data["core"]["modules"]):
        return "module", name
    return None


# ---------------------------------------------------------------------------
# v1.6.0（阶段一 1-B / 1-C）：面向 Agent 的纯函数执行面
# ---------------------------------------------------------------------------
# CLI 与 MCP catalog 共用同一套实现，杜绝"CLI 一套、MCP 另一套"的双实现漂移
# （沿用 LLM 回环的同一哲学：执行面唯一化）。三个函数均为只读、无落盘副作用，
# 参数校验与执行走在同一条路径上，因此 CLI 的 rc=2 与 MCP 的 content 错误语义一致。

class TargetNotFound(ValueError):
    """context 目标解析失败。message 面向人；candidates 为可用候选（可空）。"""

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = list(candidates or [])


def build_context(cfg: RepoConfig, data: dict, spec: str | None,
                  depth: str = "brief", parse_cache: dict | None = None) -> dict:
    """按需上下文切片（只读）。spec 为空 → 全局 AI 上下文。

    返回 {"kind": "global"|"target", "context": str, ...}；
    目标不存在时抛 TargetNotFound，由调用方决定呈现方式（rc=2 / content 错误）。
    """
    if not spec:
        return {"kind": "global",
                "context": render_ai_context(data, cfg.max_plugins_in_ai_context)}
    target = _resolve_target(data, spec)
    if target is None:
        raise TargetNotFound(
            f"找不到目标 `{spec}`",
            [p["identifier"] for p in data["plugins"]["items"][:8]])
    ttype, name = target
    if depth not in ("brief", "normal", "full"):
        depth = "brief"
    dd = analyze_target(cfg, ttype, name, parse_cache or {}, data["plugins"])
    return {"kind": "target", "target_type": ttype, "name": name, "depth": depth,
            "context": render_target_context(data, dd, depth=depth)}


def build_query(data: dict, scope: str | None = None, select: list[str] | None = None,
                where: str | None = None, fmt: str = "json",
                rows: list[dict] | None = None) -> dict:
    """受限结构化查询（只读）。参数非法抛 QueryError。

    校验顺序刻意与 CLI 历史行为一致：scope 推断 → scope 合法性 → where 解析 →
    select/where 字段路径校验，任何一步失败都在全量分析之后立即归零，语义不变。

    ``rows`` 非 None 表示行来自 SQLite 加速层（v1.8.0 3-B），此时 ``data`` 可为空
    dict——校验与投影/过滤逻辑完全走同一段代码，故两条路径结果必然一致。
    """
    scope = scope or infer_scope(select or [])
    if scope not in SCOPES:
        raise QueryError(f"未知的查询范围：{scope}（可选：{', '.join(SCOPES)}）")
    conds = parse_where(where)
    for f in (select or []):
        parse_query_path(f)
    for path, _, _ in conds:
        parse_query_path(path)
    result = run_query(data, scope, select, where, rows=rows)
    out = {"result": result, "format": fmt, "scope": scope}
    if fmt == "table":
        out["table"] = render_query_table(result)
    return out


def symbol_index_for(cfg: RepoConfig, parse_cache: dict | None = None) -> dict:
    """取符号索引：内存 parse_cache（最准，零 IO）→ 落盘产物 → 空索引降级。"""
    if parse_cache:
        return build_symbol_index(parse_cache, cfg)
    try:
        p = Path(cfg.out_dir) / ARTIFACT_SYMBOLS
        if p.is_file():
            return index_from_file(p)
    except Exception:  # noqa: BLE001 —— 索引属辅助能力，不得阻断调用方
        pass
    try:
        p = Path(cfg.stable_out_dir or cfg.out_dir) / ARTIFACT_SYMBOLS
        if p.is_file():
            return index_from_file(p)
    except Exception:  # noqa: BLE001
        pass
    return {"schema": 1, "count": 0, "symbols": {}}


def build_search(cfg: RepoConfig, data: dict, symbol: str, limit: int = 50,
                 parse_cache: dict | None = None) -> dict:
    """符号定位（只读）：精确命中优先 → 大小写不敏感子串回退。

    索引缺失时返回空结果 + note 提示（而非报错）：检索是辅助能力，
    不应因为没跑过全量分析就让 Agent 会话断掉。
    """
    index = symbol_index_for(cfg, parse_cache)
    res = search_symbols(index, symbol, limit=limit)
    if not index.get("count"):
        res["note"] = ("符号索引为空：请先执行一次全量分析（repolens --repo <repo>）"
                       "或在 MCP 会话中先调用 repo.summary 触发分析")
    return res


def _cmd_context(args) -> int:
    """输出面向 Agent 的上下文切片（stdout，不落盘）。"""
    cfg = _setup(args)
    spec = getattr(args, "for_", None)
    data, _, parse_cache = _analyze(args, cfg)
    try:
        out = build_context(cfg, data, spec, depth=args.depth, parse_cache=parse_cache)
    except TargetNotFound as e:
        print(f"[repolens] 错误：{e}", file=sys.stderr)
        if e.candidates:
            print(f"  可用插件（前 8）：{', '.join(e.candidates)}", file=sys.stderr)
        print("  用法：repolens context --for plugin:<identifier> "
              "| --for module:<name>", file=sys.stderr)
        return 2
    print(out["context"], end="")
    return 0


def _cmd_query(args) -> int:
    """受限查询：取字段 + 等值过滤。

    v1.8.0 3-B：优先走 SQLite 加速层——库存在且新鲜时直接取行，**跳过全量分析**
    （大仓库从 40s 量级降到毫秒级）。库缺失 / 过期 / scope 不支持则回退原路径，
    并在回退时顺手建库以加速下次；``--rebuild-index`` 强制走全量并重建。
    """
    cfg = _setup(args)
    select = [s.strip() for s in (getattr(args, "select", None) or "").split(",")
              if s.strip()]
    rebuild = bool(getattr(args, "rebuild_index", False))
    scope = getattr(args, "scope", None) or infer_scope(select)

    no_cache = bool(getattr(args, "no_cache", False))
    rows = None if (rebuild or no_cache) else index_db.load_rows(cfg, scope)
    data: dict = {}
    if rows is None:
        data, _, parse_cache = _analyze(args, cfg)
        if not no_cache:
            # 顺手建库：内存 parse_cache 直接供符号表，避免读落盘产物
            index_db.build(cfg, data, symbols=symbol_index_for(cfg, parse_cache))
    try:
        # 参数非法统一归入 rc=2；实际校验在 build_query 内与 MCP 共用同一路径。
        out = build_query(data, scope=getattr(args, "scope", None), select=select,
                          where=getattr(args, "where", None),
                          fmt=getattr(args, "format", "json") or "json",
                          rows=rows)
    except QueryError as e:
        print(f"[repolens] 错误：{e}", file=sys.stderr)
        return 2
    if out["format"] == "table":
        print(out["table"])
    else:
        print(json.dumps(out["result"], ensure_ascii=False, indent=2))
    return 0


def _cmd_search(args) -> int:
    """符号定位：精确命中优先 → 子串回退（只读，stdout，不落盘）。

    退出码语义对齐 grep：0=有命中，1=无命中，2=参数/环境错误，
    便于 CI 与 Agent 用退出码直接编程判断，无需解析文本。
    """
    cfg = _setup(args)
    symbol = getattr(args, "symbol", "") or ""
    data, _, parse_cache = _analyze(args, cfg)
    res = build_search(cfg, data, symbol,
                       limit=getattr(args, "limit", 50) or 50,
                       parse_cache=parse_cache)
    if (getattr(args, "format", "json") or "json") == "table":
        print(render_symbol_table(res))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("hits") else 1


def _cmd_log(args) -> int:
    """扫描提交历史。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    data = scan_commits(
        repo_root,
        since_days=args.since,
        author=getattr(args, "author", None),
        max_commits=getattr(args, "max_commits", None),
    )

    if "error" in data:
        print(f"[repolens] 错误: {data['error']}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.format == "md":
        # Markdown 格式（简化版，复用 table 逻辑）
        print(format_commit_table(data))
    else:
        print(format_commit_table(data))
    return 0


def _cmd_remote_diff(args) -> int:
    """分析本地与远程仓库差异。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    data = analyze_remote_diff(
        repo_root,
        remote=args.remote,
        branch=getattr(args, "branch", None),
    )

    if "error" in data:
        print(f"[repolens] 错误: {data['error']}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_remote_diff_table(data))
    return 0


def _cmd_untracked(args) -> int:
    """检测未跟踪文件并分类。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    data = scan_untracked(
        repo_root,
        classify=getattr(args, "classify", True),
        suggest_ignore=getattr(args, "suggest_ignore", True),
    )

    if "error" in data:
        print(f"[repolens] 错误: {data['error']}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.format == "md":
        print(format_untracked_table(data))
    else:
        print(format_untracked_table(data))
    return 0


def _cmd_group(args) -> int:
    """管理仓库组配置。"""
    group_cmd = getattr(args, "group_cmd", None)

    if group_cmd == "add":
        repo_path = Path(args.repo).expanduser().resolve()
        remotes = {}
        if getattr(args, "remote_github", None):
            remotes["github"] = args.remote_github
        if getattr(args, "remote_gitee", None):
            remotes["gitee"] = args.remote_gitee

        add_repo_to_group(args.group_name, repo_path, remotes)
        print(f"[repolens] 已将仓库添加到组 '{args.group_name}': {repo_path}")
        if remotes:
            for name, url in remotes.items():
                print(f"  Remote [{name}]: {url}")
        return 0

    elif group_cmd == "list":
        filter_name = getattr(args, "name", None)
        groups = list_groups(filter_group=filter_name)
        print(format_group_table(groups))
        return 0

    elif group_cmd == "remove":
        repo_path = Path(args.repo).expanduser().resolve()
        success = remove_repo_from_group(args.group_name, repo_path)
        if success:
            print(f"[repolens] 已从组 '{args.group_name}' 移除仓库: {repo_path}")
            return 0
        else:
            print(f"[repolens] 错误: 仓库不在组 '{args.group_name}' 中", file=sys.stderr)
            return 2

    else:
        print("[repolens] 错误: 请指定子命令 (add/list/remove)", file=sys.stderr)
        return 2


def _cmd_batch(args) -> int:
    """批量执行命令于仓库组。"""
    from .packs.gitflow.repo_group import load_groups

    groups = load_groups()
    group_name = args.group

    if group_name not in groups:
        print(f"[repolens] 错误: 仓库组 '{group_name}' 不存在", file=sys.stderr)
        avail = list(groups.keys())
        if avail:
            print(f"  可用组: {', '.join(avail)}", file=sys.stderr)
        return 2

    group = groups[group_name]
    command = args.command
    results = []

    for repo in group.repos:
        if not repo.path.exists():
            results.append({
                "repo": str(repo.path),
                "error": "路径不存在",
            })
            continue

        # 构建模拟 args 对象
        ns = type('Args', (), {
            "repo": str(repo.path),
            "since": getattr(args, "since", 90),
            "author": None,
            "max_commits": None,
            "format": args.format,
            "remote": getattr(args, "remote", "origin"),
            "branch": getattr(args, "branch", None),
            "classify": True,
            "suggest_ignore": True,
        })()

        if command == "log":
            data = scan_commits(repo.path, since_days=ns.since)
        elif command == "remote-diff":
            data = analyze_remote_diff(repo.path, remote=ns.remote, branch=ns.branch)
        elif command == "untracked":
            data = scan_untracked(repo.path)
        else:
            data = {"error": f"未知命令: {command}"}

        results.append({
            "repo": str(repo.path),
            "data": data,
        })

    # 输出结果
    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"\n{'='*60}")
            print(f"仓库: {r['repo']}")
            print('='*60)
            if "error" in r:
                print(f"错误: {r['error']}")
            elif "data" in r:
                data = r["data"]
                if "error" in data:
                    print(f"错误: {data['error']}")
                elif "total_commits" in data:
                    print(format_commit_table(data))
                elif "ahead" in data or "behind" in data:
                    print(format_remote_diff_table(data))
                elif "total_untracked" in data:
                    print(format_untracked_table(data))
    return 0


def _cmd_push(args) -> int:
    """智能推送到远程仓库。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    result = smart_push(
        repo_root,
        remote=args.remote,
        branch=getattr(args, "branch", None),
        dry_run=args.dry_run,
        check_ci=getattr(args, "check_ci", False),
        confirm=args.confirm,
    )

    if "error" in result:
        print(f"[repolens] 错误: {result['error']}", file=sys.stderr)
        if result.get("suggestion"):
            print(f"建议: {result['suggestion']}", file=sys.stderr)
        return 2

    print(result.get("message", ""))
    if result.get("unpushed_commits"):
        print("\n未推送的提交:")
        for commit in result["unpushed_commits"][:10]:
            print(f"  - {commit}")
    if result.get("ci_status"):
        print(f"\nCI 状态: {result['ci_status']}")
    if result.get("action_required"):
        print(f"\n{result['action_required']}")
    return 0


def _cmd_pull(args) -> int:
    """智能拉取远程更新。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    result = smart_pull(
        repo_root,
        remote=args.remote,
        branch=getattr(args, "branch", None),
        dry_run=args.dry_run,
        rebase=getattr(args, "rebase", False),
        confirm=args.confirm,
    )

    if "error" in result:
        print(f"[repolens] 错误: {result['error']}", file=sys.stderr)
        if result.get("suggestion"):
            print(f"建议: {result['suggestion']}", file=sys.stderr)
        return 2

    print(result.get("message", ""))
    if result.get("unpulled_commits"):
        print("\n将拉取的提交:")
        for commit in result["unpulled_commits"][:10]:
            print(f"  - {commit}")
    if result.get("conflict_risk"):
        print(f"\n冲突风险: {result['conflict_risk']}")
    if result.get("action_required"):
        print(f"\n{result['action_required']}")
    return 0


def _cmd_sync(args) -> int:
    """多仓库同步（GitHub ↔ Gitee）。"""
    results = sync_repos(
        group_name=args.group,
        direction=args.direction,
        dry_run=args.dry_run,
        confirm=args.confirm,
    )

    # 检查是否有顶层错误
    if len(results) == 1 and "error" in results[0] and len(results[0]) == 1:
        print(f"[repolens] 错误: {results[0]['error']}", file=sys.stderr)
        return 2

    print(format_sync_results(results))
    return 0


def _cmd_ci_check(args) -> int:
    """分析 CI/CD 配置合规性。"""
    repo_root = Path(getattr(args, "repo", None) or ".")
    if not repo_root.is_absolute():
        repo_root = Path.cwd() / repo_root
    repo_root = repo_root.resolve()

    data = analyze_ci_config(repo_root, platform=args.platform)

    if "error" in data:
        print(f"[repolens] 错误: {data['error']}", file=sys.stderr)
        if data.get("suggestion"):
            print(f"建议: {data['suggestion']}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_ci_findings(data))
    return 0


def _cmd_mcp_out(args) -> int:
    """outbound MCP 客户端（审计 EXT-4）：发现并调用外部 MCP Server 的工具。

    与 inbound（`mcp` 子命令）方向相反：这里是本工具当客户端去连外部 Server。
    白名单来自 settings.mcp_servers —— 不接受命令行传入任意 command（安全红线）。
    调用默认 dry-run，必须显式 --confirm 才真执行，与 run_script_api 门控口径一致。

    退出码：0 成功 / 2 参数或环境错 / 3 外部服务异常。
    """
    from .settings import is_outbound_mcp_enabled, mcp_servers

    if not is_outbound_mcp_enabled():
        print("[repolens] outbound MCP 已关闭（settings: mcp_outbound_enabled=false）",
              file=sys.stderr)
        return 2

    action = getattr(args, "action", None)
    rest = [str(x) for x in (getattr(args, "rest", []) or [])]

    # ---- servers：列出已配置的外部 Server ----
    if action == "servers":
        servers = mcp_servers()
        if not servers:
            print("（未配置外部 MCP Server；在 settings.json 的 mcp_servers 中声明，"
                  "或用 REPO_LENS_SETTINGS 指向一份配置）")
            return 0
        print(f"{'NAME':20} {'TRANSPORT':10} {'ENABLED':8} 目标")
        print("-" * 70)
        for s in servers:
            target = s.get("url") or " ".join([str(s.get("command") or ""),
                                               *[str(a) for a in (s.get("args") or [])]]).strip()
            print(f"{s.get('name',''):20} {s.get('transport','stdio'):10} "
                  f"{str(s.get('enabled', True)):8} {target[:60]}")
        print(f"\n共 {len(servers)} 个；发现工具：repolens.py mcp-out tools --server <NAME>")
        return 0

    # ---- tools：发现外部工具 ----
    if action == "tools":
        p = argparse.ArgumentParser(prog="repolens.py mcp-out tools", add_help=False)
        p.add_argument("--server", default=None)
        p.add_argument("--json", action="store_true", dest="as_json")
        try:
            opts, _ = p.parse_known_args(rest)
        except SystemExit:
            return 2
        from .scriptlib.adapters.mcp import discover_tools
        tools, errors = discover_tools(opts.server)
        if opts.as_json:
            print(json.dumps({"tools": tools, "errors": errors}, ensure_ascii=False, indent=1))
        else:
            print(f"{'SERVER':16} {'TOOL':34} 说明")
            print("-" * 92)
            for t in tools:
                print(f"{t['server']:16} {str(t['name']):34} {(t.get('description') or '')[:40]}")
            for e in errors:
                print(f"[error] {e}", file=sys.stderr)
            print(f"\n共 {len(tools)} 个外部工具；调用：repolens.py mcp-out call <server> <tool> --confirm")
        return 0

    # ---- call：调用某个外部工具 ----
    if action == "call":
        p = argparse.ArgumentParser(prog="repolens.py mcp-out call", add_help=False)
        p.add_argument("positional", nargs="*")
        p.add_argument("--arg", action="append", default=[], metavar="K=V")
        p.add_argument("--confirm", action="store_true")
        p.add_argument("--json", action="store_true", dest="as_json")
        try:
            opts, _ = p.parse_known_args(rest)
        except SystemExit:
            return 2
        pos = list(opts.positional)
        if len(pos) < 2:
            print("[repolens] 用法：mcp-out call <server> <tool> [--arg k=v ...] "
                  "[--confirm] [--json]", file=sys.stderr)
            return 2
        server, tool = pos[0], pos[1]
        arguments: dict = {}
        for kv in opts.arg:
            if "=" not in kv:
                print(f"[repolens] --arg 须为 k=v 形式：{kv}", file=sys.stderr)
                return 2
            k, v = kv.split("=", 1)
            arguments[k] = v
        if not opts.confirm:
            print("[repolens] dry-run（未 --confirm）：")
            print(json.dumps({"server": server, "tool": tool, "arguments": arguments},
                             ensure_ascii=False, indent=1))
            return 0
        from .scriptlib.adapters.mcp import McpOutboundError, get_client
        try:
            r = get_client(server).call_tool(tool, arguments)
        except McpOutboundError as e:
            print(f"[repolens] 外部 MCP 调用失败：{e}", file=sys.stderr)
            return 3
        except Exception as e:  # noqa: BLE001 - 外部服务异常一律收敛为 rc=3
            print(f"[repolens] 外部 MCP 调用异常：{type(e).__name__}: {e}", file=sys.stderr)
            return 3
        if opts.as_json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print(r.get("text", ""))
        return 0 if not r.get("isError") else 1

    print("[repolens] 用法：mcp-out {servers|tools|call} ...", file=sys.stderr)
    return 2


def _cmd_audit(args) -> int:
    """代码审计：复用 _analyze 的 data（不重扫）→ 多维 Finding → 放行裁决 + 报告。

    退出码沿用 gate 契约：0=放行 / 1=不放行 / （2 参数环境错、3 异常由 main 统一兜底）。
    """
    from .audit import engine as AE
    from .audit import report as AR

    cfg = _setup(args)
    ns = SimpleNamespace(no_cache=getattr(args, "no_cache", False),
                         deterministic=getattr(args, "deterministic", False))
    data, _duration, _pc = _analyze(ns, cfg)

    dims = [d.strip() for d in args.dims.split(",") if d.strip()] if args.dims else None
    extra_rules = None
    if getattr(args, "audit_rules", None):
        try:
            import json as _json
            data_ = _json.loads(Path(args.audit_rules).read_text(encoding="utf-8"))
            extra_rules = data_.get("rules", data_) if isinstance(data_, dict) else data_
        except (OSError, ValueError) as e:
            print(f"[repolens] 错误：无法解析 --audit-rules：{e}", file=sys.stderr)
            return 2

    from .audit import semantic as SEM
    semantic_findings = None
    use_semantic = args.with_semantic
    if getattr(args, "semantic_results", None):
        try:
            semantic_findings, rejects = SEM.load_results(args.semantic_results)
        except Exception as e:  # noqa: BLE001 - 外灌文件格式不限，统一转 rc=2
            print(f"[repolens] 错误：无法解析 --semantic-results：{e}", file=sys.stderr)
            return 2
        for r in rejects:
            print(f"[audit] 语义结果被拒：{r}", file=sys.stderr)
        use_semantic = True

    # ---- P2：内置 LLM 语义通道（可选；任何失败都降级，不影响审计主流程）----
    # 红线：语义发现默认仅咨询（semantic_can_block 由用户显式控制），此处不修改裁决口径。
    if getattr(args, "with_llm", False):
        from .llm import runner as LR
        llm_run = LR.run_semantic_audit(
            cfg, data, dims or list(AE.DEFAULT_DIMS),
            model_name=getattr(args, "llm_model", None),
            use_tools=not getattr(args, "llm_no_tools", False))
        if llm_run.ran:
            semantic_findings = list(semantic_findings or []) + llm_run.findings
            use_semantic = True
            for r in llm_run.rejects:
                print(f"[audit] 语义结果被拒：{r}", file=sys.stderr)
            if not getattr(args, "quiet", False):
                print(f"[audit] LLM 语义通道：ran=true model={llm_run.model} "
                      f"findings={len(llm_run.findings)} tool_calls={llm_run.tool_calls}"
                      f"（语义发现默认仅咨询，不影响放行）", file=sys.stderr)
        else:
            # 降级：退回「导出任务书给外部 Agent」，与未启用 LLM 时行为一致
            print(f"[audit] LLM 通道已降级（不影响本次审计）：{llm_run.reason}",
                  file=sys.stderr)

    rep = AE.run_audit(cfg, data, cfg.repo_root, dims=dims,
                       fail_on_severity=args.fail_on_severity,
                       use_semantic=use_semantic, extra_rules=extra_rules,
                       semantic_findings=semantic_findings,
                       semantic_can_block=args.semantic_can_block)

    if getattr(args, "emit_semantic", None):
        _ep = Path(args.emit_semantic)
        _ep.parent.mkdir(parents=True, exist_ok=True)
        _ep.write_text(
            SEM.build_prompt(cfg, data, dims or list(AE.DEFAULT_DIMS)), encoding="utf-8")
        if not getattr(args, "quiet", False):
            print(f"[audit] 已导出语义任务书：{args.emit_semantic}"
                  f"（交 AI Agent 判定后用 --semantic-results 回灌）")

    from .audit import versioning as AV
    rep_dict = rep.to_dict()

    fmts = {f.strip() for f in (args.format or "").split(",") if f.strip()}
    out = cfg.out_dir / "audit"
    if {"json", "md"} & fmts:
        out.mkdir(parents=True, exist_ok=True)
    if "json" in fmts:
        (out / "audit_report.json").write_text(
            AR.render_json(rep, deterministic=getattr(args, "deterministic", False)),
            encoding="utf-8", newline="\n")
    if "md" in fmts:
        (out / "audit_report.md").write_text(AR.render_md(rep), encoding="utf-8", newline="\n")

    # stdout：annot 模式给 CI 注解；否则给人读 md（quiet 抑制）
    if "annot" in fmts:
        print(AR.render_annotations(rep), end="")
    elif not getattr(args, "quiet", False):
        print(AR.render_md(rep))

    # 版本闭环：基线保存与对比
    if getattr(args, "save_baseline", None):
        p = AV.save(cfg.out_dir, args.save_baseline, rep_dict)
        if not getattr(args, "quiet", False):
            print(f"[audit] 已存基线：{p}")
    if getattr(args, "diff_baseline", None):
        base = AV.load(cfg.out_dir, args.diff_baseline)
        if base is None:
            print(f"[repolens] 找不到审计基线：{args.diff_baseline}"
                  f"；现有：{', '.join(AV.list_baselines(cfg.out_dir)) or '（无）'}", file=sys.stderr)
        else:
            print(AV.render_diff_md(args.diff_baseline, AV.diff(base, AV.fingerprint(rep_dict))))

    v = rep.verdict
    return 0 if v.decision == "releasable" else 1


def run(argv: list[str] | None = None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = _build_argparser().parse_args(argv)
    cmd = getattr(args, "cmd", None)
    handlers = {"snapshot": _cmd_snapshot, "diff": _cmd_diff,
                "context": _cmd_context, "query": _cmd_query,
                "search": _cmd_search,
                "serve": _cmd_serve,
                # 多仓库注册表管理
                "repos": _cmd_repos,
                # Git 工作流扩展子命令（阶段 G）
                "log": _cmd_log, "remote-diff": _cmd_remote_diff,
                "untracked": _cmd_untracked,
                # ci-check 是只读分析，留在内核（不随 gitflow pack 剥离）
                "ci-check": _cmd_ci_check,
                # 脚本资产库子命令（阶段 A）
                "script": _cmd_script,
                # 代码审计子命令（阶段 1）
                "audit": _cmd_audit,
                # MCP 服务化子命令（需求3 主体）
                "mcp": _cmd_mcp,
                # outbound MCP 客户端（审计 EXT-4）：本工具消费外部 MCP Server
                "mcp-out": _cmd_mcp_out}
    # pack 贡献的命令（push/pull/sync/group/batch）：未启用时不在表中，
    # 且 argparse 未注册它们（_PackAwareParser 会给出启用指引）。
    handlers.update(_PACK_HANDLERS)
    if cmd in handlers:
        return handlers[cmd](args)
    return _cmd_analyze(args)


def _clean_message(msg: str) -> str:
    """去掉本工具自己的提示前缀并把多行压成单行，便于机器解析。"""
    text = " ".join(str(msg).split())
    return re.sub(r"^\[(?:repolens|dev-insight)\]\s*", "", text)


def _hint_for(e: BaseException) -> str:
    """针对已知错误给可操作的下一步建议（供 Agent 直接照做）。"""
    msg = str(e)
    if isinstance(e, (FileNotFoundError, NotADirectoryError)):
        return "路径不存在或不可读：检查 --repo / --out 是否正确，以及当前账号是否有访问权限。"
    if isinstance(e, PermissionError):
        return "权限不足：换一个有读权限的账号运行，或用 --out 指定可写目录。"
    if isinstance(e, (json.JSONDecodeError, UnicodeDecodeError)):
        return "编码或格式异常：先用 --no-cache 重跑排除缓存因素；工具按 UTF-8 → GB18030 回退读取，仍失败需定位具体文件。"
    if isinstance(e, RecursionError):
        return "解析层级过深：检查是否存在异常嵌套的 Python 文件。"
    if "仓库" in msg or "repo" in msg.lower():
        return "仓库定位失败：用 --repo 显式指定仓库根目录，例如 --repo D:\\projects\\verorun-code"
    return "未预期的内部错误：先用 --no-cache 重跑一次以排除缓存因素；若仍失败，请把上面的 type 与 message 一并反馈。"


def _emit_error(etype: str, message: str, hint: str) -> None:
    """结构化错误：一律写 stderr，保证 stdout 的机器可读契约不被污染。"""
    payload = {"error": {"type": etype, "message": _clean_message(message),
                         "hint": hint}}
    try:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    except Exception:
        print(f"[repolens] 运行失败: {etype}: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except SystemExit as e:
        code = e.code
        if code in (None, 0):
            return 0
        # 配置类错误（如 autodetect_repo 失败）以字符串消息抛出，归入「参数/环境错误」
        if isinstance(code, str):
            _emit_error("ConfigError", code, _hint_for(RuntimeError(str(code))))
        else:
            _emit_error("ArgumentError", f"命令行参数错误（exit {code}）",
                        "用 `repolens.py --help` 查看用法；子命令用 `repolens.py <cmd> --help`。")
        return 2                                  # 2 = 参数/环境错误
    except Exception as e:                        # 顶层兜底：本地工具不允许裸堆栈吓人
        _emit_error(type(e).__name__, str(e), _hint_for(e))
        return 3                                  # 3 = 运行异常（区别于门禁失败 1）
