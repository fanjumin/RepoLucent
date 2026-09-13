# -*- coding: utf-8 -*-
"""变更热点分析：借鉴 code-maat / CodeScene 的 hotspot 方法论。

原理：频繁被改（churn）且体量大（LOC）的文件 = 最容易藏缺陷、最该重构的地方。
- churn：近 N 天 git 历史中该文件被提交改动的次数（numstat 聚合）。
- 体量：当前文件的代码行。
- 排序：churn 降序为主、LOC 降序为辅；churn>=阈值 且 LOC>=阈值 标记为高风险。

纯标准库 + subprocess 调 git；git 不可用 / 非 git 仓库时返回 None，不中断主流程。

------------------------------------------------------------- 增量加速（v1.8.0）
全量 ``git log --numstat --since=90.days`` 是大仓库上最大的单项开销（实测 27.8 万行
的 VeroRun 仓库约 10s，占墙钟一半以上）。本模块引入两级加速，二者都是**加速层**：

1. **HEAD 短路**：churn 是纯历史派生量。缓存记录上次分析的 HEAD sha 与窗口下界，
   HEAD 未变则 churn 必然不变 → 零 git 调用直接复用。
2. **增量合并**：HEAD 变了但旧 sha 仍是新 HEAD 的祖先时，只取 ``old..new`` 的新增
   numstat，并对「滑出 N 天窗口」的时间区间做减法::

       new = (old − 滑出区间) + 新增区间

   数学上与全量 ``--since=now-N.days`` 等价（两侧同用 git 默认的 committer date 口径：
   新增区间加 ``--since=new_cutoff`` 过滤，滑出区间取 ``[old_cutoff, new_cutoff)``）。
   旧 sha 不再是祖先（rebase / force-push）→ 自动回退全量。

任一步失败一律回退全量：**加速层可牺牲，事实源不可损**。
结果新增 ``source`` 字段（``head_hit`` / ``incremental`` / ``full``）便于诊断。
"""
from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .config import CODE_EXTS, ROOT_LOCAL_SCRIPT_PREFIXES
from .fs_scan import count_lines, read_text_safe

#: 高风险带阈值
HIGH_CHURN = 5
HIGH_LOC = 800

#: 统计回溯天数（默认与快照口径一致：近 90 天）
DEFAULT_DAYS = 90

#: 热点缓存结构版本。改动缓存字段语义时必须递增（旧缓存整体作废并回退全量）。
HOTSPOT_CACHE_VERSION = 1

#: 缓存文件名（落在 cfg.cache_dir，与 AST 缓存同稳定根）
_CACHE_NAME = "hotspot.json"


def _run_git(root: Path, args: list[str], timeout: int = 120) -> str | None:
    """执行 git 子命令并返回 stdout；任何失败返回 None（调用方据此回退）。"""
    try:
        r = subprocess.run(["git", "-C", str(root)] + args,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _iso_utc(ts: float) -> str:
    """epoch 秒 → ISO 8601（带 UTC 偏移），供 git --since/--until 使用。"""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def git_available(root: Path) -> bool:
    return _run_git(root, ["rev-parse", "--git-dir"], timeout=10) is not None


def _head_sha(root: Path) -> str | None:
    """当前 HEAD 的 commit sha；空仓库 / detached 失败时返回 None。"""
    out = _run_git(root, ["rev-parse", "--verify", "HEAD"], timeout=15)
    if out is None:
        return None
    sha = out.strip()
    return sha or None


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """判断 ancestor 是否为 descendant 的祖先（增量合并的适用前提）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor",
             ancestor, descendant], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _is_relevant(rel: str, top_excludes: set) -> bool:
    """文件是否应纳入热点统计（与全仓统计口径一致）。"""
    p = Path(rel)
    if p.suffix.lower() not in CODE_EXTS:
        return False
    top = p.parts[0] if len(p.parts) > 1 else "<root>"
    if top in top_excludes or top.startswith("."):
        return False
    if top == "<root>":
        name = p.name
        if name not in config.ROOT_ENTRY_ALLOWLIST:
            for prefix in ROOT_LOCAL_SCRIPT_PREFIXES:
                if name.lower().startswith(prefix):
                    return False
    return True


def _numstat(root: Path, extra: list[str], top_excludes: set
             ) -> tuple[dict, dict] | None:
    """按给定 git log 参数聚合 numstat → (churn, added)。失败返回 None。

    全量与增量共用同一聚合口径，是「增量结果 == 全量结果」这一性质的前提。
    """
    out = _run_git(root, ["-c", "core.quotepath=false", "log", "--numstat",
                          "--pretty=format:", "--no-renames"] + extra)
    if out is None:
        return None
    churn: dict[str, int] = {}
    added: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] == "-":
            continue
        rel = parts[2]
        if not _is_relevant(rel, top_excludes):
            continue
        try:
            a = int(parts[0])
        except ValueError:
            continue
        churn[rel] = churn.get(rel, 0) + 1
        added[rel] = added.get(rel, 0) + a
    return churn, added


def _combine(base_churn: dict, base_added: dict, plus_churn: dict, plus_added: dict,
             minus_churn: dict, minus_added: dict) -> tuple[dict, dict]:
    """增量合并：``(base − minus) + plus``；计数降到 0 的条目直接移除。"""
    churn = dict(base_churn)
    added = dict(base_added)
    for k, v in plus_churn.items():
        churn[k] = churn.get(k, 0) + v
    for k, v in plus_added.items():
        added[k] = added.get(k, 0) + v
    for k, v in minus_churn.items():
        nv = churn.get(k, 0) - v
        if nv > 0:
            churn[k] = nv
        else:
            churn.pop(k, None)
    for k, v in minus_added.items():
        nv = added.get(k, 0) - v
        if nv > 0:
            added[k] = nv
        else:
            added.pop(k, None)
    return churn, added


def _load_cache(cfg, days: int) -> dict | None:
    """读取热点缓存；结构不符 / 损坏 / 天数变更 → None（回退全量）。"""
    try:
        d = json.loads((Path(cfg.cache_dir) / _CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(d, dict)
            or d.get("version") != HOTSPOT_CACHE_VERSION
            or d.get("days") != days
            or not isinstance(d.get("churn"), dict)
            or not isinstance(d.get("added"), dict)
            or not isinstance(d.get("cutoff_ts"), (int, float))
            or not d.get("head")):
        return None
    return d


def _save_cache(cfg, days: int, head: str, cutoff_ts: float,
                churn: dict, added: dict) -> None:
    """写回热点缓存；失败静默（缓存只是加速层）。"""
    payload = {"version": HOTSPOT_CACHE_VERSION, "days": days, "head": head,
               "cutoff_ts": cutoff_ts, "churn": churn, "added": added}
    try:
        d = Path(cfg.cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / _CACHE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _merge_incremental(root: Path, cached: dict, head: str, new_cutoff_ts: float,
                       top_excludes: set) -> tuple[dict, dict] | None:
    """增量路径：旧缓存 + 新增区间 − 滑出区间。任一步失败返回 None（回退全量）。"""
    old_head = cached["head"]
    old_cutoff_ts = cached["cutoff_ts"]

    # 1) 新增区间：old..new，且只保留仍在窗口内的 commit（committer date）。
    plus = _numstat(root, ["--since=" + _iso_utc(new_cutoff_ts),
                           f"{old_head}..{head}"], top_excludes)
    if plus is None:
        return None

    # 2) 滑出区间：[old_cutoff, new_cutoff)。--until 为闭区间，故上界取 -1s。
    if new_cutoff_ts - 1.0 > old_cutoff_ts:
        minus = _numstat(root, ["--since=" + _iso_utc(old_cutoff_ts),
                                "--until=" + _iso_utc(new_cutoff_ts - 1.0)],
                         top_excludes)
        if minus is None:
            return None
    else:
        minus = ({}, {})                     # 两次分析间隔不足 1s：无滑出

    return _combine(cached["churn"], cached["added"],
                    plus[0], plus[1], minus[0], minus[1])


def _note(days: int) -> str:
    return ("churn=近 %d 天改动次数；high_risk = churn≥%d 且 LOC≥%d（最该重构的文件带）"
            % (days, HIGH_CHURN, HIGH_LOC))


def analyze_hotspots(cfg, days: int = DEFAULT_DAYS,
                     top_files: int = 20, top_groups: int = 12,
                     parse_cache: dict | None = None,
                     use_cache: bool = True) -> dict | None:
    """分析仓库变更热点。返回结构化结果；git 不可用时返回 None。

    ``use_cache=False``（对应 CLI ``--no-cache``）时忽略热点缓存，强制全量重建。
    """
    root = cfg.repo_root
    if not git_available(root):
        return None

    exclude_tops = set(cfg.exclude_dirs)
    head = _head_sha(root)
    new_cutoff_ts = time.time() - days * 86400

    cached = _load_cache(cfg, days) if use_cache else None
    churn: dict | None = None
    added: dict = {}
    cutoff_ts = new_cutoff_ts
    source = "full"

    if cached and head:
        if cached["head"] == head:
            # ① HEAD 短路：零 git 调用。
            churn = dict(cached["churn"])
            added = dict(cached["added"])
            cutoff_ts = cached["cutoff_ts"]
            source = "head_hit"
        elif _is_ancestor(root, cached["head"], head):
            # ② 增量合并（失败自动落到下面的全量分支）。
            merged = _merge_incremental(root, cached, head, new_cutoff_ts,
                                        exclude_tops)
            if merged is not None:
                churn, added = merged
                source = "incremental"

    if churn is None:
        # ③ 全量：沿用历史口径（相对语法 N.days），保证与 v1.7.0 前结果一致。
        got = _numstat(root, [f"--since={days}.days"], exclude_tops)
        if got is None:
            return None
        churn, added = got
        cutoff_ts = new_cutoff_ts
        source = "full"

    if use_cache and head:
        _save_cache(cfg, days, head, cutoff_ts, churn, added)

    if not churn:
        return {"days": days, "git_ok": True, "source": source,
                "note": _note(days), "files_top": [], "groups_top": []}

    # 并入当前体量（复用 parse_cache 避免重复读文件）。
    # 注意：LOC 是「当前体量」，与 churn 的历史口径无关，故缓存命中时仍重算。
    rows = []
    for rel, c in churn.items():
        fpath = root / rel
        if not fpath.exists():
            continue
        entry = parse_cache.get(rel) if parse_cache else None
        if entry and isinstance(entry.get("loc_code"), int):
            loc = entry["loc_code"]
        else:
            text = read_text_safe(fpath)
            _, loc = count_lines(text)
        score = c * math.sqrt(max(loc, 1)) / 10
        rows.append({
            "file": rel,
            "churn": c,
            "added_lines": added.get(rel, 0),
            "loc": loc,
            "score": round(score, 1),
            "high_risk": c >= HIGH_CHURN and loc >= HIGH_LOC,
            "group": _group_of(rel),
        })

    rows.sort(key=lambda r: (-r["churn"], -r["loc"]))

    # 按插件/模块聚合
    gsum: dict[str, dict] = {}
    for r in rows:
        g = r["group"]
        e = gsum.setdefault(g, {"churn": 0, "added_lines": 0, "loc": 0,
                                "files": 0, "high_risk_files": 0})
        e["churn"] += r["churn"]
        e["added_lines"] += r["added_lines"]
        e["loc"] += r["loc"]
        e["files"] += 1
        e["high_risk_files"] += 1 if r["high_risk"] else 0
    groups = [{"group": k, **v} for k, v in
              sorted(gsum.items(), key=lambda kv: -kv[1]["churn"])]

    return {
        "days": days,
        "git_ok": True,
        "source": source,
        "note": _note(days),
        "files_top": rows[:top_files],
        "groups_top": groups[:top_groups],
    }


def _group_of(rel: str) -> str:
    """把文件归组到 插件 / 核心模块 / 根 / 其他。"""
    p = Path(rel)
    if len(p.parts) >= 3 and p.parts[0] == "plugins":
        return f"plugins/{p.parts[1]}"
    if len(p.parts) >= 1 and p.parts[0] in (
            "plugin_manager", "orchestrator", "agent_matrix", "shared",
            "i18n", "providers", "auth-center", "admin", "main_site",
            "health_service", "veroguard", "sdks"):
        return p.parts[0]
    return p.parts[0] if len(p.parts) > 1 else "<root>"


def render_mermaid(data: dict) -> str:
    """把 插件↔核心 依赖渲染为 Mermaid graph（madge / dependency-cruiser 风格）。

    设计：组级聚合边（PLG ==> 核心）避免 37 条散边把图搞成蜘蛛网；
    插件→插件的真实依赖逐条画（数量有限）。
    """
    inter = data["interactions"]
    plugins = data["plugins"]["items"]

    core_import = {x["module"]: x["import_count"]
                   for x in inter.get("plugins_import_core", [])}
    hot_core = [k for k, _ in sorted(core_import.items(),
                                     key=lambda kv: -kv[1])[:6]]

    top_loc = sorted(plugins, key=lambda p: -p["loc"])[:16]
    dep_edges = inter.get("plugin_to_plugin", [])
    dep_ids = {d for e in dep_edges for d in [e["from"]] + list(e.get("to") or [])}
    pick = [p["identifier"] for p in top_loc]
    for pid in sorted(dep_ids):
        if pid not in pick:
            pick.append(pid)

    L = ["graph LR"]
    L.append("  subgraph CORE[系统核心]")
    for c in hot_core:
        L.append(f"    {c}")
    L.append("  end")
    L.append("  subgraph PLG[业务插件]")
    for pid in pick:
        L.append(f"    {pid}")
    L.append("  end")
    for c in hot_core:
        L.append(f"  PLG ==> {c}")
    for e in dep_edges:
        for t in e.get("to") or []:
            L.append(f"  {e['from']} --> {t}")
    L.append("  classDef corebox fill:#eef2f7,stroke:#666;")
    L.append("  class " + ",".join(hot_core) + " corebox")
    return "\n".join(L) + "\n"
