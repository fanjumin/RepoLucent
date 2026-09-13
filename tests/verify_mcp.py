# -*- coding: utf-8 -*-
"""MCP 服务化 e2e 自验证（需求3 验收，§2.3 范式）。

复用本工具自带的 subprocess_harness.JsonRpcProc 当 e2e 客户端，对真实启动的
`repolens.py mcp` 进程跑：initialize / tools/list / tools/call(repo.summary) / 未知方法
四用例，并额外验证安全红线未被放宽：
  - archived 脚本（copy_patchset）经 tools/call 须返回 not_runnable_via_api（不放宽白名单）
  - active 脚本（anchor_assert）confirm=false 须仅 dry-run，不真执行

退出码 0=全 PASS；1=有 FAIL。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# stdout 必须纯 JSON-RPC：子进程同样需要无缓冲（避免 terminate 吞尾行造假失败）
os.environ.setdefault("PYTHONUNBUFFERED", "1")

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens.scriptlib.subprocess_harness import (  # noqa: E402
    JsonRpcProc, has_result, has_error, expect_field,
)

REPO_DEFAULT = HERE / "tests" / "fixture_repo"
INSIGHT = HERE / "repolens.py"

def _content_text(resp: dict) -> str:
    try:
        return resp["result"]["content"][0]["text"]
    except Exception:
        return ""

def _content_json(resp: dict):
    t = _content_text(resp)
    try:
        return json.loads(t)
    except Exception:
        return None

def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_DEFAULT
    out = Path(tempfile.mkdtemp(prefix="mcp_verify_"))
    print(f"[verify] repo={repo}\n[verify] out ={out}")

    # 经模块入口调用（python -m repo_lens），零文件路径硬编码；
    # 包位置由 PYTHONPATH 注入（HERE 由 __file__ 推导，非写死）。
    proc = JsonRpcProc([sys.executable, "-m", "repo_lens", "mcp",
                        "--repo", str(repo), "--out", str(out)],
                       env=dict(os.environ, PYTHONPATH=str(HERE)))
    results: list[dict] = []

    def case(rid, method, params, expect, label):
        proc.send(rid, method, params)
        resp = proc.read(rid, timeout_s=180)
        ok = resp is not None and expect(resp)
        results.append({"id": rid, "label": label, "ok": ok})
        print(f"[{'PASS' if ok else 'FAIL'}] #{rid} {label}")
        return resp

    try:
        proc.start()

        # 1) 握手
        r = case(1, "initialize", {"client": "verify"}, has_result, "initialize → result")
        if r:
            si = (r.get("result") or {}).get("serverInfo", {})
            print(f"        serverInfo={si}")

        # 2) 工具清单
        r = case(2, "tools/list", None,
                 expect_field("result.tools", "__list__"), "tools/list → tools[]")
        if r:
            tools = (r.get("result") or {}).get("tools") or []
            names = [t["name"] for t in tools]
            print(f"        共 {len(tools)} 个工具：{', '.join(names)}")
            assert any(n.startswith("repo.") for n in names), "缺少核心工具"
            assert any(n.startswith("script.") for n in names), "缺少脚本工具"
            # v1.6.0 一等工具（阶段一 1-B / 1-C）
            missing = [n for n in ("repo.context", "repo.query", "repo.search")
                       if n not in names]
            if missing:
                print(f"        [FAIL] 缺少 v1.6.0 工具：{missing}")
                results[-1]["ok"] = False
            else:
                print("        v1.6.0 工具就位：repo.context / repo.query / repo.search")

        # 3) 核心分析工具真实可跑（含一次完整分析）
        r = case(3, "tools/call",
                 {"name": "repo.summary", "arguments": {}},
                 has_result, "tools/call repo.summary（真实分析）")
        if r:
            j = _content_json(r)
            if isinstance(j, dict):
                print(f"        摘要：{ {k: j.get(k) for k in ('repo','plugins','core_modules','lines_code')} }")
                assert "plugins" in j, "摘要缺少 plugins 字段"
            else:
                print(f"        警告：非预期载荷 → {_content_text(r)[:200]}")

        # 4) 未知方法 → 协议错误
        case(4, "does/not/exist", None, has_error, "未知方法 → error -32601")

        # 5) 安全红线：archived 脚本不得经 MCP 执行
        r = case(5, "tools/call",
                 {"name": "script.copy_patchset", "arguments": {}},
                 has_result, "archived 脚本 copy_patchset（须 not_runnable）")
        if r:
            j = _content_json(r)
            txt = _content_text(r)
            ok_gate = (isinstance(j, dict) and j.get("error") == "not_runnable_via_api") or ("not_runnable_via_api" in txt)
            print(f"        门控校验：{'[OK] 未被放宽' if ok_gate else '[FAIL] 门控被绕过！'} → {txt[:120]}")
            results[-1]["ok"] = results[-1]["ok"] and ok_gate

        # 6) 安全红线：active 脚本 confirm=false 仅 dry-run
        r = case(6, "tools/call",
                 {"name": "script.anchor_assert",
                  "arguments": {"confirm": False, "against": str(repo),
                                "claims": str(repo / "nonexistent_claims.json")}},
                 has_result, "active 脚本 anchor_assert confirm=false（须 dry-run）")
        if r:
            j = _content_json(r)
            if isinstance(j, dict):
                print(f"        dry_run={j.get('dry_run')} ok={j.get('ok')} error={j.get('error')}")
                assert j.get("dry_run") is True, "confirm=false 不应真执行"
            else:
                print(f"        警告：{_content_text(r)[:120]}")

        # ---- v1.6.0 阶段一 1-B：repo.context 往返（字节上限校验）----
        r = case(7, "tools/call",
                 {"name": "repo.context",
                  "arguments": {"target": "plugin:demo", "depth": "brief"}},
                 has_result, "repo.context plugin:demo depth=brief")
        if r:
            txt = _content_text(r)
            err = bool((r.get("result") or {}).get("isError"))
            nbytes = len(txt.encode("utf-8"))
            ok_ctx = (not err and "Context" in txt and "## 标识" in txt
                      and nbytes < 12288)
            print(f"        bytes={nbytes} isError={err} "
                  f"has_sections={'## 路由' in txt}")
            results[-1]["ok"] = results[-1]["ok"] and ok_ctx

        # ---- v1.6.0：repo.context 目标不存在 → content 错误（不放行、不崩流）----
        r = case(8, "tools/call",
                 {"name": "repo.context",
                  "arguments": {"target": "plugin:no_such_plugin_xyz"}},
                 has_result, "repo.context 未知目标 → content 错误")
        if r:
            txt = _content_text(r)
            err = bool((r.get("result") or {}).get("isError"))
            print(f"        isError={err} msg={txt[:90]}")
            results[-1]["ok"] = results[-1]["ok"] and err and "找不到目标" in txt

        # ---- v1.6.0：repo.query 往返（与 CLI 同一执行面）----
        r = case(9, "tools/call",
                 {"name": "repo.query",
                  "arguments": {"select": "identifier,version"}},
                 has_result, "repo.query select=identifier,version")
        if r:
            j = _content_json(r)
            rows = (j or {}).get("rows") if isinstance(j, dict) else None
            print(f"        count={(j or {}).get('count')} rows={rows}")
            results[-1]["ok"] = (results[-1]["ok"] and isinstance(rows, list)
                                 and len(rows) >= 1
                                 and all("identifier" in x for x in rows))

        # ---- v1.6.0 阶段一 1-C：repo.search 往返 ----
        r = case(10, "tools/call",
                 {"name": "repo.search", "arguments": {"symbol": "DemoPlugin"}},
                 has_result, "repo.search exact hit")
        if r:
            j = _content_json(r)
            hits = (j or {}).get("hits") if isinstance(j, dict) else None
            ok_s = (isinstance(hits, list) and hits
                    and hits[0].get("kind") == "class"
                    and hits[0].get("file", "").replace("\\", "/").endswith(
                        "plugins/demo/__init__.py")
                    and isinstance(hits[0].get("line"), int))
            print(f"        match={(j or {}).get('match')} count={(j or {}).get('count')} "
                  f"first={hits[0] if hits else None}")
            results[-1]["ok"] = results[-1]["ok"] and ok_s

        # ---- v1.6.0：repo.search 未命中 → 空结果（非错误）----
        r = case(11, "tools/call",
                 {"name": "repo.search",
                  "arguments": {"symbol": "definitely_absent_symbol_xyz"}},
                 has_result, "repo.search miss → 空结果非错误")
        if r:
            j = _content_json(r)
            err = bool((r.get("result") or {}).get("isError"))
            print(f"        isError={err} count={(j or {}).get('count')} "
                  f"truncated={(j or {}).get('truncated')}")
            results[-1]["ok"] = (results[-1]["ok"] and not err
                                 and isinstance(j, dict) and j.get("count") == 0)
    finally:
        proc.close()

    fails = [r for r in results if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails else f"失败 {len(fails)} 项"))

    # 落盘纯 ASCII 结果（ensure_ascii=True → 无 NUL/中文干扰，便于工具回读）
    try:
        summary = {
            "exit": 1 if fails else 0,
            "cases": results,
        }
        (HERE / "out" / "mcp_verify.json").write_text(
            json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[verify] 结果落盘失败：{e}")

    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
