# -*- coding: utf-8 -*-
"""基线快照与差异对比：把「这次比上次多了什么」变成一条命令。

设计边界（重要）：

- 快照只存【可比较的标量指标】：版本、代码行、路由数、manifest 状态、依赖。
  不含目录树、路由明细、类/方法签名——这些内容体量大且高频变动，存进快照会让
  「基线」本身失去稳定性。因此单份快照 <20KB，可安全进 Git。
- 快照恒为确定性输出（generated_at=None）：同一仓库状态下的两次快照逐字节相同。
  时间维度由文件名承载（--save 默认为当天日期）。
- diff 只回答「变了什么」，不回答「为什么变」。+14,770 行是事实，归因不是本模块的职责
  （归因需要 git 历史与人的判断，交给 Agent 或人工报告）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import SCHEMA_VERSION

#: 快照结构版本。新增可选字段为 MINOR：读取时兼容旧版本（缺失段按缺省处理），
#: 写入恒为最新版本。字段改名/语义变化才整体拒绝比对。
SNAPSHOT_VERSION = "1.1"
#: 读取时兼容的快照版本集合
_READABLE_SNAPSHOT_VERSIONS = ("1.0", "1.1")

#: 快照存放目录（相对输出目录）
HISTORY_DIRNAME = "history"


# ---------------------------------------------------------------- 快照构建

def build_snapshot(data: dict) -> dict:
    """从完整分析结果中提取可比较的标量指标。

    恒为确定性输出：不含时间戳，同一状态下的两次快照逐字节相同。
    """
    ov = data["overview"]
    snap = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": None,
        "totals": {
            "files": ov["total_files"],
            "lines_total": ov["total_lines"],
            "lines_code": ov["total_code_lines"],
        },
        "core": {
            m["name"]: {"loc_code": m["loc_code"], "routes": m["route_count"]}
            for m in data["core"]["modules"]
        },
        "plugins": {
            p["identifier"]: {
                "version": p["version"],
                "loc_code": p["loc_code"],
                "routes": p["route_count"],
                "manifest_valid": p["manifest_valid"],
                "manifest_errors": list(p["manifest_errors"]),
                "depends_on": sorted((p.get("depends_on") or {}).keys()),
            }
            for p in data["plugins"]["items"]
        },
    }
    fe = data.get("frontend") or []
    snap["frontend"] = {
        f["root"]: {
            "files": f["overview"]["total_files"],
            "loc_code": f["overview"]["total_code_lines"],
        }
        for f in fe
    }
    return snap


# ---------------------------------------------------------------- 快照存取

def history_dir(out_dir: Path) -> Path:
    return Path(out_dir) / HISTORY_DIRNAME


def _safe_name(name: str) -> str:
    """把用户给出的名称规整为安全文件名（阻断路径穿越）。"""
    s = str(name).strip()
    if s.lower().endswith(".json"):
        s = s[:-5]
    for ch in ("\\", "/", ":", "*", "?", '"', "<", ">", "|"):
        s = s.replace(ch, "_")
    return s if s and s not in (".", "..") else "baseline"


def default_name() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def save_snapshot(out_dir: Path, name: str, snap: dict) -> Path:
    """保存快照到 <out>/history/<name>.json，返回落盘路径。"""
    d = history_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_safe_name(name)}.json"
    p.write_text(_dumps(snap), encoding="utf-8", newline="\n")
    return p


def _dumps(obj: dict) -> str:
    """确定性序列化：sort_keys 保证同一内容两次输出的键序一致。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def resolve_snapshot(baseline: str, out_dir: Path) -> Path | None:
    """把基线参数解析为文件路径：优先按文件路径，再按 history/<name>.json。"""
    p = Path(baseline)
    if p.is_file():
        return p
    hd = history_dir(out_dir)
    for cand in (hd / f"{_safe_name(baseline)}.json", hd / baseline):
        if cand.is_file():
            return cand
    return None


def load_snapshot(baseline: str, out_dir: Path) -> tuple[str, dict]:
    """加载基线快照，返回 (显示名, 快照)。

    失败时抛出 FileNotFoundError（找不到）或 ValueError（结构非法），
    由 CLI 统一转成退出码 2 与可读提示。
    """
    p = resolve_snapshot(baseline, out_dir)
    if p is None:
        raise FileNotFoundError(f"找不到基线快照：{baseline}")
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"快照文件无法解析：{p}（{type(e).__name__}）") from e
    if not isinstance(snap, dict) or "plugins" not in snap or "totals" not in snap:
        raise ValueError(f"不是有效的快照文件（缺少 plugins/totals 段）：{p}")
    if snap.get("snapshot_version") not in _READABLE_SNAPSHOT_VERSIONS:
        raise ValueError(
            f"快照版本不兼容：{p} 为 {snap.get('snapshot_version')!r}，"
            f"当前工具可读 {list(_READABLE_SNAPSHOT_VERSIONS)}。请重新生成基线。")
    return p.stem, snap


def list_baselines(out_dir: Path) -> list[str]:
    """列出 history/ 下已有基线名称（按名称升序）。"""
    hd = history_dir(out_dir)
    if not hd.is_dir():
        return []
    return sorted(p.stem for p in hd.glob("*.json") if p.is_file())


# ---------------------------------------------------------------- 差异计算

def _fmt_delta(n: int) -> str:
    return f"+{n:,}" if n > 0 else f"{n:,}"


def diff_snapshots(base: dict, cur: dict, top: int = 10) -> dict:
    """比较两份快照，返回结构化差异。

    LOC 变动按 |增量| 降序取前 top 条——膨胀与瘦身同样值得看见，
    只按增量降序会把「整个插件被删除」这类最大变动挤到列表末尾。
    """
    bp, cp = base.get("plugins", {}), cur.get("plugins", {})
    bc, cc = base.get("core", {}), cur.get("core", {})
    bt, ct = base.get("totals", {}), cur.get("totals", {})

    added = sorted(k for k in cp if k not in bp)
    removed = sorted(k for k in bp if k not in cp)
    common = sorted(k for k in cp if k in bp)

    def plugin_row(k: str, src: dict) -> dict:
        v = src[k]
        return {"identifier": k, "version": v.get("version"),
                "loc_code": v.get("loc_code", 0), "routes": v.get("routes", 0)}

    # --- 版本变化 ---
    version_changed = []
    for k in common:
        bv, cv = bp[k].get("version"), cp[k].get("version")
        if bv != cv:
            version_changed.append({
                "identifier": k, "base_version": bv, "cur_version": cv,
                "loc_delta": cp[k].get("loc_code", 0) - bp[k].get("loc_code", 0),
                "route_delta": cp[k].get("routes", 0) - bp[k].get("routes", 0),
            })

    # --- LOC 变动（插件 + 核心）---
    loc_rows: list[dict] = []
    for k in common:
        b0 = bp[k].get("loc_code", 0)
        c0 = cp[k].get("loc_code", 0)
        if b0 != c0:
            loc_rows.append({"target": f"plugins/{k}", "base": b0, "cur": c0,
                             "delta": c0 - b0})
    for k in sorted(set(bc) | set(cc)):
        b0 = bc.get(k, {}).get("loc_code", 0)
        c0 = cc.get(k, {}).get("loc_code", 0)
        if b0 != c0:
            loc_rows.append({"target": f"core/{k}", "base": b0, "cur": c0,
                             "delta": c0 - b0})
    for k in added:                                   # 新增插件：基线记 0
        loc_rows.append({"target": f"plugins/{k}", "base": 0,
                         "cur": cp[k].get("loc_code", 0),
                         "delta": cp[k].get("loc_code", 0)})
    for k in removed:                                 # 删除插件：当前记 0
        loc_rows.append({"target": f"plugins/{k}", "base": bp[k].get("loc_code", 0),
                         "cur": 0, "delta": -bp[k].get("loc_code", 0)})
    loc_rows.sort(key=lambda r: (-abs(r["delta"]), r["target"]))

    # --- 路由变更 ---
    route_changes = []
    for k in common:
        b0 = bp[k].get("routes", 0)
        c0 = cp[k].get("routes", 0)
        if b0 != c0:
            route_changes.append({"target": f"plugins/{k}", "base": b0, "cur": c0,
                                  "delta": c0 - b0})
    for k in sorted(set(bc) | set(cc)):
        b0 = bc.get(k, {}).get("routes", 0)
        c0 = cc.get(k, {}).get("routes", 0)
        if b0 != c0:
            route_changes.append({"target": f"core/{k}", "base": b0, "cur": c0,
                                  "delta": c0 - b0})
    route_changes.sort(key=lambda r: (-abs(r["delta"]), r["target"]))

    # --- manifest 错误：新增 / 已修复 ---
    manifest_new: list[dict] = []
    manifest_fixed: list[dict] = []
    for k in common:
        be = set(bp[k].get("manifest_errors") or [])
        ce = set(cp[k].get("manifest_errors") or [])
        if ce - be:
            manifest_new.append({"plugin": k, "errors": sorted(ce - be)})
        if be - ce:
            manifest_fixed.append({"plugin": k, "errors": sorted(be - ce)})
    for k in added:
        errs = sorted(cp[k].get("manifest_errors") or [])
        if errs:
            manifest_new.append({"plugin": k, "errors": errs})

    # --- 依赖变更 ---
    deps_changed = []
    for k in common:
        bd = set(bp[k].get("depends_on") or [])
        cd = set(cp[k].get("depends_on") or [])
        if bd != cd:
            deps_changed.append({"plugin": k, "added": sorted(cd - bd),
                                 "removed": sorted(bd - cd)})

    # --- 桌面端/前端仓库（v1.1 快照起才有；旧快照按缺省兼容） ---
    bf: dict = base.get("frontend") or {}
    cf: dict = cur.get("frontend") or {}
    fe_loc_rows: list[dict] = []
    for k in sorted(set(bf) | set(cf)):
        b0 = bf.get(k, {}).get("loc_code", 0)
        c0 = cf.get(k, {}).get("loc_code", 0)
        if b0 != c0:
            fe_loc_rows.append({"target": f"frontend/{k}", "base": b0, "cur": c0,
                                "delta": c0 - b0})
    fe_loc_rows.sort(key=lambda r: (-abs(r["delta"]), r["target"]))

    totals = {
        key: {"base": bt.get(key, 0), "cur": ct.get(key, 0),
              "delta": ct.get(key, 0) - bt.get(key, 0)}
        for key in ("files", "lines_total", "lines_code")
    }

    has_changes = bool(
        added or removed or version_changed or loc_rows or fe_loc_rows
        or route_changes or manifest_new or manifest_fixed or deps_changed
        or any(v["delta"] for v in totals.values())
    )

    return {
        "has_changes": has_changes,
        "totals": totals,
        "plugins": {
            "added": [plugin_row(k, cp) for k in added],
            "removed": [plugin_row(k, bp) for k in removed],
            "version_changed": version_changed,
        },
        "core": {
            "added": sorted(k for k in cc if k not in bc),
            "removed": sorted(k for k in bc if k not in cc),
        },
        "loc": {"changed_count": len(loc_rows), "top": loc_rows[:top]},
        "frontend": {"loc": fe_loc_rows},
        "routes": route_changes,
        "manifest": {"new_errors": manifest_new, "fixed": manifest_fixed},
        "deps": deps_changed,
    }


# ---------------------------------------------------------------- 差异渲染

_TOTAL_LABELS = (("files", "文件数"), ("lines_total", "总行数"), ("lines_code", "代码行"))


def render_diff_md(result: dict, base_name: str, cur_name: str = "current") -> str:
    """把差异渲染为 Markdown 报告（人看）。"""
    L: list[str] = [f"# RepoLucent Diff · `{base_name}` → `{cur_name}`\n"]
    w = L.append

    if not result["has_changes"]:
        w("\n**无差异**：核心模块、插件版本、代码行、路由、manifest 状态、依赖全部一致。\n")
        return "\n".join(L)

    w("\n## 总览\n")
    w("| 指标 | 基线 | 当前 | 增量 |")
    w("|---|---:|---:|---:|")
    for key, label in _TOTAL_LABELS:
        v = result["totals"][key]
        w(f"| {label} | {v['base']:,} | {v['cur']:,} | {_fmt_delta(v['delta'])} |")

    # --- 插件变更 ---
    p = result["plugins"]
    w("\n## 插件变更\n")
    if not (p["added"] or p["removed"] or p["version_changed"]):
        w("- 无")
    for it in p["added"]:
        w(f"- ➕ 新增 `{it['identifier']}`"
          f"（{it['version']}，{it['loc_code']:,} 代码行，{it['routes']} 路由）")
    for it in p["removed"]:
        w(f"- ➖ 删除 `{it['identifier']}`"
          f"（原 {it['version']}，-{it['loc_code']:,} 代码行）")
    for it in p["version_changed"]:
        seg = [f"`{it['identifier']}` {it['base_version']} → {it['cur_version']}"]
        detail = []
        if it["loc_delta"]:
            detail.append(f"代码行 {_fmt_delta(it['loc_delta'])}")
        if it["route_delta"]:
            detail.append(f"路由 {_fmt_delta(it['route_delta'])}")
        seg.append(f"（{'，'.join(detail)}）" if detail else "")
        w("- 🔼 " + "".join(seg))

    if result["core"]["added"] or result["core"]["removed"]:
        for k in result["core"]["added"]:
            w(f"- ➕ 新增核心模块 `{k}`")
        for k in result["core"]["removed"]:
            w(f"- ➖ 删除核心模块 `{k}`")

    # --- LOC 变动 ---
    loc = result["loc"]
    if loc["top"]:
        w(f"\n## LOC 变动 Top {len(loc['top'])}"
          f"（共 {loc['changed_count']} 项变动，按 |增量| 降序）\n")
        w("| 目标 | 基线 | 当前 | 增量 |")
        w("|---|---:|---:|---:|")
        for r in loc["top"]:
            w(f"| `{r['target']}` | {r['base']:,} | {r['cur']:,} | {_fmt_delta(r['delta'])} |")

    # --- 路由变更 ---
    if result["routes"]:
        w("\n## 路由变更\n")
        for r in result["routes"]:
            w(f"- `{r['target']}` {r['base']} → {r['cur']}（{_fmt_delta(r['delta'])}）")

    # --- 桌面端仓库 ---
    fe = result.get("frontend", {}).get("loc") or []
    if fe:
        w("\n## 桌面端仓库\n")
        w("| 仓库 | 基线 | 当前 | 增量 |")
        w("|---|---:|---:|---:|")
        for r in fe:
            w(f"| `{r['target']}` | {r['base']:,} | {r['cur']:,} | {_fmt_delta(r['delta'])} |")

    # --- manifest ---
    mf = result["manifest"]
    if mf["new_errors"] or mf["fixed"]:
        w("\n## manifest 校验\n")
        for it in mf["new_errors"]:
            w(f"- 🆕 `{it['plugin']}`：" + "；".join(it["errors"]))
        for it in mf["fixed"]:
            w(f"- ✅ `{it['plugin']}` 已修复：" + "；".join(it["errors"]))

    # --- 依赖 ---
    if result["deps"]:
        w("\n## 依赖变更\n")
        for it in result["deps"]:
            parts = []
            if it["added"]:
                parts.append("+" + "，+".join(it["added"]))
            if it["removed"]:
                parts.append("−" + "，−".join(it["removed"]))
            w(f"- `{it['plugin']}`：{ ' / '.join(parts) }")

    return "\n".join(L) + "\n"
