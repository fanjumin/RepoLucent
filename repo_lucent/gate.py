# -*- coding: utf-8 -*-
"""架构门禁：把分析结论转化为可通过 / 可拦截的判定结果与退出码。

设计边界：
- 本模块**只读**已生成的 data 与 parse_cache，不修改任何分析结果，
  也不写文件；判定失败只体现在 stdout 摘要与进程退出码上。
- 除 file-too-large 需按阈值重新统计代码行外，其余检查项的数据均已存在于
  data 中，无需重复扫描。file-too-large 也只在被选中时才付出遍历代价。
- 检查项之间互不依赖，新增一项只需加一个 `_check_*` 函数并登记到 CHECKS。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .config import CODE_EXTS

#: 可用检查项。"all" 为集合展开的快捷方式，不参与实际判定。
GATE_CHOICES = (
    "manifest-invalid",     # 存在 manifest 校验失败的插件
    "boundary-violation",   # 核心模块直接 import 业务插件
    "plugin-cycle",         # 插件间存在循环依赖
    "include-cycle",        # C/C++/Arduino 头文件循环包含（基于图产物 file→file include 边）
    "route-unprefixed",     # 插件路由未遵循 /admin/<identifier> 前缀惯例
    "file-too-large",       # 单文件代码行超过阈值
    "findings",             # 规则引擎产生 error 级 finding（阶段 F / 2.0.0）
    "all",                  # 以上全部
)

#: 默认阈值
DEFAULT_MAX_FILE_LINES = 2000

#: 与语言/形态无关的通用规则：profile 未声明 ``gates.rules`` 时的可用集。
#: 只保留 file-too-large——它不假设组件体系、不读 manifest、不需要路由惯例，
#: 对任意仓库都成立；其余检查项都是"某个形态"的约定，须由 profile 显式声明。
GATE_GENERIC_RULES: tuple[str, ...] = ("file-too-large",)


def active_rules() -> set[str]:
    """该 profile **支持**的门禁项集合（= profile.gates.rules ∩ GATE_CHOICES）。

    未声明 gates.rules 时返回 GATE_GENERIC_RULES。这里做交集而非直接信任，
    是为了让 profile 里的笔误不会变成"运行时未知检查项"的崩溃。
    """
    enabled = config.GATE_RULES
    if enabled is None:
        return set(GATE_GENERIC_RULES)
    return {str(x) for x in enabled if str(x) in GATE_CHOICES}


def active_rule_list() -> list[str]:
    """该 profile 支持的门禁项，按 GATE_CHOICES 的声明顺序返回（供文档生成用）。

    与 active_rules() 的区别：这里保留稳定顺序。AGENTS.md 要逐项列出，
    顺序必须确定，否则产物不可 diff。
    """
    active = active_rules()
    return [g for g in GATE_CHOICES if g != "all" and g in active]


def default_gates() -> list[str]:
    """--fail-on 未显式给出时的缺省门禁集（档位 A，来自 profile.default_gates）。

    零行为变化约束：未配置 profile.default_gates 时返回 []，即维持历史行为
    「不传 --fail-on 就不跑门禁」。配置后由 expand() 校验合法性（只能从
    GATE_CHOICES 中选，"all" 会被展开），非法项报 ValueError → 退出码 2。

    另按 active_rules() 收窄：缺省集只是"顺手的默认"，不应因为 profile 未支持
    其中某项就让整次运行失败（用户没有点名该项），故这里静默取交集。
    """
    from .settings import profile_get
    v = profile_get("default_gates", None)
    if not v:
        return []
    if isinstance(v, str):
        v = [s.strip() for s in v.split(",") if s.strip()]
    active = active_rules()
    return [str(g) for g in v if g and (str(g) == "all" or str(g) in active)]


@dataclass
class GateResult:
    """单项门禁结果。

    hits 中每条至少含 file / plugin / detail 之一，用于定位问题；
    threshold 描述该检查项的判定标准，便于 CI 日志自解释。
    """
    name: str
    passed: bool
    hits: list[dict] = field(default_factory=list)
    threshold: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "hit_count": len(self.hits),
            "threshold": self.threshold,
            "hits": self.hits,
        }


# --------------------------------------------------------------------------
# 各检查项实现
# --------------------------------------------------------------------------

def _check_manifest_invalid(data: dict) -> GateResult:
    hits = [{"plugin": p["identifier"],
             "detail": "；".join(p["manifest_errors"]) or "manifest 校验失败"}
            for p in data["plugins"]["items"] if not p["manifest_valid"]]
    hits.sort(key=lambda h: h["plugin"])
    return GateResult("manifest-invalid", not hits, hits,
                      "所有插件的 plugin.json 必须通过 schema 校验")


def _check_boundary_violation(data: dict) -> GateResult:
    obs = data["interactions"]["boundary_observations"]
    hits = [{"file": v["file"],
             "detail": f"核心模块 {v['module']} 直接 import {v['imports']}"}
            for v in obs["violations"]]
    hits.sort(key=lambda h: h["file"])
    return GateResult("boundary-violation", not hits, hits, obs["rule"])


def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """检测有向图中的全部简单环（含自环），返回规范化后的环路径列表。

    使用 DFS 三色标记；插件规模在数十量级，递归实现足够且更可读。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    path: list[str] = []
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def norm(cycle: list[str]) -> tuple[str, ...]:
        """把环旋转到最小元素开头，使 A→B→A 与 B→A→B 视为同一环。"""
        nodes = cycle[:-1]                      # 末元素与首元素相同
        i = nodes.index(min(nodes))
        return tuple(nodes[i:] + nodes[:i])

    def dfs(u: str) -> None:
        color[u] = GRAY
        path.append(u)
        for v in graph.get(u, []):
            if color.get(v, WHITE) == GRAY:    # 回边 → 发现环
                cycle = path[path.index(v):] + [v]
                key = norm(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
            elif color.get(v, WHITE) == WHITE:
                dfs(v)
        path.pop()
        color[u] = BLACK

    for n in sorted(graph):
        if color.get(n, WHITE) == WHITE:
            dfs(n)
    return cycles


def _check_plugin_cycle(data: dict) -> GateResult:
    graph = {e["from"]: list(e["to"])
             for e in data["interactions"]["plugin_to_plugin"]}
    cycles = _find_cycles(graph)
    hits = [{"plugin": " → ".join(c), "detail": f"循环依赖链：{' → '.join(c)}"}
            for c in sorted(cycles, key=lambda c: (len(c), c))]
    return GateResult("plugin-cycle", not hits, hits,
                      "插件依赖图必须是无环的（depends_on 与实际 import 合并统计）")


def _check_include_cycle(data: dict, cfg=None) -> GateResult:
    """C/C++/Arduino 头文件循环包含门禁（基于图产物 file→file include 边）。

    依赖独立图产物 repo_lucent_graph.json（分析/落盘时生成）；cfg 缺失或图未生成
    时**跳过并判过**（不误伤），detail 说明原因。Python 调用图无 include 边 → 恒过。
    """
    import json
    from . import ARTIFACT_GRAPH
    if cfg is None:
        return GateResult("include-cycle", True, [], "未提供 repo 配置，include 环检测已跳过")
    gf = cfg.out_dir / ARTIFACT_GRAPH
    if not gf.exists():
        return GateResult("include-cycle", True, [],
                          "依赖图产物未生成（先跑一次分析），include 环检测已跳过")
    try:
        graph = json.loads(gf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return GateResult("include-cycle", True, [], "依赖图产物损坏，include 环检测已跳过")
    cycles = graph.get("include_cycles") or []
    hits = [{"file": cyc[0], "detail": "循环包含链：" + " → ".join(cyc)}
            for cyc in sorted(cycles, key=lambda c: (len(c), c))]
    return GateResult("include-cycle", not hits, hits,
                      "头文件 #include 依赖图必须无环（建议用 include guard / 前置声明解环）")


def _check_route_unprefixed(data: dict) -> GateResult:
    """组件路由应遵循 profile.gates.route_prefix_pattern 声明的前缀惯例。

    改造前这里把 ``/admin/<identifier>`` 写死——那是 VeroRun 的惯例，套到别的
    项目上就是误报。现改为：pattern 由 profile 声明（用 ``{identifier}`` 占位）；
    未声明（None）时本项整体跳过。

    url_prefix 若为非字面量（引用变量或常量），无法确定实际路径，跳过判定
    以避免误报——宁可漏报，不可误伤。
    """
    pattern = config.ROUTE_PREFIX_PATTERN
    if not pattern:
        return GateResult("route-unprefixed", True, [],
                          "该 profile 未声明路由前缀惯例（route_prefix_pattern），本项跳过")
    hits: list[dict] = []
    for p in data["plugins"]["items"]:
        ident, pdir = p["identifier"], p["dir"]
        allowed = {pattern.format(identifier=ident), pattern.format(identifier=pdir)}
        for r in p["routes"]:
            prefix = (r.get("url_prefix") or "").strip().strip("\"'")
            if not prefix.startswith("/"):
                continue                          # 非字面量：跳过
            if prefix.rstrip("/") in allowed:
                continue
            hits.append({"plugin": ident,
                         "file": r.get("file", ""),
                         "detail": f"{r.get('endpoint', '?')} 的 url_prefix="
                                   f"{prefix or '(空)'}，期望 {sorted(allowed)[0]}"})
    hits.sort(key=lambda h: (h["plugin"], h["file"]))
    label = str(((config.PLUGIN_SYSTEM or {}).get("marker_labels") or {})
                .get("component") or "组件")
    return GateResult("route-unprefixed", not hits, hits,
                      f"{label} Blueprint 的 url_prefix 应为 {pattern}")


def _check_file_too_large(data: dict, cfg, max_file_lines: int) -> GateResult:
    """按阈值统计超大文件。仅在被选中时执行，会重新读取代码文件内容。"""
    # 延迟导入：避免 gate 与 fs_scan 形成顶层循环依赖
    from .fs_scan import iter_repo_files, read_text_safe, count_lines

    if cfg is None:
        return GateResult("file-too-large", True, [],
                          f"单文件代码行 ≤ {max_file_lines}（未提供 repo 配置，已跳过）")
    hits: list[dict] = []
    for rel, fpath in iter_repo_files(cfg):
        if fpath.suffix.lower() not in CODE_EXTS:
            continue
        _, code = count_lines(read_text_safe(fpath))
        if code > max_file_lines:
            hits.append({"file": str(rel),
                         "detail": f"{code} 代码行 > {max_file_lines}"})
    hits.sort(key=lambda h: h["file"])
    return GateResult("file-too-large", not hits, hits,
                      f"单文件代码行 ≤ {max_file_lines}")


def _check_findings(data: dict) -> GateResult:
    """规则引擎 findings 门禁：error 级 finding 即失败（warning/info 不计）。

    与文档「info 级不计入门禁」一致——仅 error 级会令 --fail-on findings 返回 1；
    warning 级是否卡 CI 由 --fail-on-warn 控制（作用于最终 failed 列表）。
    """
    findings = data.get("findings") or {}
    items = findings.get("items") or []
    summary = findings.get("summary") or {}
    # 仅 error 级计入失败命中；warning/info 不进 hits，避免对 CI 误伤。
    hits = [{"plugin": f.get("target", "?"),
             "detail": f"{f.get('rule_id')} {f.get('severity')}: {f.get('message')}"
                       + (f" @{f.get('file')}" if f.get("file") else "")}
            for f in items if f.get("severity") == "error"]
    hits.sort(key=lambda h: h["plugin"])
    return GateResult("findings", not hits, hits,
                      f"error 级 findings = {summary.get('error', 0)}（warning/info 不计入）")


# --------------------------------------------------------------------------
# 调度
# --------------------------------------------------------------------------

def expand(gates) -> set[str]:
    """展开检查项集合：支持 --fail-on all / 逗号分隔字符串 / 可迭代对象。"""
    if isinstance(gates, str):
        gates = [s.strip() for s in gates.split(",") if s.strip()]
    chosen = set(gates)
    if "all" in chosen:
        chosen = {g for g in GATE_CHOICES if g != "all"}
    unknown = chosen - set(GATE_CHOICES)
    if unknown:
        raise ValueError(f"未知门禁项: {', '.join(sorted(unknown))}；"
                         f"可选: {', '.join(GATE_CHOICES)}")
    return chosen


def _mentions_all(gates) -> bool:
    """请求里是否用了 ``all`` 快捷方式（用于区分"点名"与"图省事"）。"""
    if isinstance(gates, str):
        return any(s.strip() == "all" for s in gates.split(","))
    try:
        return "all" in set(gates)
    except TypeError:
        return False


#: 检查项实现表（顺序即评估顺序）。新增一项只需在此登记并写一个 _check_* 。
_KNOWN_RULES = (
    ("manifest-invalid", _check_manifest_invalid),
    ("boundary-violation", _check_boundary_violation),
    ("plugin-cycle", _check_plugin_cycle),
    ("include-cycle", _check_include_cycle),
    ("route-unprefixed", _check_route_unprefixed),
    ("file-too-large", _check_file_too_large),
    ("findings", _check_findings),
)


def evaluate(gates, data: dict, cfg=None, max_file_lines: int = DEFAULT_MAX_FILE_LINES
             ) -> list[GateResult]:
    """按登记顺序评估选中的检查项，返回结果列表。

    两级收窄（阶段 6：门禁集由 profile.gates.rules 驱动）：
    1) **全局合法性**——不在 GATE_CHOICES 里的名字直接 ValueError（退出码 2），
       与改造前行为一致；
    2) **profile 可用性**——``all`` 是无法穷举的图省事写法，静默收窄到该 profile
       支持的交集；而**逐个点名**了该 profile 不支持的项时抛 ValueError，
       避免"以为跑了、其实被静默跳过"的假绿。
    """
    requested = expand(gates)
    active = active_rules()
    unavailable = requested - active
    if unavailable and not _mentions_all(gates):
        raise ValueError(
            f"当前 profile 未启用门禁项: {', '.join(sorted(unavailable))}；"
            f"该 profile 可用: {', '.join(sorted(active)) or '(无)'}"
            "。如需启用，请在其 profiles/*.json 的 gates.rules 中声明。")
    chosen = requested & active

    results: list[GateResult] = []
    for name, fn in _KNOWN_RULES:
        if name not in chosen:
            continue
        results.append(fn(data, cfg, max_file_lines) if name == "file-too-large"
                       else fn(data, cfg) if name == "include-cycle"
                       else fn(data))
    return results
