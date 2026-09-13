# -*- coding: utf-8 -*-
"""本地可视化控制台（`repolucent.py serve`）：给人点的界面 + 给 Agent 调用的 HTTP API。

设计边界（与整体哲学一致）：
- 纯标准库（http.server），零第三方依赖；不接入 LLM，只暴露本工具已有的确定性能力。
- 默认只绑定 127.0.0.1；POST 请求体做字段白名单与长度限制；不提供任意命令/文件执行。
- 所有执行都是进程内复用 cli 的分析管线（_analyze / snapshot / diff / query），
  保证「界面看到的结果」与「命令行/AI 调用的结果」口径完全一致。
- 同步执行：完整分析 5-30s，UI 用 loading 态等待即可。

安全模型（v1.5.1 起，四层递进，参考 Jupyter Server token 与 Chrome DevTools host 校验）：
    L1 Host 白名单   请求 Host 头必须属于 127.0.0.1 / localhost / ::1，否则 403
                     （DNS rebinding 后 Host 头必为攻击域名，一击必杀）
    L2 Origin 校验   POST 带 Origin 头时，origin host 必须属于本地白名单，否则 403
                     （浏览器跨站伪造场景；curl/Agent 不带 Origin 时放行）
    L3 Token 鉴权    /api/* 与 /mcp 必须 Bearer token 或 X-RepoLucent-Token 头；
                     token 由 secrets.token_urlsafe(24) 生成，可用环境变量
                     REPOLUCENT_TOKEN 固定（供 MCP 客户端配置复用）；恒时比较。
                     显式设置 REPOLUCENT_TOKEN=""（空串）= 退回无鉴权模式（仅本机自担风险，
                     启动横幅红色警告）。
    L4 最小豁免      仅 GET /（UI 壳，服务端把 token 注入页面 JS 上下文）与
                     /static/*（保留目录穿越防护）免 token；其余全量过 L1~L3。
    token 注入原理：恶意页面可发起对 127.0.0.1 的请求但读不到响应体（同源策略），
    token 只存在于 UI 壳响应体里，攻击者拿不到；DNS rebinding 又被 L1 封死。两层互为冗余。

路由实现（v1.5.1 重构）：@route 注册表替代历史 106 分支 if/elif——
每个端点一个独立函数（含函数内 import 不再遮蔽外层名字），do_GET/do_POST 收口为
「门控 → 查表 → 调用 → 统一序列化/异常归一化」。新增端点 = 新函数 + 一行装饰器。

API 一览（Agent 可直接用 HTTP 调用；除 / 与 /static/* 外需 token）：
    GET  /api/state                    仓库与基线状态（不分析）
    POST /api/analyze                  全量分析并落盘（body: {module?, plugin?}）
    GET  /api/summary                  跑一次分析，返回指标字典（不落盘）
    GET  /api/report                   返回最新 HTML 报告
    POST /api/snapshot                 生成基线快照（body: {name?}）
    GET  /api/history                  列出已有基线
    GET  /api/diff?baseline=NAME       与基线对比（&format=json|md）
    POST /api/query                    结构化查询（body: {scope,select,where,format}）
    POST /api/ai                       输出 AI 上下文（body: {target?, depth?}）

    Git 工作流 API：
    GET  /api/git/log?since=90         提交历史扫描
    GET  /api/git/remote-diff          远程差异对比
    GET  /api/git/untracked            未跟踪文件检测
    POST /api/git/push                 智能推送（body: {remote?, branch?, confirm?}）
    POST /api/git/pull                 智能拉取（body: {remote?, branch?, rebase?, confirm?}）
    POST /api/git/sync                 多仓库同步（body: {group, direction?, confirm?}）
    GET  /api/git/ci-check             CI/CD 配置检查
    GET  /api/git/groups               列出仓库组

    脚本资产库 API（读多写慎）：
    GET  /api/scripts                  列出脚本库（registry 元数据，只读）
    GET  /api/scripts/<id>             单条脚本完整元数据
    POST /api/scripts/run              受控运行（body: {id, argv?, confirm?}）
    GET  /api/scripts/doctor[?id=]     脚本自检（import + main() 入口检查）
    GET  /api/scripts/manual           生成脚本库使用手册（text/markdown）
    POST /api/scripts/baseline         保存脚本库基线（body: {name?}）
    GET  /api/scripts/baseline-diff?name=NAME   与基线对比脚本库变更

    代码审计 API（只读分析 serve 当前仓库）：
    GET  /api/audit                    审计规则目录 + 上次结论缓存
    GET  /api/audit/semantic-prompt    导出语义审计任务书
    POST /api/audit/run                受控审计（body: {confirm, dims?, ...}）

    配置 / 仓库组 / 批量 / outbound MCP API：
    GET  /api/settings                 展示合并后配置与生效 profile（密钥类值一律掩码）
    POST /api/git/groups/add           向组添加仓库（body: {group, repo, ...}）
    POST /api/git/groups/remove        从组移除仓库（body: {group, repo}）
    POST /api/batch                    批量只读命令于仓库组
    GET  /api/mcp-out/servers          列出已配置的外部 MCP Server
    GET  /api/mcp-out/tools[?server=]  发现外部 MCP 工具
    POST /api/mcp-out/call             调用外部工具（body: {server, tool, args?, confirm?}）
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import TOOL_VERSION
from .config import (RepoConfig, ToolConfig, apply_date_dir,
                     latest_artifact_dir, list_artifact_dirs)
from .report_html import render_html
from .report_ai import render_ai_context
from .snapshot import (build_snapshot, save_snapshot, load_snapshot,
                       list_baselines, diff_snapshots, render_diff_md, default_name)
from .query import run_query, parse_where, infer_scope, SCOPES, QueryError
from .git_log_analyzer import scan_commits, format_commit_table
from .remote_diff import analyze_remote_diff, format_remote_diff_table
from .untracked_scanner import scan_untracked, format_untracked_table
from .packs.gitflow.git_push import smart_push
from .packs.gitflow.git_pull import smart_pull
from .packs.gitflow.multi_remote_sync import sync_repos, format_sync_results
from .ci_analyzer import analyze_ci_config, format_ci_findings
from .packs.gitflow.repo_group import load_groups

_MAX_BODY = 64 * 1024

# ---------------------------------------------------------------- 安全常量 ----
#: L1/L2 本地白名单（Host 头主机名 / Origin 主机名都按此校验）
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: UI 壳 token 注入占位符。token 字符集为 [A-Za-z0-9_-]，与占位符无碰撞可能。
_TOKEN_PLACEHOLDER = "__REPOLUCENT_TOKEN__"
_VERSION_PLACEHOLDER = "__REPOLUCENT_VERSION__"


def resolve_auth_token() -> tuple[str, bool]:
    """解析控制台访问令牌。返回 (token, degraded)。

    - 环境变量 REPOLUCENT_TOKEN 非空 → 固定 token（MCP 客户端可配置复用）；
    - 显式设置 REPOLUCENT_TOKEN=""（空串）→ 降级为无鉴权模式（degraded=True，
      仅本机自担风险，启动横幅红色警告）；
    - 未设置 → secrets.token_urlsafe(24) 随机生成（默认）。
    """
    env = os.environ.get("REPOLUCENT_TOKEN")
    if env is None:
        return secrets.token_urlsafe(24), False
    if env == "":
        return "", True
    return env, False


# ---------------------------------------------------------------- 路由注册表 ----
#: 精确路由：(method, path) -> handler
_ROUTES: dict[tuple[str, str], callable] = {}
#: 前缀路由（/static/、动态段）；精确匹配优先于前缀匹配
_ROUTE_PREFIX: list[tuple[str, str, callable]] = []


def route(method: str, path: str, prefix: bool = False):
    """端点注册装饰器。handler 签名：fn(handler, q, body) -> (body, ctype[, status])。

    - q 为 parse_qs 后的查询参数 dict；
    - body 为 POST 请求体解析后的 dict（GET 恒为 {}）；
    - 返回值直接 self._send(*res)：2 元组按 200 处理，3 元组带状态码。
    """
    def deco(fn):
        if prefix:
            _ROUTE_PREFIX.append((method, path, fn))
        else:
            _ROUTES[(method, path)] = fn
        return fn
    return deco


def _match_prefix(method: str, path: str):
    """按注册顺序做前缀匹配（精确路由查不到时兜底）。"""
    for m, pfx, fn in _ROUTE_PREFIX:
        if m == method and path.startswith(pfx):
            return fn
    return None


# P1a UI 侧栏化：控制台静态资源外置到 repo_lucent/ui/static，由 /static 服务。
# 不再把整页 HTML/CSS/JS 塞进 Python 字符串（_UI_PAGE 缩为壳层骨架）。
UI_STATIC_DIR = Path(__file__).resolve().parent / "ui" / "static"
_STATIC_MIME = {".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
                ".json": "application/json; charset=utf-8",
                ".png": "image/png",
                ".woff2": "font/woff2"}


def _serve_static(rel_path: str) -> tuple:
    """安全服务 /static/<rel_path>：解析后必须仍在 UI_STATIC_DIR 内，杜绝目录穿越。

    返回 (body, ctype[, status]) 三元组，由路由分发器统一发送（避免双重响应）。
    """
    root = UI_STATIC_DIR.resolve()
    try:
        target = (root / rel_path).resolve()
    except (OSError, ValueError):
        return _err(400, "BadRequest", "非法静态资源路径")
    # 穿越防护：target 必须等于 root 或位于其下（Python 3.9+ 可用 is_relative_to）
    if not (target == root or str(target).startswith(str(root) + os.sep)):
        return _err(403, "Forbidden", "禁止访问该路径")
    if not target.is_file():
        return _err(404, "NotFound", f"静态资源不存在：{rel_path}")
    ctype = _STATIC_MIME.get(target.suffix.lower(), "application/octet-stream")
    return (target.read_bytes(), ctype)


def _ok(payload: dict | str, ctype: str = "application/json; charset=utf-8"):
    if isinstance(payload, str):
        return (payload.encode("utf-8"), ctype)
    return (json.dumps(payload, ensure_ascii=False).encode("utf-8"), ctype)


def _err(status: int, etype: str, message: str):
    """返回与 _send(body, ctype, status) 形参顺序一致的错误三元组。"""
    body = json.dumps({"error": {"type": etype, "message": str(message)}},
                      ensure_ascii=False).encode("utf-8")
    return (body, "application/json; charset=utf-8", status)


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    raw = handler.rfile.read(min(int(handler.headers.get("Content-Length") or 0),
                                 _MAX_BODY))
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("请求体不是合法 JSON")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data


class _Handler(BaseHTTPRequestHandler):
    server_version = f"RepoLucent/{TOOL_VERSION}"
    state: dict = {}
    # P0 §4.2：ThreadingHTTPServer 多线程下，跨请求的共享 state 写需加锁，避免脏读竞态。
    state_lock = threading.Lock()

    # ------------------------------------------------------------- 分发 ----
    def do_GET(self):  # noqa: N802
        if not self._guard():
            return
        try:
            u = urlparse(self.path)
            fn = _ROUTES.get(("GET", u.path)) or _match_prefix("GET", u.path)
            if fn is None:
                self._send(*_err(404, "NotFound", f"未知路径：{u.path}"))
                return
            self._send(*fn(self, parse_qs(u.query), {}))
        except ValueError as e:
            self._send(*_err(400, "BadRequest", e))
        except (BrokenPipeError, ConnectionResetError):
            pass                       # 客户端提前断开：无需回错误页
        except Exception as e:  # 本地工具：错误也要可读，不裸堆栈
            self._send(*_err(500, type(e).__name__, e))

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        try:
            body = _read_body(self)
        except ValueError as e:
            self._send(*_err(400, "BadRequest", e))
            return
        try:
            u = urlparse(self.path)
            fn = _ROUTES.get(("POST", u.path)) or _match_prefix("POST", u.path)
            if fn is None:
                self._send(*_err(404, "NotFound", f"未知路径：{u.path}"))
                return
            self._send(*fn(self, parse_qs(u.query), body))
        except ValueError as e:
            self._send(*_err(400, "BadRequest", e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(*_err(500, type(e).__name__, e))

    def _send(self, body, ctype: str, status: int = 200):
        if isinstance(body, str):        # 统一容错：str 自动按 UTF-8 编码
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- 门控 ----
    def _guard(self) -> bool:
        """四层递进门控（L1 Host → L2 Origin → L3 Token；L4 豁免收口在 L3 判断内）。"""
        # ---- L1 Host 白名单（DNS rebinding 防线）----
        raw_host = (self.headers.get("Host") or "").strip().lower()
        if raw_host.startswith("["):               # [::1]:8788 形态
            host = raw_host[1:raw_host.find("]") if "]" in raw_host else None]
        elif raw_host.count(":") == 1:             # host:port（IPv4/域名）
            host = raw_host.rsplit(":", 1)[0]
        else:                                      # 无端口或裸 IPv6
            host = raw_host
        if host not in _LOCAL_HOSTS:
            self._send(*_err(403, "BadHost", "Host header not allowed"))
            return False
        # ---- L2 Origin 校验（仅 POST；无 Origin 头放行——curl/Agent 不带）----
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin:
                try:
                    oh = urlparse(origin).hostname or ""
                except ValueError:
                    oh = ""
                if oh.lower() not in _LOCAL_HOSTS:
                    self._send(*_err(403, "BadOrigin", "Cross-origin POST blocked"))
                    return False
        # ---- L3 Token 鉴权（豁免面最小化：仅首页与静态资源免 token）----
        path = urlparse(self.path).path
        needs_token = path.startswith("/api/") or path.rstrip("/") == "/mcp"
        if needs_token and self.server.auth_token:
            tok = (self.headers.get("Authorization") or "")
            if tok.startswith("Bearer "):
                tok = tok[len("Bearer "):]
            tok = tok.strip() or (self.headers.get("X-RepoLucent-Token") or "").strip()
            if not secrets.compare_digest(tok, self.server.auth_token):
                self._send(*_err(401, "Unauthorized", "token required"))
                return False
        return True


# ---------------------------------------------------------------- 端点：UI 壳 ----

@route("GET", "/")
def _ui_index(h, q, body):
    """UI 壳：服务端把 token 注入页面 JS 上下文（同源策略保证攻击者读不到）。"""
    page = (_UI_PAGE
            .replace(_TOKEN_PLACEHOLDER, h.server.auth_token)
            .replace(_VERSION_PLACEHOLDER, TOOL_VERSION))
    return (page.encode("utf-8"), "text/html; charset=utf-8")


@route("GET", "/static/", prefix=True)
def _ui_static(h, q, body):
    return _serve_static(urlparse(h.path).path[len("/static/"):])


# ---------------------------------------------------------------- 端点：核心分析 ----

@route("GET", "/api/state")
def _api_state(h, q, body):
    cfg = h.state["cfg"]
    fe = h.state["frontends"]
    out = cfg.out_dir
    report = out / "repo_lucent_report.html"
    return _ok({
        "tool_version": TOOL_VERSION,
        "repo": cfg.repo_root.name,
        "repo_path": str(cfg.repo_root),
        "frontends": [{"root": f["root"], "path": f["path"]} for f in fe],
        "out_dir": str(out),
        "artifact_root": str(cfg.stable_out_dir or out),
        "baselines": list_baselines(cfg.snapshot_dir),
        "report_exists": report.exists(),
        "report_mtime": (report.stat().st_mtime
                         if report.exists() else None),
    })


@route("GET", "/api/summary")
def _api_summary(h, q, body):
    data, duration, _ = h.state["analyzer"](None)
    return _ok(h.state["summarize"](data, duration))


@route("GET", "/api/data")
def _api_data(h, q, body):
    data_file = h.state["cfg"].out_dir / "repo_lucent.json"
    if not data_file.exists():
        return _err(404, "NotFound",
                    "分析结果尚未生成：先点「运行完整分析」或 POST /api/analyze")
    return _ok(json.loads(data_file.read_text(encoding="utf-8")))


@route("GET", "/api/report")
def _api_report(h, q, body):
    report = h.state["cfg"].out_dir / "repo_lucent_report.html"
    if not report.exists():
        return _err(404, "NotFound", "报告尚未生成，先 POST /api/analyze")
    return (report.read_bytes(), "text/html; charset=utf-8")


@route("GET", "/api/history")
def _api_history(h, q, body):
    return _ok({"baselines": list_baselines(h.state["cfg"].snapshot_dir)})


@route("GET", "/api/artifacts")
def _api_artifacts(h, q, body):
    # 产物按日期归档（DONE-15）：列出历史产物目录，供 UI 回溯/对比
    cfg = h.state["cfg"]
    base = cfg.stable_out_dir or cfg.out_dir
    latest = latest_artifact_dir(base)
    return _ok({
        "artifact_root": str(base),
        "current": str(cfg.out_dir),
        "latest": str(latest) if latest else None,
        "runs": list_artifact_dirs(base),
    })


@route("GET", "/api/diff")
def _api_diff(h, q, body):
    baseline = (q.get("baseline") or [""])[0]
    fmt = (q.get("format") or ["json"])[0]
    if not baseline:
        return _err(400, "BadRequest", "缺少 baseline 参数")
    try:
        base_name, base = load_snapshot(baseline, h.state["cfg"].snapshot_dir)
    except (FileNotFoundError, ValueError) as e:
        return _err(404, "NotFound", e)
    data, _, _ = h.state["analyzer"](None)
    result = diff_snapshots(base, build_snapshot(data), top=10)
    if fmt == "md":
        return (render_diff_md(result, base_name), "text/markdown; charset=utf-8")
    return _ok({"baseline": base_name, "current": "current", "diff": result})


@route("POST", "/api/analyze")
def _api_analyze(h, q, body):
    from .cli import invalidate_analysis_cache
    module = body.get("module")
    plugin = body.get("plugin")
    cfg = h.state["cfg"]
    cfg.target = module or plugin
    cfg.target_type = "module" if module else ("plugin" if plugin else None)
    invalidate_analysis_cache()        # 显式「重新分析」：清空 memo，保证本次为全新计算
    written = h.state["write_reports"](cfg)
    data = h.state["last_data"]
    dur = h.state["last_duration"]
    return _ok({
        "written": [str(p) for p in written],
        "summary": h.state["summarize"](data, dur),
    })


@route("POST", "/api/snapshot")
def _api_snapshot(h, q, body):
    cfg = h.state["cfg"]
    data, _, _ = h.state["analyzer"](None)
    snap = build_snapshot(data)
    name = body.get("name") or default_name()
    path = save_snapshot(cfg.snapshot_dir, name, snap)
    return _ok({"name": path.stem, "path": str(path),
                "plugins": len(snap["plugins"]),
                "lines_code": snap["totals"]["lines_code"]})


@route("POST", "/api/query")
def _api_query(h, q, body):
    scope = body.get("scope") or infer_scope(
        [s.strip() for s in str(body.get("select", "")).split(",") if s.strip()])
    if scope not in SCOPES:
        raise QueryError(f"未知查询范围：{scope}（可选 {', '.join(SCOPES)}）")
    select = str(body.get("select", "")).strip() or None
    where = str(body.get("where", "")).strip() or None
    fmt = body.get("format", "json")
    data, _, _ = h.state["analyzer"](None)
    result = run_query(data, scope,
                       [s.strip() for s in select.split(",")]
                       if select else None, where)
    if fmt == "table":
        from .query import render_table
        return (render_table(result), "text/plain; charset=utf-8")
    return _ok(result)


@route("POST", "/api/gate")
def _api_gate(h, q, body):
    from .gate import evaluate as gate_evaluate
    data, _, _ = h.state["analyzer"](None)
    results = gate_evaluate(body.get("items", "all"), data,
                            h.state["cfg"],
                            int(body.get("max_file_lines", 2000)))
    return _ok({"results": [r.to_dict() for r in results]})


@route("POST", "/api/ai")
def _api_ai(h, q, body):
    data, _, _ = h.state["analyzer"](None)
    cfg = h.state["cfg"]
    target = body.get("target")
    depth = body.get("depth", "brief")
    if target:
        from .report_ai import render_target_context
        from .deep_analyzer import analyze_target
        from .cli import _resolve_target
        parsed = _resolve_target(data, target)
        if parsed is None:
            return _err(404, "NotFound", f"找不到目标：{target}")
        ttype, name = parsed
        dd = analyze_target(cfg, ttype, name,
                            h.state["last_cache"], data["plugins"])
        return (render_target_context(data, dd, depth=depth),
                "text/markdown; charset=utf-8")
    return (render_ai_context(data, cfg.max_plugins_in_ai_context),
            "text/markdown; charset=utf-8")


# ---------------------------------------------------------------- 端点：Git 工作流 ----

@route("GET", "/api/git/log")
def _api_git_log(h, q, body):
    since = int((q.get("since") or ["90"])[0])
    author = (q.get("author") or [None])[0]
    data = scan_commits(h.state["cfg"].repo_root, since_days=since, author=author)
    return _ok(data)


@route("GET", "/api/git/remote-diff")
def _api_git_remote_diff(h, q, body):
    remote = (q.get("remote") or ["origin"])[0]
    branch = (q.get("branch") or [None])[0]
    data = analyze_remote_diff(h.state["cfg"].repo_root, remote=remote, branch=branch)
    return _ok(data)


@route("GET", "/api/git/untracked")
def _api_git_untracked(h, q, body):
    return _ok(scan_untracked(h.state["cfg"].repo_root))


@route("GET", "/api/git/ci-check")
def _api_git_ci_check(h, q, body):
    platform = (q.get("platform") or ["auto"])[0]
    data = analyze_ci_config(h.state["cfg"].repo_root, platform=platform)
    return _ok(data)


@route("GET", "/api/git/groups")
def _api_git_groups(h, q, body):
    groups = load_groups()
    result = {}
    for name, group in groups.items():
        result[name] = {
            "name": group.name,
            "repos": [{"path": str(r.path), "remotes": r.remotes} for r in group.repos],
        }
    return _ok({"groups": result})


@route("POST", "/api/git/push")
def _api_git_push(h, q, body):
    remote = body.get("remote", "origin")
    branch = body.get("branch")
    confirm = body.get("confirm", False)
    result = smart_push(
        h.state["cfg"].repo_root,
        remote=remote, branch=branch,
        dry_run=not confirm, confirm=confirm,
    )
    return _ok(result)


@route("POST", "/api/git/pull")
def _api_git_pull(h, q, body):
    from .cli import invalidate_analysis_cache
    remote = body.get("remote", "origin")
    branch = body.get("branch")
    rebase = body.get("rebase", False)
    confirm = body.get("confirm", False)
    if confirm:
        invalidate_analysis_cache()    # 拉取会改动本地源 → 失效分析缓存
    result = smart_pull(
        h.state["cfg"].repo_root,
        remote=remote, branch=branch,
        dry_run=not confirm, rebase=rebase, confirm=confirm,
    )
    return _ok(result)


@route("POST", "/api/git/sync")
def _api_git_sync(h, q, body):
    from .cli import invalidate_analysis_cache
    group = body.get("group")
    if not group:
        return _err(400, "BadRequest", "缺少 group 参数")
    direction = body.get("direction", "bidirectional")
    confirm = body.get("confirm", False)
    if confirm:
        invalidate_analysis_cache()    # 同步会改动本地源 → 失效分析缓存
    results = sync_repos(
        group_name=group,
        direction=direction,
        dry_run=not confirm, confirm=confirm,
    )
    return _ok({"results": results})


@route("POST", "/api/git/groups/add")
def _api_git_groups_add(h, q, body):
    from .packs.gitflow.repo_group import add_repo_to_group
    group = str(body.get("group") or "")
    repo = str(body.get("repo") or "")
    if not group or not repo:
        return _err(400, "BadRequest", "缺少 group 或 repo 参数")
    repo_path = Path(repo).expanduser().resolve()
    remotes = {}
    if body.get("remote_github"):
        remotes["github"] = str(body["remote_github"])
    if body.get("remote_gitee"):
        remotes["gitee"] = str(body["remote_gitee"])
    confirm = bool(body.get("confirm", False))
    if not confirm:
        return _ok({"ok": True, "dry_run": True,
                    "action": "groups.add", "group": group,
                    "repo": str(repo_path), "remotes": remotes,
                    "hint": "传 confirm=true 才写入组配置"})
    add_repo_to_group(group, repo_path, remotes)
    from .cli import invalidate_analysis_cache
    invalidate_analysis_cache()          # 组配置变更：让 groups 查询即时可见
    return _ok({"ok": True, "group": group, "repo": str(repo_path),
                "remotes": remotes})


@route("POST", "/api/git/groups/remove")
def _api_git_groups_remove(h, q, body):
    from .packs.gitflow.repo_group import remove_repo_from_group
    group = str(body.get("group") or "")
    repo = str(body.get("repo") or "")
    if not group or not repo:
        return _err(400, "BadRequest", "缺少 group 或 repo 参数")
    repo_path = Path(repo).expanduser().resolve()
    confirm = bool(body.get("confirm", False))
    if not confirm:
        return _ok({"ok": True, "dry_run": True,
                    "action": "groups.remove", "group": group, "repo": str(repo_path),
                    "hint": "传 confirm=true 才写入组配置"})
    ok = remove_repo_from_group(group, repo_path)
    if not ok:
        return _err(404, "NotFound", f"仓库不在组 '{group}' 中：{repo_path}")
    return _ok({"ok": True, "group": group, "repo": str(repo_path)})


@route("POST", "/api/batch")
def _api_batch(h, q, body):
    # 批量只读命令于仓库组（对应 CLI batch；log/remote-diff/untracked 均只读，无需 confirm）
    group_name = str(body.get("group") or "")
    command = body.get("command")
    if command not in ("log", "remote-diff", "untracked"):
        return _err(400, "BadRequest",
                    "command 须为 log|remote-diff|untracked（只读命令集）")
    groups = load_groups()
    if group_name not in groups:
        return _err(404, "NotFound", f"仓库组不存在：{group_name}")
    since = int(body.get("since", 90))
    remote = body.get("remote", "origin")
    branch = body.get("branch")
    results = []
    for repo in groups[group_name].repos:
        if not repo.path.exists():
            results.append({"repo": str(repo.path), "error": "路径不存在"})
            continue
        if command == "log":
            data = scan_commits(repo.path, since_days=since)
        elif command == "remote-diff":
            data = analyze_remote_diff(repo.path, remote=remote, branch=branch)
        else:
            data = scan_untracked(repo.path)
        results.append({"repo": str(repo.path), "data": data})
    return _ok({"group": group_name, "command": command,
                "count": len(results), "results": results})


# ---------------------------------------------------------------- 端点：脚本资产库 ----

@route("GET", "/api/scripts")
def _api_scripts(h, q, body):
    from .script_cmd import _load_registry
    reg = _load_registry()
    items = [{
        "id": s["id"], "name": s.get("name"), "category": s.get("category"),
        "kind": s.get("kind"), "status": s.get("status"), "version": s.get("version"),
        "summary": s.get("summary"), "usage": s.get("usage"),
    } for s in reg.get("scripts", [])]
    return _ok({"registry_version": reg.get("registry_version"),
                "count": len(items), "scripts": items})


@route("GET", "/api/scripts/doctor")
def _api_scripts_doctor(h, q, body):
    # 脚本自检（对应 CLI script doctor）：import + main() 入口检查
    from .script_cmd import _load_registry
    import importlib
    only = (q.get("id") or [None])[0]
    reg = _load_registry()
    results, checked = [], 0
    for s in reg.get("scripts", []):
        if only and s["id"] != only:
            continue
        if not s.get("entry"):
            continue
        checked += 1
        try:
            mod = importlib.import_module(s["entry"])
            ok = callable(getattr(mod, "main", None))
            results.append({"id": s["id"], "ok": ok,
                            "error": None if ok else "无 main(argv) 入口"})
        except Exception as e:  # noqa: BLE001
            results.append({"id": s["id"], "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
    return _ok({"checked": checked, "results": results})


@route("GET", "/api/scripts/manual")
def _api_scripts_manual(h, q, body):
    from .script_cmd import _load_registry, _render_manual
    return (_render_manual(_load_registry()), "text/markdown; charset=utf-8")


@route("GET", "/api/scripts/baseline-diff")
def _api_scripts_baseline_diff(h, q, body):
    from .script_cmd import (_load_registry, _fingerprint,
                             _load_script_baseline)
    name = (q.get("name") or [""])[0]
    if not name:
        return _err(400, "BadRequest", "缺少 name 参数")
    out = h.state["cfg"].out_dir
    base = _load_script_baseline(name, out)
    if base is None:
        return _err(404, "NotFound", f"找不到脚本库基线：{name}")
    cur = _fingerprint(_load_registry())
    b = base.get("fingerprint", {})
    changed = [{"id": k, "before": b[k], "after": cur[k]}
               for k in sorted(set(cur) & set(b)) if cur[k] != b[k]]
    return _ok({
        "baseline": name,
        "added": sorted(set(cur) - set(b)),
        "removed": sorted(set(b) - set(cur)),
        "changed": changed,
    })


@route("GET", "/api/scripts/", prefix=True)
def _api_script_one(h, q, body):
    # 单条脚本元数据（精确路由 doctor/manual/baseline-diff 未命中时兜底）
    from .script_cmd import _load_registry, _index
    sid = urlparse(h.path).path[len("/api/scripts/"):].strip("/")
    s = _index(_load_registry()).get(sid)
    if not s:
        return _err(404, "NotFound", f"未知脚本 id：{sid}")
    return _ok(s)


@route("POST", "/api/scripts/run")
def _api_scripts_run(h, q, body):
    from .script_cmd import run_script_api
    from .cli import invalidate_analysis_cache
    confirm = bool(body.get("confirm", False))
    if confirm:
        invalidate_analysis_cache()    # 受控脚本可能改动本地源 → 失效分析缓存
    result = run_script_api(
        body.get("id"),
        argv=body.get("argv") or [],
        confirm=confirm,
    )
    return _ok(result)


@route("POST", "/api/scripts/baseline")
def _api_scripts_baseline(h, q, body):
    # 脚本库基线（对应 CLI script snapshot）：写 out/history/scripts/<name>.json
    from .script_cmd import _load_registry, _fingerprint
    from . import snapshot as S
    name = str(body.get("name") or S.default_name())
    reg = _load_registry()
    payload = {"kind": "script_registry", "script_meta_version":
               reg.get("meta_schema", {}).get("version"), "fingerprint": _fingerprint(reg)}
    d = S.history_dir(h.state["cfg"].out_dir) / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / (S._safe_name(name) + ".json")
    fp.write_text(S._dumps(payload), encoding="utf-8", newline="\n")
    return _ok({"path": str(fp), "count": len(payload["fingerprint"])})


# ---------------------------------------------------------------- 端点：代码审计 ----

@route("GET", "/api/audit")
def _api_audit(h, q, body):
    from .script_cmd import _load_registry
    reg = _load_registry()
    rules = reg.get("audit_rules", [])
    return _ok({
        "rules": [{"rule_id": r["rule_id"], "dimension": r.get("dimension"),
                   "severity": r.get("severity"), "coverage": r.get("coverage"),
                   "provider": r.get("provider"), "title": r.get("title")} for r in rules],
        "last": h.state.get("last_audit"),  # 读取与写共用 state_lock 保护
    })


@route("GET", "/api/audit/semantic-prompt")
def _api_audit_semantic_prompt(h, q, body):
    from .audit import semantic as SEM
    from .audit import engine as _AE
    data = h.state["analyzer"](None)[0]
    prompt = SEM.build_prompt(h.state["cfg"], data, list(_AE.DEFAULT_DIMS))
    return (prompt.encode("utf-8"), "text/markdown; charset=utf-8")


@route("POST", "/api/audit/run")
def _api_audit_run(h, q, body):
    from .audit import engine as AE
    cfg = h.state["cfg"]
    dims = body.get("dims") or None
    fail_on = body.get("fail_on_severity", "blocking")
    confirm = bool(body.get("confirm", False))
    with_llm = bool(body.get("with_llm", False))
    if not confirm:
        return _ok({
            "ok": True, "dry_run": True,
            "target": str(cfg.repo_root),
            "dims": dims or list(AE.DEFAULT_DIMS),
            "fail_on_severity": fail_on,
            "with_llm": with_llm,
            "hint": "审计会跑完整分析（数秒）并只读源仓；传 confirm=true 才执行",
        })
    data = h.state["analyzer"](None)[0]   # (data, duration, parse_cache)
    sem_findings = None
    if body.get("semantic_results") is not None:
        from .audit import semantic as SEM
        try:
            sem_findings, _rej = SEM.load_items(body["semantic_results"])
        except Exception as e:  # noqa: BLE001
            return _err(400, "BadRequest", f"semantic_results 非法：{e}")
    use_semantic = bool(body.get("with_semantic", False)) or sem_findings is not None
    llm_channel = None
    if with_llm:
        # P2：内置 LLM 语义通道（与 CLI audit --with-llm 同构）。
        # 任何失败都降级为 llm_channel.ran=false，绝不影响审计主流程。
        from .llm import runner as LR
        llm_run = LR.run_semantic_audit(
            cfg, data, dims or list(AE.DEFAULT_DIMS),
            model_name=body.get("llm_model"),
            use_tools=not bool(body.get("llm_no_tools", False)))
        llm_channel = {"ran": llm_run.ran, "model": llm_run.model,
                       "findings": len(llm_run.findings),
                       "tool_calls": llm_run.tool_calls,
                       "reason": None if llm_run.ran else llm_run.reason}
        if llm_run.ran:
            sem_findings = list(sem_findings or []) + llm_run.findings
            use_semantic = True
    rep = AE.run_audit(cfg, data, cfg.repo_root, dims=dims,
                       fail_on_severity=fail_on,
                       use_semantic=use_semantic,
                       semantic_findings=sem_findings,
                       semantic_can_block=bool(body.get("semantic_can_block", False)))
    rd = rep.to_dict()
    if llm_channel is not None:
        rd["llm_channel"] = llm_channel
    with _Handler.state_lock:
        h.state["last_audit"] = {"verdict": rd["verdict"], "summary": rd["summary"],
                                 "coverage": rd["coverage_matrix"]}
    return _ok(rd)


# ---------------------------------------------------------------- 端点：配置 / 仓库组 ----

@route("GET", "/api/settings")
def _api_settings(h, q, body):
    # 配置展示（OPEN-D）：合并后 settings + 生效 profile。密钥类值一律掩码。
    from .settings import load_settings, get_profile, _search_paths, _TOOL_ROOT as _SETTINGS_TOOL_ROOT
    raw = load_settings()

    def _mask(d):
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out[k] = _mask(v)
            elif (any(w in k.lower() for w in ("key", "token", "secret", "password"))
                  and isinstance(v, str) and v
                  and not k.lower().endswith("_env")):  # *_env 只是环境变量名，非密钥本体
                out[k] = "***masked***"
            else:
                out[k] = v
        return out

    hits = [str(p) for p in _search_paths() if p.is_file()]
    # 各模型密钥环境变量的「已设置/未设置」状态（只回布尔，不回值）
    env_status = {}
    for m in (raw.get("models") or []):
        if isinstance(m, dict) and m.get("name"):
            env_status[m["name"]] = {
                "key_env": m.get("key_env"),
                "key_set": bool(m.get("key_env") and os.environ.get(m["key_env"])),
                "base_url_env": m.get("base_url_env"),
                "base_url_set": bool(m.get("base_url_env") and os.environ.get(m["base_url_env"])),
            }
    return _ok({
        "tool_version": TOOL_VERSION,
        "config": _mask(raw),
        "profile": get_profile(),
        "profile_name": str(raw.get("profile", {}).get("name") or "verorun"),
        "loaded_files": hits,
        "env_status": env_status,
        "project_settings_path": str(_SETTINGS_TOOL_ROOT / "settings.json"),
    })


@route("GET", "/api/repos")
def _api_repos(h, q, body):
    # 多仓库注册表（只读）：注册项 + 可用 profile 预设 + 当前仓库名
    from . import repo_registry as RR
    from .settings import get_profile as _gp
    cur = h.state["cfg"]
    prof = _gp()
    return _ok({
        "repos": RR.list_repos(),
        "profiles": RR.list_profiles(),
        "current": {"name": getattr(cur, "repo_name", None) or cur.repo_root.name,
                    "path": str(cur.repo_root),
                    "profile": str(prof.get("name") or "verorun")},
    })


@route("POST", "/api/repos/add")
def _api_repos_add(h, q, body):
    from .repo_registry import add_repo as _add
    name = str(body.get("name") or "")
    path = str(body.get("path") or "")
    profile = body.get("profile") or None
    if not name or not path:
        return _err(400, "BadRequest", "缺少 name 或 path 参数")
    confirm = bool(body.get("confirm", False))
    if not confirm:
        return _ok({"ok": True, "dry_run": True, "action": "repos.add",
                    "name": name, "path": path, "profile": profile,
                    "hint": "传 confirm=true 才写入注册表"})
    try:
        ent = _add(name, path, profile)
    except ValueError as e:
        return _err(400, "BadRequest", str(e))
    return _ok({"ok": True, "entry": ent})


@route("POST", "/api/repos/remove")
def _api_repos_remove(h, q, body):
    from .repo_registry import remove_repo as _rm
    name = str(body.get("name") or "")
    if not name:
        return _err(400, "BadRequest", "缺少 name 参数")
    confirm = bool(body.get("confirm", False))
    if not confirm:
        return _ok({"ok": True, "dry_run": True, "action": "repos.remove",
                    "name": name,
                    "hint": "传 confirm=true 才移除（不影响磁盘文件）"})
    ok = _rm(name)
    if not ok:
        return _err(404, "NotFound", f"注册表中无此仓库：{name}")
    return _ok({"ok": True, "name": name})


@route("POST", "/api/repos/switch")
def _api_repos_switch(h, q, body):
    # 多仓库切换：重建 cfg + 按仓 profile 覆盖 + 失效两侧分析缓存 + 重发现前端仓
    from .repo_registry import resolve as _resolve
    name = str(body.get("name") or "")
    ent = _resolve(name)
    if ent is None:
        return _err(404, "NotFound", f"注册表中无此仓库：{name}")
    repo_path = Path(ent["path"]).expanduser().resolve()
    if not repo_path.is_dir():
        return _err(400, "BadRequest", f"仓库路径不存在：{repo_path}")
    from .settings import set_profile_override
    effective = set_profile_override(ent.get("profile") or None)
    # 用模块限定访问 config：避免函数内 import 遮蔽外层名字（历史教训 DONE-14）。
    from . import config as _config_mod
    _config_mod.apply_profile()
    old_cfg = h.state["cfg"]
    # 稳定根 = <out>/<仓库名>（日期归档层之上），切换仓库时按名另起一根，
    # 再套用同一套日期归档规则，保证产物结构一致。
    base_out = (old_cfg.stable_out_dir or old_cfg.out_dir).parent / ent["name"]
    new_cfg = _config_mod.RepoConfig(repo_root=repo_path, out_dir=base_out)
    tc = _config_mod.ToolConfig.from_settings()
    new_cfg.max_tree_depth = tc.max_tree_depth
    new_cfg.max_plugins_in_ai_context = tc.max_plugins_in_ai_context
    _config_mod.apply_date_dir(new_cfg, base_out)
    try:
        from .cli import discover_frontend_repos, analyze_frontend
        frontends = [f for f in (analyze_frontend(new_cfg, r)
                                 for r in discover_frontend_repos(new_cfg)) if f]
    except Exception:  # noqa: BLE001 —— 前端发现失败不阻断切换
        frontends = []
    # 失效两侧缓存：cli memo（按 repo 身份）+ catalog 分析器（按 id(cfg)）
    from .cli import invalidate_analysis_cache
    invalidate_analysis_cache()
    from .mcp.catalog import invalidate_catalog_analysis
    invalidate_catalog_analysis(old_cfg)
    invalidate_catalog_analysis(new_cfg)
    with _Handler.state_lock:
        h.state["cfg"] = new_cfg
        h.state["frontends"] = frontends
        h.state["last_data"] = {}
        h.state["last_duration"] = 0
        h.state["last_cache"] = {}
    return _ok({"ok": True, "name": ent["name"], "path": str(repo_path),
                "profile": effective, "out_dir": str(new_cfg.out_dir),
                "frontends": len(frontends),
                "hint": "已切换；控制台刷新 dashboard 触发新一轮分析"})


# ------------------------------------------------- 端点：路径点选 + 项目分组 ----

@route("GET", "/api/fs/list")
def _api_fs_list(h, q, body):
    """目录浏览（只读）：路径点选的服务端基础。path 为空 → 起点（盘符+用户目录）。"""
    from .fs_browse import list_dir
    raw = (q.get("path") or [None])[0]
    try:
        return _ok(list_dir(raw))
    except ValueError as e:
        return _err(400, "BadRequest", str(e))


@route("GET", "/api/projects")
def _api_projects(h, q, body):
    """项目列表（含内嵌成员）。"""
    from . import project_registry as PR
    return _ok({"projects": PR.list_projects(),
                "path": str(PR.projects_path())})


@route("POST", "/api/projects/add")
def _api_projects_add(h, q, body):
    from . import project_registry as PR
    name = str(body.get("name") or "")
    if not name:
        return _err(400, "BadRequest", "缺少 name 参数")
    if not bool(body.get("confirm", False)):
        return _ok({"ok": True, "dry_run": True, "action": "projects.add",
                    "name": name, "hint": "传 confirm=true 才写入注册表"})
    try:
        ent = PR.add_project(name)
    except ValueError as e:
        return _err(400, "BadRequest", str(e))
    return _ok({"ok": True, "project": ent})


@route("POST", "/api/projects/remove")
def _api_projects_remove(h, q, body):
    from . import project_registry as PR
    name = str(body.get("name") or "")
    if not name:
        return _err(400, "BadRequest", "缺少 name 参数")
    if not bool(body.get("confirm", False)):
        return _ok({"ok": True, "dry_run": True, "action": "projects.remove",
                    "name": name, "hint": "传 confirm=true 才移除（不影响磁盘与分析产物）"})
    ok = PR.remove_project(name)
    if not ok:
        return _err(404, "NotFound", f"项目中无此项目：{name}")
    return _ok({"ok": True, "name": name})


@route("POST", "/api/projects/repos/add")
def _api_projects_repos_add(h, q, body):
    from . import project_registry as PR
    proj = str(body.get("project") or "")
    name = str(body.get("name") or "")
    path = str(body.get("path") or "")
    if not proj or not name or not path:
        return _err(400, "BadRequest", "缺少 project / name / path 参数")
    profile = body.get("profile") or None
    scopes = body.get("scopes") or []
    if not bool(body.get("confirm", False)):
        return _ok({"ok": True, "dry_run": True, "action": "projects.repos.add",
                    "project": proj, "name": name, "path": path,
                    "profile": profile, "scopes": scopes,
                    "hint": "传 confirm=true 才写入（scopes 会先校验是真实子目录）"})
    try:
        ent = PR.add_member(proj, name, path, profile, scopes)
    except ValueError as e:
        return _err(400, "BadRequest", str(e))
    return _ok({"ok": True, "member": ent})


@route("POST", "/api/projects/repos/remove")
def _api_projects_repos_remove(h, q, body):
    from . import project_registry as PR
    proj = str(body.get("project") or "")
    name = str(body.get("name") or "")
    if not proj or not name:
        return _err(400, "BadRequest", "缺少 project / name 参数")
    if not bool(body.get("confirm", False)):
        return _ok({"ok": True, "dry_run": True, "action": "projects.repos.remove",
                    "project": proj, "name": name,
                    "hint": "传 confirm=true 才移除（不影响磁盘与分析产物）"})
    ok = PR.remove_member(proj, name)
    if not ok:
        return _err(404, "NotFound", f"项目中无此成员：{name}")
    return _ok({"ok": True, "project": proj, "name": name})


def _project_artifact_summary(base: Path) -> dict | None:
    """读取某成员/子范围的最新产物摘要；缺失或损坏返回 None（状态=未分析）。"""
    from .config import latest_artifact_dir
    from .cli import _summary_pairs
    d = latest_artifact_dir(base)
    if d is None:
        return None
    f = d / "repo_lucent.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("overview"):
        return None
    dur = ((data.get("meta") or {}).get("duration_ms")) or 0
    return dict(_summary_pairs(data, dur))


@route("GET", "/api/projects/summary")
def _api_projects_summary(h, q, body):
    """项目聚合摘要（只读）：读已有产物，缺失标 not_analyzed，绝不触发分析。"""
    from . import project_registry as PR
    name = (q.get("name") or [""])[0]
    proj = PR.resolve_project(name)
    if proj is None:
        return _err(404, "NotFound", f"项目中无此项目：{name}")
    cfg = h.state["cfg"]
    out_root = ((cfg.stable_out_dir or cfg.out_dir).parent
                / "projects" / proj["name"])
    members = []
    for m in proj.get("repos") or []:
        mb = out_root / m["name"]
        s = _project_artifact_summary(mb)
        row = {"name": m["name"], "path": m["path"], "profile": m.get("profile"),
               "status": "analyzed" if s else "not_analyzed",
               "summary": s, "out_base": str(mb), "scopes": []}
        for sc in m.get("scopes") or []:
            ss = _project_artifact_summary(mb / sc)
            row["scopes"].append({"name": sc,
                                  "status": "analyzed" if ss else "not_analyzed",
                                  "summary": ss, "out_base": str(mb / sc)})
        members.append(row)
    return _ok({"project": proj["name"], "members": members})


@route("POST", "/api/projects/analyze")
def _api_projects_analyze(h, q, body):
    """分析项目的一个成员（或其子范围）：完整复用既有管线，产物落到项目命名空间。

    不触碰 h.state（当前 dashboard 指向的仓库不受影响）；按成员 profile 临时
    覆盖全局 settings（与 /api/repos/switch 同款），finally 恢复。子范围强制
    内置口径（目录视角，无插件识别），UI 侧应标注「目录口径」。
    """
    from types import SimpleNamespace
    from . import project_registry as PR
    from . import config as C
    from . import settings as S
    from .cli import _analyze, _write_reports, _summary_pairs
    pname = str(body.get("project") or "")
    mname = str(body.get("member") or "")
    proj = PR.resolve_project(pname)
    if proj is None:
        return _err(404, "NotFound", f"项目中无此项目：{pname}")
    m = PR.resolve_member(proj, mname)
    if m is None:
        return _err(404, "NotFound", f"项目中无此成员：{mname}")
    scope = str(body.get("scope") or "").strip() or None
    if scope:
        if scope not in (m.get("scopes") or []):
            return _err(400, "BadRequest",
                        f"成员未登记该子范围：{scope}（已登记 {m.get('scopes') or []}）")
        root = Path(m["path"]) / scope
        eff_profile = None            # 子范围：内置口径（目录视角）
        label = f"{mname}/{scope}"
    else:
        root = Path(m["path"])
        eff_profile = m.get("profile") or None
        label = mname
    if not root.is_dir():
        return _err(400, "BadRequest", f"分析目标不存在：{root}")
    only = str(body.get("only") or "json,md")
    cfg = h.state["cfg"]
    base = ((cfg.stable_out_dir or cfg.out_dir).parent / "projects"
            / proj["name"] / m["name"] / (scope or ""))
    prev_override = getattr(S, "_PROFILE_OVERRIDE", None)
    try:
        effective = S.set_profile_override(eff_profile)
        C.apply_profile()
        new_cfg = C.RepoConfig(repo_root=root, out_dir=base)
        tc = C.ToolConfig.from_settings()
        new_cfg.max_tree_depth = tc.max_tree_depth
        new_cfg.max_plugins_in_ai_context = tc.max_plugins_in_ai_context
        new_cfg = C.apply_date_dir(new_cfg, base)
        ns = SimpleNamespace(no_cache=False, deterministic=False)
        data, dur, parse_cache = _analyze(ns, new_cfg)
        written = _write_reports(new_cfg, data, only=only, parse_cache=parse_cache)
        summary = dict(_summary_pairs(data, dur))
    finally:
        S.set_profile_override(prev_override)
        C.apply_profile()
    return _ok({"ok": True, "target": label, "profile": effective,
                "out_dir": str(new_cfg.out_dir),
                "written": [str(p) for p in written], "summary": summary})


@route("POST", "/api/settings/llm")
def _api_settings_llm(h, q, body):
    # LLM 可视化配置写回（OPEN-D+）：只写 key_env 变量名，密钥本体永不落盘
    from .settings import _TOOL_ROOT
    proj = _TOOL_ROOT / "settings.json"
    llm_enabled = bool(body.get("llm_enabled", False))
    default_model = body.get("default_model") or None
    models_in = body.get("models")
    if models_in is not None:
        if not isinstance(models_in, list):
            return _err(400, "BadRequest", "models 必须是 JSON 数组")
        norm = []
        for m in models_in:
            if not isinstance(m, dict) or not m.get("name"):
                return _err(400, "BadRequest", "models[] 每项须为含 name 的对象")
            norm.append({
                "name": str(m["name"]),
                "provider": str(m.get("provider") or "openai_compat"),
                "model": str(m.get("model") or m["name"]),
                "key_env": str(m.get("key_env")) if m.get("key_env") else None,
                "base_url_env": str(m.get("base_url_env")) if m.get("base_url_env") else None,
            })
        models_in = norm
    confirm = bool(body.get("confirm", False))
    payload = {"llm_enabled": llm_enabled, "default_model": default_model,
               "models": models_in}
    if not confirm:
        return _ok({"ok": True, "dry_run": True, "target": str(proj),
                    "payload": payload,
                    "hint": "传 confirm=true 才写回项目级 settings.json（密钥仍只走环境变量）"})
    cur = {}
    if proj.is_file():
        try:
            cur = json.loads(proj.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 —— 损坏旧文件视为空，.bak 仍可抢救
            cur = {}
    if proj.is_file():
        proj.replace(proj.with_suffix(".json.bak"))   # 写前备份
        cur = {}
        try:
            cur = json.loads(proj.with_suffix(".json.bak").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    for k, v in payload.items():
        if v is not None or k == "llm_enabled":
            cur[k] = v
    tmp = proj.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(proj)
    # 立即失效 settings 相关缓存与生效视图
    from .cli import invalidate_analysis_cache
    invalidate_analysis_cache()
    from .mcp.catalog import invalidate_catalog_analysis
    invalidate_catalog_analysis(h.state["cfg"])
    return _ok({"ok": True, "written": str(proj),
                "backup": str(proj.with_suffix(".json.bak")),
                "llm_enabled": llm_enabled,
                "models": len(payload["models"] or [])})


# ---------------------------------------------------------------- 端点：outbound MCP ----

def _outbound_disabled():
    from .settings import is_outbound_mcp_enabled
    return not is_outbound_mcp_enabled()


@route("GET", "/api/mcp-out/servers")
def _api_mcp_out_servers(h, q, body):
    from .settings import mcp_servers
    if _outbound_disabled():
        return _err(404, "NotEnabled",
                    "outbound MCP 已关闭（settings: mcp_outbound_enabled=false）")
    items = [{"name": s.get("name"), "transport": s.get("transport", "stdio"),
              "enabled": bool(s.get("enabled", True)),
              "target": s.get("url")
              or " ".join([str(s.get("command") or ""),
                           *[str(a) for a in (s.get("args") or [])]]).strip()}
             for s in mcp_servers()]
    return _ok({"count": len(items), "servers": items})


@route("GET", "/api/mcp-out/tools")
def _api_mcp_out_tools(h, q, body):
    if _outbound_disabled():
        return _err(404, "NotEnabled",
                    "outbound MCP 已关闭（settings: mcp_outbound_enabled=false）")
    server = (q.get("server") or [None])[0]
    from .scriptlib.adapters.mcp import discover_tools
    tools, errors = discover_tools(server)
    return _ok({"tools": tools, "errors": errors})


@route("POST", "/api/mcp-out/call")
def _api_mcp_out_call(h, q, body):
    if _outbound_disabled():
        return _err(404, "NotEnabled",
                    "outbound MCP 已关闭（settings: mcp_outbound_enabled=false）")
    server, tool = str(body.get("server") or ""), str(body.get("tool") or "")
    if not server or not tool:
        return _err(400, "BadRequest", "缺少 server 或 tool 参数")
    args_in = body.get("args") or {}
    if not isinstance(args_in, dict):
        return _err(400, "BadRequest", "args 必须是 JSON 对象")
    confirm = bool(body.get("confirm", False))
    if not confirm:
        return _ok({"ok": True, "dry_run": True, "server": server,
                    "tool": tool, "arguments": args_in,
                    "hint": "传 confirm=true 才真正调用外部工具"})
    from .scriptlib.adapters.mcp import McpOutboundError, get_client
    try:
        r = get_client(server).call_tool(tool, args_in)
    except McpOutboundError as e:
        return _err(502, "OutboundError", f"外部 MCP 调用失败：{e}")
    except Exception as e:  # noqa: BLE001
        return _err(502, type(e).__name__, f"外部 MCP 调用异常：{e}")
    return _ok(r)


# ---------------------------------------------------------------- 端点：MCP 服务化 ----

@route("POST", "/mcp")
def _api_mcp(h, q, body):
    # P0 §4.1 配置外部化：受 settings.json 的 mcp_enabled 控制（默认 True）。
    if not ToolConfig.from_settings().mcp_enabled:
        return _err(404, "NotEnabled", "MCP 已在 settings 中禁用")
    from .mcp.catalog import handle_jsonrpc as mcp_handle
    # body 已由 _read_body 解析为 dict（单请求），直接喂给传输无关核心
    result = mcp_handle(h.state["cfg"], body)
    return (json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")


class _ConsoleServer(ThreadingHTTPServer):
    """控制台 HTTP 服务：**禁用端口复用** + 携带访问令牌。

    标准库 HTTPServer 的 allow_reuse_address 默认为 1，而 Windows 的 SO_REUSEADDR
    允许与「已处于监听状态」的端口重复绑定且**不报错**。后果是新进程打印「已启动」
    却收不到任何请求——连接仍被先前那个进程（例如更名前的残留服务）受理，表现为
    前端静态资源全部 404、页面卡在加载态，极难排查（实际踩到过）。

    auth_token 为本进程的访问令牌（""= 显式降级无鉴权模式）。
    """

    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, addr, handler, auth_token: str):
        super().__init__(addr, handler)
        self.auth_token = auth_token


def _port_in_use(host: str, port: int) -> bool:
    """探测端口是否已被监听（connect 成功即视为占用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def start(cfg: RepoConfig, frontends: list, analyzer, summarize,
          write_reports, host: str = "127.0.0.1", port: int = 8788) -> None:
    """启动控制台服务（阻塞）。analyzer/summarize/write_reports 由 cli 注入。"""
    if _port_in_use(host, port):
        print(f"[repolucent] 错误：端口 {port} 已被占用——可能有上一次或旧版本的服务仍在运行。",
              file=sys.stderr)
        print(f"[repolucent] 提示：该端口上的服务未必是本版本；旧进程会导致前端静态资源 404、页面卡住。",
              file=sys.stderr)
        print(f"[repolucent]   排查（Windows）：netstat -ano | findstr :{port}"
              f"，再 taskkill /PID <pid> /F", file=sys.stderr)
        print(f"[repolucent]   或改用其他端口：repolucent.py serve --port {port + 1}",
              file=sys.stderr)
        raise SystemExit(2)
    token, degraded = resolve_auth_token()
    _Handler.state = {
        "cfg": cfg,
        "frontends": frontends,
        "analyzer": analyzer,
        "summarize": summarize,
        "write_reports": write_reports,
        "last_data": {},
        "last_duration": 0,
        "last_cache": {},
    }
    try:
        srv = _ConsoleServer((host, port), _Handler, token)
    except OSError as e:
        print(f"[repolucent] 错误：无法绑定 {host}:{port} —— {e}", file=sys.stderr)
        raise SystemExit(2) from None
    print(f"[repolucent] 控制台已启动：http://{host}:{port}")
    print(f"[repolucent] 仓库：{cfg.repo_root} · 输出：{cfg.out_dir} · Ctrl+C 退出")
    if degraded:
        # 显式降级：REPOLUCENT_TOKEN="" 选择了无鉴权模式（仅本机自担风险）
        print("\033[91m[repolucent] 警告：REPOLUCENT_TOKEN 为空——本服务已退回无鉴权模式，"
              "本机任何进程/页面都可调用全部 API。\033[0m", file=sys.stderr)
        print("[repolucent] 警告：如需恢复鉴权，请删除空的 REPOLUCENT_TOKEN 环境变量后重启。",
              file=sys.stderr)
    else:
        print(f"[repolucent] 访问令牌: {token}")
        if os.environ.get("REPOLUCENT_TOKEN"):
            print("[repolucent] （token 来自 REPOLUCENT_TOKEN 环境变量，MCP 客户端可固定复用）")
        print(f"[repolucent] MCP 客户端请配置 header: X-RepoLucent-Token: <token>"
              f"（浏览器打开首页会自动注入）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[repolucent] 已停止")


# ---------------------------------------------------------------------- UI ----

_UI_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoLucent 控制台</title>
<link rel="stylesheet" href="/static/tokens.css">
<script>window.__REPOLUCENT_TOKEN="__REPOLUCENT_TOKEN__";</script>
</head>
<body>
<div class="scr">
  <div class="tbar">
    <span class="tbd"><span class="vmark" style="width:15px;height:15px;border-radius:4px;font-size:9px">V</span>RepoLucent</span>
    <span>本地控制台 · 仅绑定 127.0.0.1</span>
    <span class="winv"><span>—</span><span>□</span><span>?</span></span>
  </div>
  <div class="hdr">
    <div class="brand"><span class="vmark">R</span><span><span class="bname">RepoLucent</span><br><span class="bsub">CODE LENS</span></span><span class="edchip">v__REPOLUCENT_VERSION__</span></div>
    <div class="cmdbar"><span>仓库洞察 · 分析 / 门禁 / 审计 / 脚本</span></div>
    <div class="hspace"></div>
    <span class="ibtn">?</span>
  </div>
  <div class="sbody">
    <aside class="sider" id="sider"></aside>
    <main class="smain" id="smain"></main>
  </div>
  <div class="sbar">
    <span class="sgroup"><span class="dotp ok"></span><span id="meta">加载中…</span></span>
    <span class="sgroup"><span id="busy">执行中…</span></span>
    <span class="sgroup"><span id="errBox"></span></span>
    <span class="sgap"></span>
    <span class="sgroup mono">REPOLUCENT · v__REPOLUCENT_VERSION__</span>
  </div>
</div>
<script src="/static/icons.js"></script>
<script src="/static/app.js"></script>
</body></html>"""
