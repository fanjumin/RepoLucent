# -*- coding: utf-8 -*-
"""改动锚点断言器（通用化自 AI 审计脚本 anchors.py）。只读，不改任何文件。

用途：把"报告/补丁示例所引用的改动锚点"外置成清单，逐条对真实仓库断言
      —— 文件存在、且期望的原文子串仍然在（可选：仍在指定行号）。
任何一条 FAIL 都意味着文档里的补丁示例会打歪，必须先修文档再交付。
这正是"读真实文件 → 断言锚点存在 → 打同款补丁"方法论的可复用工具化。

claims 清单格式（JSON 数组，每项）：
    {"id":"A01", "file":"plugins/x/plugin.json", "must_contain":"\"version\": \"1.6.2\"",
     "line":12(可选，1 基行号), "note":"版本号"}
也兼容 {"id","file","contains"} 简写。

用法：
    python -m repo_lens.scriptlib.anchor_assert --against <repo> --claims claims.json [--json] [--stop-on-first]
    repolens.py script run anchor_assert --against <repo> --claims claims.json

退出码：0=全 PASS；1=有 FAIL；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _norm_claim(c: dict) -> dict:
    mc = c.get("must_contain", c.get("contains"))
    if mc is None:
        raise ValueError(f"锚点 {c.get('id','?')} 缺 must_contain/contains")
    return {
        "id": c.get("id", "?"),
        "file": c["file"],
        "must_contain": mc,
        "line": c.get("line"),
        "note": c.get("note", ""),
    }


def check(repo: Path, claims: list[dict]) -> list[dict]:
    """逐条断言。返回 [{id,file,status,reason}]；status∈PASS/FAIL。"""
    results = []
    text_cache: dict[str, list[str]] = {}
    for c in claims:
        p = repo / c["file"]
        if not p.is_file():
            results.append({**c, "status": "FAIL", "reason": "文件不存在"})
            continue
        rel = str(c["file"])
        if rel not in text_cache:
            try:
                text_cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                text_cache[rel] = []
                results.append({**c, "status": "FAIL", "reason": f"读取失败:{e}"})
                continue
        lines = text_cache[rel]
        needle = c["must_contain"]
        if c["line"]:
            idx = c["line"] - 1
            ok = 0 <= idx < len(lines) and needle in lines[idx]
            reason = ("锚点原文与行号匹配" if ok
                      else f"第 {c['line']} 行未含期望片段（或行越界）")
        else:
            found = any(needle in ln for ln in lines)
            ln_no = next((i + 1 for i, ln in enumerate(lines) if needle in ln), None)
            ok = found
            reason = (f"命中于第 {ln_no} 行" if found else "全文件未找到期望片段")
        results.append({**c, "status": "PASS" if ok else "FAIL", "reason": reason})
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="anchor_assert",
        description="对真实仓库逐条断言改动锚点原文仍存在（只读）。")
    ap.add_argument("--against", required=True, help="目标仓库根目录")
    ap.add_argument("--claims", required=True, help="锚点清单 JSON 文件路径")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--stop-on-first", action="store_true", help="遇到首个 FAIL 即停")
    try:
        args = ap.parse_args(argv)
        repo = Path(args.against).resolve()
        if not repo.is_dir():
            print(f"[anchor_assert] 仓库目录不存在: {repo}", file=sys.stderr)
            return 2
        raw = json.loads(Path(args.claims).read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            print("[anchor_assert] claims 须为非空 JSON 数组", file=sys.stderr)
            return 2
        claims = [_norm_claim(c) for c in raw]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[anchor_assert] 参数/环境错误：{e}", file=sys.stderr)
        return 2

    if args.stop_on_first:
        results, fails = [], 0
        for one in claims:
            r = check(repo, [one])[0]
            results.append(r)
            if r["status"] == "FAIL":
                fails += 1
                break
    else:
        results = check(repo, claims)
        fails = sum(1 for r in results if r["status"] == "FAIL")

    if args.json:
        print(json.dumps({"repo": str(repo), "total": len(results), "fail": fails,
                          "results": results}, ensure_ascii=False))
    else:
        for r in results:
            mark = "PASS" if r["status"] == "PASS" else "FAIL"
            print(f"[{mark}] {r['id']:>6}  {r['file']}  —  {r['reason']}"
                  + (f"  ({r['note']})" if r["note"] else ""))
        print(f"—— 共 {len(results)} 条，FAIL {fails} 条 ——")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
