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

from .config import CODE_EXTS

#: 可用检查项。"all" 为集合展开的快捷方式，不参与实际判定。
GATE_CHOICES = (
    "manifest-invalid",     # 存在 manifest 校验失败的插件
    "boundary-violation",   # 核心模块直接 import 业务插件
    "plugin-cycle",         # 插件间存在循环依赖
    "route-unprefixed",     # 插件路由未遵循 /admin/<identifier> 前缀惯例
    "file-too-large",       # 单文件代码行超过阈值
    "findings",             # 规则引擎产生 error 级 finding（阶段 F / 2.0.0）
    "all",                  # 以上全部
)

#: 默认阈值
DEFAULT_MAX_FILE_LINES = 2000


def default_gates() -> list[str]:
    """--fail-on 未显式给出时的缺省门禁集（档位 A，来自 profile.default_gates）。

    零行为变化约束：未配置 profile.default_gates 时返回 []，即维持历史行为
    「不传 --fail-on 就不跑门禁」。配置后由 expand() 校验合法性（只能从
    GATE_CHOICES 中选，"all" 会被展开），非法项报 ValueError → 退出码 2。
    """
    from .settings import profile_get
    v = profile_get("default_gates", None)
    if not v:
        return []
    if isinstance(v, str):
        v = [s.strip() for s in v.split(",") if s.strip()]
    return [str(g) for g in v if g]


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


def _check_route_unprefixed(data: dict) -> GateResult:
    """插件路由应遵循 /admin/<identifier> 前缀惯例。

    url_prefix 若为非字面量（引用变量或常量），无法确定实际路径，跳过判定
    以避免误报——宁可漏报，不可误伤。
    """
    hits: list[dict] = []
    for p in data["plugins"]["items"]:
        ident, pdir = p["identifier"], p["dir"]
        allowed = {f"/admin/{ident}", f"/admin/{pdir}"}
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
    return GateResult("route-unprefixed", not hits, hits,
                      "插件 Blueprint 的 url_prefix 应为 /admin/<identifier>")


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


def evaluate(gates, data: dict, cfg=None, max_file_lines: int = DEFAULT_MAX_FILE_LINES
             ) -> list[GateResult]:
    """按名称顺序评估全部选中的检查项，返回结果列表。"""
    chosen = expand(gates)
    results: list[GateResult] = []
    if "manifest-invalid" in chosen:
        results.append(_check_manifest_invalid(data))
    if "boundary-violation" in chosen:
        results.append(_check_boundary_violation(data))
    if "plugin-cycle" in chosen:
        results.append(_check_plugin_cycle(data))
    if "route-unprefixed" in chosen:
        results.append(_check_route_unprefixed(data))
    if "file-too-large" in chosen:
        results.append(_check_file_too_large(data, cfg, max_file_lines))
    if "findings" in chosen:
        results.append(_check_findings(data))
    return results
