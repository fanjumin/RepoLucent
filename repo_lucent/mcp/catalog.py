# -*- coding: utf-8 -*-
"""MCP tool-catalog：把本工具的确定性能力编译成标准 MCP 工具清单（单一事实源）。

两类能力统一登记：
1) 核心确定性工具（repo.* / repo.git.*）：直接映射到 server.py 里已有的进程内函数
   （与 /api/* 同一分析管线，口径完全一致）。全部只读。
2) 脚本工具（script.<id>）：registry.json 里 kind=tool & status=active 的条目，其 inputs
   （--x 形参）编译成 JSON Schema 即得 tools/list。执行面唯一化——一律走既有受控入口
   run_script_api(sid, argv, confirm)，门控原样继承，MCP 层只搬运、不放宽。

传输无关：本模块只产出「工具清单」与「调用结果」，不关心是 stdio 还是 HTTP。
stdio.py 与 server.py 的 /mcp 分支都复用这里的 handle_jsonrpc / list_tools / call_tool。
"""
from __future__ import annotations

import json
import threading
import types
from pathlib import Path

from .. import TOOL_VERSION
from ..script_cmd import _load_registry, _index, run_script_api


SERVER_INFO = {
    "name": "repolucent",
    "version": TOOL_VERSION,
    "protocolVersion": "2024-11-05",
}

# 过渡期别名：更名前工具名前缀为 insight.*，tools/call 收到旧名时
# 映射到新名执行（tools/list 只暴露新名，避免清单膨胀）。
_LEGACY_TOOL_PREFIX = "insight."


# ---------------------------------------------------------------------------
# 核心工具定义（handler 为 _h_* 函数名；schema 即 JSON Schema properties 片段）
# 仅登记只读能力；任何写操作（git push/pull/sync、脚本真执行）都不在此暴露，
# 必须经 script.* 的 run_script_api 确认门控。
# ---------------------------------------------------------------------------
CORE_TOOLS: dict[str, dict] = {
    "repo.summary": {
        "desc": "全仓指标概览（插件数/核心模块/路由/边界观察项/代码行等，对应 /api/summary）。只读。",
        "schema": {},
        "handler": "_h_summary",
    },
    "repo.context": {
        "desc": "按需上下文切片（v1.6.0 新增，对应 CLI `context`）：指定单个插件/核心模块"
                "即返回该目标的聚焦上下文（标识/manifest 校验/路由/依赖/关键类），"
                "避免为看一个插件而先跑全量再翻文件。只读，不落盘。",
        "schema": {
            "target": {"type": "string",
                       "description": "plugin:<identifier> 或 module:<name>；省略则返回全局 AI 上下文"},
            "depth": {"type": "string", "enum": ["brief", "normal", "full"],
                      "description": "颗粒度：brief≈2-4KB / normal 加关键类与文件清单 / full 加全部签名（默认 brief）"},
        },
        "handler": "_h_context",
    },
    "repo.query": {
        "desc": "受限结构化查询（scope/select/where，对应 /api/query）。只读，不落盘。",
        "schema": {
            "scope": {"type": "string", "enum": ["plugins", "core"],
                      "description": "查询范围；缺省由 select 首个字段推断"},
            "select": {"type": "string",
                       "description": "逗号分隔字段路径，支持 a.b / routes[].rule，如 identifier,version,routes"},
            "where": {"type": "string",
                      "description": "过滤 key=value 或 key!=value，逗号分隔取 AND"},
            "format": {"type": "string", "enum": ["json", "table"], "description": "输出格式，默认 json"},
        },
        "handler": "_h_query",
    },
    "repo.search": {
        "desc": "符号定位（v1.6.0 新增，对应 CLI `search`）：类/函数/路由 → file:line 事实，"
                "替代全仓 grep。精确命中优先，未命中按子串（大小写不敏感）回退。只读，不落盘。",
        "schema": {
            "symbol": {"type": "string",
                       "description": "符号名（类名/函数名/路由 rule）；子串亦可"},
            "limit": {"type": "integer", "description": "结果条数上限，默认 50（上限 500）"},
        },
        "required": ["symbol"],
        "handler": "_h_search",
    },
    "repo.gate": {
        "desc": "架构门禁评估（manifest/边界/循环/路由前缀/超大文件）。只读，不落盘。",
        "schema": {
            "items": {"type": "string", "description": "检查项，逗号分隔或 'all'，默认 all"},
            "max_file_lines": {"type": "integer", "description": "file-too-large 阈值，默认 2000"},
        },
        "handler": "_h_gate",
    },
    "repo.audit": {
        "desc": "代码审计（只读、不落盘）：返回放行裁决 + 覆盖度矩阵 + 发现。语义通道默认仅咨询。",
        "schema": {
            "dims": {"type": "string",
                     "description": "审计维度 CSV：security,architecture,quality,ai_business"},
            "fail_on_severity": {"type": "string", "enum": ["blocking", "major", "minor", "info"],
                                 "description": "达到即判不放行，默认 blocking"},
            "with_semantic": {"type": "boolean", "description": "标注语义通道已启用（需外部回灌才生效）"},
            "semantic_can_block": {"type": "boolean", "description": "允许语义发现纳入阻断，默认 false"},
        },
        "handler": "_h_audit",
    },
    "repo.git.log": {
        "desc": "提交历史扫描（按作者/时间过滤）。只读。",
        "schema": {
            "since": {"type": "integer", "description": "最近多少天，默认 90"},
            "author": {"type": "string", "description": "仅扫描指定作者"},
        },
        "handler": "_h_git_log",
    },
    "repo.git.remote-diff": {
        "desc": "本地与远程仓库差异对比。只读。",
        "schema": {
            "remote": {"type": "string", "description": "remote 名称，默认 origin"},
            "branch": {"type": "string", "description": "分支名（默认当前分支）"},
        },
        "handler": "_h_git_remote_diff",
    },
    "repo.git.untracked": {
        "desc": "未跟踪文件检测与分类。只读。",
        "schema": {},
        "handler": "_h_git_untracked",
    },
    "repo.git.ci-check": {
        "desc": "CI/CD 配置合规性检查。只读。",
        "schema": {
            "platform": {"type": "string", "enum": ["github", "gitee", "auto"], "description": "默认 auto"},
        },
        "handler": "_h_git_ci_check",
    },
}

# handler 名 → 函数对象的延迟映射（在模块加载后补全）
CORE_HANDLERS: dict[str, callable] = {}


# ---------------------------------------------------------------------------
# 进程内分析器（带 mtime 失效判定的 memo，满足 P0 §4.2 请求级缓存诉求）
# ---------------------------------------------------------------------------
class _Analyzer:
    """懒加载并缓存一次完整分析结果。同一 MCP 进程多次工具调用复用，避免重复 5–30s 分析。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._args = self._make_args()
        self._data = None
        self._sig = None
        #: 本次分析的逐文件解析事实（v1.6.0）：供 repo.context / repo.search 复用，
        #: 使符号索引与上下文切片都能零新增解析成本地服务 MCP 会话。
        self.parse_cache: dict = {}

    @staticmethod
    def _make_args():
        ns = types.SimpleNamespace()
        ns.no_cache = False
        ns.deterministic = False
        ns.module = None
        ns.plugin = None
        return ns

    def _current_sig(self):
        try:
            repo_mt = self.cfg.repo_root.stat().st_mtime_ns
            out_mt = self.cfg.out_dir.stat().st_mtime_ns if self.cfg.out_dir.exists() else 0
            return (repo_mt, out_mt)
        except OSError:
            return None

    def data(self) -> dict:
        sig = self._current_sig()
        if self._data is not None and self._sig == sig:
            return self._data
        # 延迟导入，避免与 cli/server 形成顶层循环依赖
        from ..cli import _analyze as cli_analyze
        data, _, parse_cache = cli_analyze(self._args, self.cfg)
        self._data = data
        self.parse_cache = parse_cache or {}
        self._sig = sig
        return data


# ---------------------------------------------------------------------------
# 跨 tools/call 共享的分析器缓存（进程级）
# 让多次核心工具调用复用同一份分析结果（否则每个 tools/call 都重跑 5–30s），
# 并支持写操作后即时失效——极致一致性：经 MCP 跑过受控写脚本（confirm=true）后
# 立即失效，下次读工具拿到的是最新仓库状态，不依赖 mtime/TTL 兜底。
# ---------------------------------------------------------------------------
_AZ_LOCK = threading.Lock()
_AZ_REGISTRY: dict = {}  # id(cfg) -> _Analyzer


def _get_analyzer(cfg) -> "_Analyzer":
    key = id(cfg)
    with _AZ_LOCK:
        az = _AZ_REGISTRY.get(key)
        if az is None:
            az = _Analyzer(cfg)
            _AZ_REGISTRY[key] = az
    return az


def invalidate_catalog_analysis(cfg) -> None:
    """写操作后即时失效 catalog 侧的分析器缓存（与 cli.invalidate_analysis_cache 配对）。"""
    with _AZ_LOCK:
        _AZ_REGISTRY.pop(id(cfg), None)


# ---------------------------------------------------------------------------
# registry.inputs → JSON Schema 编译（修复文档参考实现里 EXT-c 的 required 遗漏）
# ---------------------------------------------------------------------------
_TYPE_MAP = {"path": "string", "file": "string", "flag": "boolean",
             "int": "integer", "str": "string", "enum": "string",
             "bool": "boolean", "string": "string"}


def _argparse_inputs_to_schema(inputs: list[dict] | None):
    """把 registry.inputs（--x 形参）编译成 (properties, required[]) 两段 JSON Schema。"""
    props: dict = {}
    required: list[str] = []
    for i in (inputs or []):
        raw = i.get("name", "")
        key = raw.lstrip("-").replace("-", "_")
        t = _TYPE_MAP.get(i.get("type"), "string")
        p = {"type": t, "description": i.get("desc", "")}
        if i.get("default") is not None:
            p["default"] = i["default"]
        props[key] = p
        if i.get("required"):
            required.append(key)
    return props, required


def _to_argv(args: dict | None, inputs: list[dict] | None) -> list[str]:
    """把 MCP 传入的 arguments 还原成 script 进程的 argv（修复 EXT-d）。

    - flag/boolean 且为 True → 仅追加原始 --name；为 False → 不还原（避免 `--flag false`
      被 argparse 吞掉下一个 token）。
    - 其余类型 → 追加 `--name value`。
    - confirm 是 MCP 层门控，不进 argv。
    - 未知 key 忽略，避免把 MCP 自有字段当脚本参数。
    run_script_api 另有 argv 限长 40 的兜底，双保险。
    """
    norm = {i["name"].lstrip("-").replace("-", "_"): i for i in (inputs or [])}
    argv: list[str] = []
    for k, v in (args or {}).items():
        if k == "confirm":
            continue
        spec = norm.get(k)
        if spec is None:
            continue
        raw = spec["name"]
        t = spec.get("type", "string")
        if t in ("flag", "boolean") or isinstance(v, bool):
            if v:
                argv.append(raw)
            continue
        if v is None:
            continue
        argv.append(raw)
        argv.append(str(v))
    return argv


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------
def list_tools(cfg) -> list[dict]:
    out: list[dict] = []
    for tid, meta in CORE_TOOLS.items():
        out.append({
            "name": tid,
            "description": meta["desc"],
            "inputSchema": {
                "type": "object",
                "properties": meta["schema"],
                "required": meta.get("required", []),
            },
        })
    reg = _load_registry()
    for s in reg.get("scripts", []):
        if s.get("kind") == "tool" and s.get("status") == "active":
            props, required = _argparse_inputs_to_schema(s.get("inputs"))
            out.append({
                "name": "script." + s["id"],
                "description": s.get("summary", ""),
                "inputSchema": {"type": "object", "properties": props, "required": required},
            })
        elif s.get("kind") == "mcp" and s.get("status") == "active":
            # registry 里显式声明的 kind=mcp 条目（经 run_script_api 同一门控）
            props, required = _argparse_inputs_to_schema(s.get("inputs"))
            out.append({
                "name": "script." + s["id"],
                "description": f"[外部 MCP · {s.get('server')}] {s.get('summary', '')}",
                "inputSchema": {"type": "object", "properties": props, "required": required},
            })

    # ---- outbound MCP（EXT-4）：本工具作为客户端消费外部 MCP Server ----
    # 仅在 settings.mcp_servers 显式配置了 server 时才出现；未配置时清单与改造前完全一致。
    out.extend(_ext_tools())
    return out


# ---------------------------------------------------------------------------
# outbound 外部工具（mcp.<server>.<tool>）
# ---------------------------------------------------------------------------
_EXT_PREFIX = "mcp."


def _ext_tools() -> list[dict]:
    """发现外部 MCP Server 暴露的工具，编译成 MCP 工具条目。

    故障隔离：单个 server 不可达只跳过该 server，不影响整体 tools/list。
    """
    try:
        from ..settings import is_outbound_mcp_enabled, mcp_servers
        if not is_outbound_mcp_enabled() or not mcp_servers():
            return []
        from ..scriptlib.adapters.mcp import discover_tools
        tools, errors = discover_tools()
    except Exception:  # noqa: BLE001 - 发现失败不该让 tools/list 崩
        return []
    out: list[dict] = []
    for t in tools:
        out.append({
            "name": f"{_EXT_PREFIX}{t['server']}.{t['name']}",
            "description": f"[外部 MCP · {t['server']}] {t.get('description') or ''}",
            "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
        })
    if errors:
        # 诊断信息放进一个只读伪工具的描述里，便于排查又不污染工具清单
        out.append({
            "name": f"{_EXT_PREFIX}__discovery_errors",
            "description": "外部 MCP Server 发现错误（只读诊断，调用无副作用）：" + "; ".join(errors)[:500],
            "inputSchema": {"type": "object", "properties": {}},
        })
    return out


# ---------------------------------------------------------------------------
# tools/call → 结果载荷（由 transport 包装成 {"content": [...], "isError": bool}）
# ---------------------------------------------------------------------------
def _content_ok(payload) -> dict:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _content_err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def call_tool(cfg, name: str | None, args: dict | None) -> dict:
    if not name:
        return _content_err("tools/call 缺少 name")
    # ---- 过渡期兼容：旧前缀 insight.* → 新前缀 repo.* ----
    if name.startswith(_LEGACY_TOOL_PREFIX):
        name = "repo." + name[len(_LEGACY_TOOL_PREFIX):]
    # ---- 脚本工具：唯一执行面 = 既有受控入口（门控原样继承，不放宽）----
    if name.startswith("script."):
        sid = name[len("script."):]
        reg = _load_registry()
        s = _index(reg).get(sid)
        if not s:
            return _content_err(f"未知脚本：{sid}")
        argv = _to_argv(args, s.get("inputs"))
        confirm = bool((args or {}).get("confirm", False))
        result = run_script_api(sid, argv=argv, confirm=confirm)
        # 极致一致性：受控写（confirm=true）成功后即刻失效两侧缓存，
        # 避免后续读工具拿到陈旧仓库快照。dry-run（confirm=false）不失效。
        # 与 server.py 写端点守卫逻辑保持一致（仅 confirm=true 才失效）。
        if confirm:
            try:
                from ..cli import invalidate_analysis_cache
                invalidate_analysis_cache()
            except Exception:
                pass
            invalidate_catalog_analysis(cfg)
        return _content_ok(result)
    # ---- outbound 外部 MCP 工具：mcp.<server>.<tool>（EXT-4）----
    # 门控与 script.* 一致：confirm=false 只给 dry-run 预览，绝不自动放行。
    if name.startswith(_EXT_PREFIX):
        rest = name[len(_EXT_PREFIX):]
        if "." not in rest:
            return _content_err(f"外部工具名须为 mcp.<server>.<tool>：{name}")
        server, tool = rest.split(".", 1)
        confirm = bool((args or {}).get("confirm", False))
        arguments = {k: v for k, v in (args or {}).items() if k != "confirm"}
        if not confirm:
            return _content_ok({"ok": True, "dry_run": True, "server": server,
                                "tool": tool, "arguments": arguments,
                                "hint": "传 confirm=true 才真正调用外部 MCP Server（不自动放行）"})
        try:
            from ..scriptlib.adapters.mcp import get_client
            r = get_client(server).call_tool(tool, arguments)
        except Exception as e:  # noqa: BLE001
            return _content_err(f"外部 MCP 调用失败（{server}/{tool}）：{e}")
        # 外部工具可能改动仓库状态 → 与受控写保持一致，即时失效两侧缓存
        try:
            from ..cli import invalidate_analysis_cache
            invalidate_analysis_cache()
        except Exception:  # noqa: BLE001
            pass
        invalidate_catalog_analysis(cfg)
        return _content_ok({"ok": not r.get("isError"), "server": server, "tool": tool,
                            "isError": bool(r.get("isError")),
                            "text": r.get("text", ""), "json": r.get("json")})
    # ---- 核心工具：与 /api 同源的进程内函数 ----
    if name not in CORE_HANDLERS:
        return _content_err(f"未知工具：{name}")
    az = _get_analyzer(cfg)
    try:
        payload = CORE_HANDLERS[name](az, args or {})
    except Exception as e:  # 优雅降级：业务异常转成 content 错误，绝不崩断流
        return _content_err(f"{name} 执行失败：{e}")
    return _content_ok(payload)


# ---------------------------------------------------------------------------
# 核心工具 handlers（全部只读，与 server.py 的 /api/* 同源）
# ---------------------------------------------------------------------------
def _h_summary(az: _Analyzer, args: dict) -> dict:
    from ..cli import _summary_pairs
    data = az.data()
    return dict(_summary_pairs(data, 0))


def _h_query(az: _Analyzer, args: dict) -> dict:
    """受限查询。与 CLI `query` 共用 cli.build_query —— 执行面唯一化（v1.6.0）。"""
    from ..cli import build_query
    select = args.get("select") or None
    sel_list = ([s.strip() for s in select.split(",") if s.strip()]
                if select else None)
    # 参数错误以异常形式抛出，由 call_tool 兜底为 content 错误
    out = build_query(az.data(), scope=args.get("scope") or None, select=sel_list,
                      where=args.get("where") or None,
                      fmt=args.get("format", "json") or "json")
    return out["table"] if out["format"] == "table" else out["result"]


def _h_context(az: _Analyzer, args: dict) -> str:
    """按需上下文切片（v1.6.0）：直接返回 Markdown 文本。

    返回纯文本而非 JSON 包装：Agent 拿到即可作为上下文使用，省一层解析、
    也避免把 2-4KB 的切片再套 JSON 转义放大体积。
    """
    from ..cli import build_context, TargetNotFound
    spec = args.get("target") or args.get("for") or None
    try:
        out = build_context(az.cfg, az.data(), spec,
                            depth=args.get("depth") or "brief",
                            parse_cache=az.parse_cache)
    except TargetNotFound as e:
        raise ValueError(
            f"{e}（候选：{', '.join(e.candidates) or '无'}；"
            f"可先用 repo.query --select identifier 确认名称）") from e
    return out["context"]


def _h_search(az: _Analyzer, args: dict) -> dict:
    """符号定位（v1.6.0）：类/函数/路由 → file:line/kind/owner，只回事实。"""
    from ..cli import build_search
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    return build_search(az.cfg, az.data(), args.get("symbol") or "",
                        limit=limit, parse_cache=az.parse_cache)


def _h_gate(az: _Analyzer, args: dict) -> dict:
    from ..gate import evaluate
    data = az.data()
    items = args.get("items") or "all"
    max_file_lines = int(args.get("max_file_lines", 2000))
    results = evaluate(items, data, az.cfg, max_file_lines)
    return {"results": [r.to_dict() for r in results]}


def _h_audit(az: _Analyzer, args: dict) -> dict:
    from ..audit import engine as AE
    data = az.data()
    dims = args.get("dims")
    dims_list = ([d.strip() for d in dims.split(",") if d.strip()]
                 if dims else None)
    fail_on = args.get("fail_on_severity", "blocking") or "blocking"
    with_semantic = bool(args.get("with_semantic", False))
    sem_block = bool(args.get("semantic_can_block", False))
    # run_audit 纯计算、不落盘；无 save_baseline，审计报告由调用方决定如何处置
    rep = AE.run_audit(az.cfg, data, az.cfg.repo_root, dims=dims_list,
                       fail_on_severity=fail_on, use_semantic=with_semantic,
                       semantic_can_block=sem_block)
    return rep.to_dict()


def _h_git_log(az: _Analyzer, args: dict) -> dict:
    from ..git_log_analyzer import scan_commits
    since = int(args.get("since", 90))
    author = args.get("author")
    return scan_commits(az.cfg.repo_root, since_days=since, author=author)


def _h_git_remote_diff(az: _Analyzer, args: dict) -> dict:
    from ..remote_diff import analyze_remote_diff
    remote = args.get("remote", "origin")
    branch = args.get("branch")
    return analyze_remote_diff(az.cfg.repo_root, remote=remote, branch=branch)


def _h_git_untracked(az: _Analyzer, args: dict) -> dict:
    from ..untracked_scanner import scan_untracked
    return scan_untracked(az.cfg.repo_root)


def _h_git_ci_check(az: _Analyzer, args: dict) -> dict:
    from ..ci_analyzer import analyze_ci_config
    platform = args.get("platform", "auto")
    return analyze_ci_config(az.cfg.repo_root, platform=platform)


# 补全 handler 映射（延迟，确保模块导入期不触发重依赖）
def _register_handlers():
    for tid, meta in CORE_TOOLS.items():
        fn = globals().get(meta["handler"])
        if callable(fn):
            CORE_HANDLERS[tid] = fn


_register_handlers()


# ---------------------------------------------------------------------------
# JSON-RPC 传输无关的处理核心（stdio 与 HTTP 共用）
# ---------------------------------------------------------------------------
def _resp(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err_resp(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_jsonrpc(cfg, msg: dict) -> dict:
    """处理单条 JSON-RPC 请求，返回完整响应 dict（含 jsonrpc/id）。

    通知（无 id）由传输层提前跳过，不进入本函数。任何异常都降级为 content 错误，
    绝不因单条请求导致整个 stdio 流崩溃。
    """
    rid = msg.get("id")
    method = msg.get("method")
    try:
        if method == "initialize":
            return _resp(rid, {
                "protocolVersion": SERVER_INFO["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_INFO["name"], "version": SERVER_INFO["version"]},
            })
        if method == "tools/list":
            return _resp(rid, {"tools": list_tools(cfg)})
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            return _resp(rid, call_tool(cfg, name, arguments))
        return _err_resp(rid, -32601, "method not found")
    except Exception as e:  # 传输层兜底
        return _resp(rid, {"content": [{"type": "text", "text": f"server error: {e}"}],
                           "isError": True})
