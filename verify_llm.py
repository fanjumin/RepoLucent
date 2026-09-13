# -*- coding: utf-8 -*-
"""P2 LLM 集成自验证（实跑，非纸面）。

用例设计对齐设计文档 §3.3 验收：
1. 无模型配置 → 明确降级（ran=False + reason），不崩
2. 有模型但缺密钥环境变量 → 降级，且 reason 提示「只允许环境变量注入」
3. 注入假 provider 返回合法 JSON → ran=True，findings 经既有 load_items 校验回灌
4. 只加严不放行（LLM-2）：语义发现 severity=blocking 时，
   semantic_can_block=False → 不增加 blocking；=True → 才纳入
5. 工具回环复用 MCP（§3.2-2）：假 provider 发起工具调用 → 真实启动
   `python -m repo_lens mcp` 子进程执行 repo.summary，走同一 tools/call
6. 熔断与密钥脱敏（LLM-1/LLM-3）：不可达网关 → 重试后降级；repr 不含明文密钥
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPO_DEFAULT = HERE / "tests" / "fixture_repo"


def _write_settings(d: Path, obj: dict) -> str:
    p = d / "settings.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def main() -> int:
    results: list[dict] = []

    from repo_lens.cli import _setup, _build_argparser
    from repo_lens.llm import runner as LR
    from repo_lens.llm.providers.base import ChatResult, Provider, ToolCall
    from repo_lens.llm.providers.openai_compat import OpenAICompat
    from repo_lens.settings import load_settings

    # ---------------- 用例 1：无模型配置 → 降级 ----------------
    with tempfile.TemporaryDirectory() as td:
        os.environ["REPO_LENS_SETTINGS"] = _write_settings(
            Path(td), {"llm_enabled": False, "models": []})
        load_settings()  # 触发重读
        run = LR.run_semantic_audit(None, None, None)
        ok1 = (not run.ran) and bool(run.reason)
        results.append({"case": "degrade_no_models", "ok": ok1,
                        "detail": {"ran": run.ran, "reason": run.reason}})

    # ---------------- 用例 2：有模型但缺密钥 → 降级 ----------------
    try:
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("REPO_LENS_TEST_LLM_KEY", None)
            os.environ.pop("REPO_LENS_TEST_LLM_BASE", None)
            os.environ["REPO_LENS_SETTINGS"] = _write_settings(Path(td), {
                "llm_enabled": True,
                "models": [{"name": "test", "provider": "openai_compat",
                            "model": "qwen-test",
                            "key_env": "REPO_LENS_TEST_LLM_KEY",
                            "base_url_env": "REPO_LENS_TEST_LLM_BASE"}],
            })
            run = LR.run_semantic_audit(None, None, None)
            ok2 = ((not run.ran) and "REPO_LENS_TEST_LLM_KEY" in (run.reason or "")
                   and "环境变量" in (run.reason or ""))
            results.append({"case": "degrade_missing_key_env", "ok": ok2,
                            "detail": {"ran": run.ran, "reason": run.reason}})
    finally:
        os.environ.pop("REPO_LENS_SETTINGS", None)

    # ---------------- 准备真实 cfg / data（用例 3、4、5 共用）----------------
    out_dir = HERE / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    args = _build_argparser().parse_args(
        ["audit", "--repo", str(REPO_DEFAULT), "--out", str(out_dir)])
    cfg = _setup(args)
    from repo_lens.cli import _analyze
    import types as _t
    ns = _t.SimpleNamespace(no_cache=False, deterministic=False)
    data, _dur, _pc = _analyze(ns, cfg)
    from repo_lens.audit import engine as AE
    dims = list(AE.DEFAULT_DIMS)

    # ---------------- 用例 3：假 provider 合法 JSON → 回灌 findings ----------------
    class FakeProvider(Provider):
        name = "fake"
        supports_tools = True
        model = "fake-model"

        def __init__(self, responses):
            self.responses = list(responses)
            self.calls: list[dict] = []

        def chat(self, messages, tools=None, model=None, temperature=0.0, timeout_s=None):
            self.calls.append({"n_messages": len(messages), "has_tools": bool(tools)})
            r = self.responses.pop(0) if self.responses else ChatResult(content="{}")
            return r

    good_json = json.dumps({"findings": [
        {"rule_id": "AIB004", "dimension": "ai_business", "severity": "blocking",
         "title": "测试语义发现", "file": "repo_lens/demo.py", "line": 12,
         "why": "测试证据", "confidence": "high", "fix": "测试修复"},
        {"rule_id": "NOPE999", "file": "x.py", "why": "未知规则应被拒"},
    ]}, ensure_ascii=False)

    fake = FakeProvider([ChatResult(content="```json\n" + good_json + "\n```")])
    run3 = LR.run_semantic_audit(cfg, data, dims, provider=fake, use_tools=False)
    ok3 = (run3.ran and len(run3.findings) == 1 and len(run3.rejects) == 1)
    results.append({"case": "fake_provider_parses_findings", "ok": ok3,
                    "detail": {"ran": run3.ran, "findings": len(run3.findings),
                               "rejects": run3.rejects, "model": run3.model}})

    # ---------------- 用例 4：只加严不放行（LLM-2）----------------
    sem_findings = run3.findings
    rep_no_sem = AE.run_audit(cfg, data, cfg.repo_root, dims=dims,
                              fail_on_severity="blocking", use_semantic=False,
                              semantic_findings=None, semantic_can_block=False)
    rep_consult = AE.run_audit(cfg, data, cfg.repo_root, dims=dims,
                               fail_on_severity="blocking", use_semantic=True,
                               semantic_findings=sem_findings, semantic_can_block=False)
    rep_block = AE.run_audit(cfg, data, cfg.repo_root, dims=dims,
                             fail_on_severity="blocking", use_semantic=True,
                             semantic_findings=sem_findings, semantic_can_block=True)
    ai_dim = "ai_business"
    sem_ran = bool(ai_dim in rep_consult.coverage
                   and rep_consult.coverage[ai_dim].semantic.ran)
    not_increased = rep_consult.verdict.blocking == rep_no_sem.verdict.blocking
    increased_when_allowed = rep_block.verdict.blocking == rep_no_sem.verdict.blocking + 1
    ok4 = bool(sem_ran and not_increased and increased_when_allowed)
    results.append({
        "case": "semantic_only_stiffens_never_clears", "ok": ok4,
        "detail": {"coverage_semantic_ran": sem_ran,
                   "blocking_without_semantic": rep_no_sem.verdict.blocking,
                   "blocking_consult_only": rep_consult.verdict.blocking,
                   "blocking_when_can_block": rep_block.verdict.blocking},
    })

    # ---------------- 用例 5：工具回环复用真实 MCP 子进程 ----------------
    ok5 = False
    detail5: dict = {}
    try:
        from repo_lens.llm.loopback import McpLoopback
        lb = McpLoopback(cfg.repo_root, cfg.out_dir)
        lb.start()
        try:
            tools = lb.list_tools()
            names = [t["name"] for t in tools]
            has_summary = "repo.summary" in names
            # 假 provider 首轮发起工具调用（OpenAI 名为 insight_summary，需还原）
            call_resp = ChatResult(content="", tool_calls=[
                ToolCall(id="call_1", name="insight_summary", arguments={})])
            final = ChatResult(content=json.dumps({"findings": []}))
            fake2 = FakeProvider([call_resp, final])
            run5 = LR.run_semantic_audit(cfg, data, dims, provider=fake2,
                                         use_tools=True, loopback=lb)
            ok5 = bool(has_summary and run5.ran and run5.tool_calls >= 1
                       and run5.rounds == 2)
            detail5 = {"tools_seen": len(names), "has_insight_summary": has_summary,
                       "ran": run5.ran, "tool_calls": run5.tool_calls,
                       "rounds": run5.rounds}
        finally:
            lb.close()
    except Exception as e:  # noqa: BLE001
        detail5 = {"error": f"{type(e).__name__}: {e}"}
    results.append({"case": "tool_loopback_via_real_mcp", "ok": ok5, "detail": detail5})

    # ---------------- 用例 6：熔断 + 密钥脱敏 ----------------
    secret = "sk-UNITTEST-DO-NOT-LEAK"
    prov = OpenAICompat(base_url="http://127.0.0.1:9", api_key=secret,
                        model="m", timeout_s=1.0, max_retries=1,
                        circuit_threshold=1, circuit_cooldown_s=30)
    raised = False
    try:
        prov.chat([{"role": "user", "content": "hi"}])
    except Exception as e:  # noqa: BLE001
        raised = isinstance(e, type(e)) and "熔断" not in str(e)
    circuit_open = prov.circuit_open
    masked = (secret not in repr(prov)) and ("***set***" in repr(prov))
    # 熔断开启后再调用应快速失败
    fast_fail = False
    try:
        prov.chat([{"role": "user", "content": "hi"}])
    except Exception as e:  # noqa: BLE001
        fast_fail = "熔断" in str(e)
    # runner 用该 provider → 应降级而非抛出
    run6 = LR.run_semantic_audit(cfg, data, dims, provider=prov, use_tools=False)
    ok6 = bool(raised and circuit_open and masked and fast_fail
               and (not run6.ran) and bool(run6.reason))
    results.append({"case": "circuit_breaker_and_key_masking", "ok": ok6,
                    "detail": {"first_call_failed": raised,
                               "circuit_open": circuit_open,
                               "key_masked_in_repr": masked,
                               "second_call_fast_failed": fast_fail,
                               "runner_degraded": not run6.ran,
                               "reason": run6.reason}})

    # ---------------- 汇总 ----------------
    fails = [r for r in results if not r["ok"]]
    summary = {"exit": 1 if fails else 0, "cases": results}
    (HERE / "out" / "llm_verify.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    for r in results:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['case']}")
    print(("ALL PASS" if not fails else f"FAILED {len(fails)}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
