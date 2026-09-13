# -*- coding: utf-8 -*-
"""子进程往返协议验证模板（通用化自 AI 脚本 test_mcp_roundtrip.py）。

价值：对一个"通过 stdin/stdout 收发的 JSON-RPC 子进程服务"，跑一组请求并断言应答，
无需任何被测服务的具体依赖即可复用。原始脚本硬绑 stock-analysis 的 mcp_server.py 与
numpy；本模板把『启动命令 / 用例(方法+期望谓词)』参数化，并内置一个自给自足的 demo：
现起一个最小 JSON-RPC 服务（`python -c`）验证 initialize/tools/未知方法 三类行为。

作为库导入：
    from repo_lens.scriptlib.subprocess_harness import JsonRpcProc, roundtrip, expect
    proc = JsonRpcProc([sys.executable, "server.py"])
    ok = roundtrip(proc, [("initialize", {}, expect.has_result), ...])

CLI（跑内置 demo，证明往返机制可用）：
    python -m repo_lens.scriptlib.subprocess_harness --demo
    repolens.py script run subprocess_harness

退出码：0=全部用例通过；1=有失败；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from typing import Callable, Optional


# -- 期望谓词 -------------------------------------------------------
def has_result(resp: dict) -> bool:
    return isinstance(resp, dict) and "result" in resp


def has_error(resp: dict) -> bool:
    return isinstance(resp, dict) and "error" in resp


def expect_field(path: str, value) -> Callable[[dict], bool]:
    """按点分路径断言字段值，如 expect_field('result.tools', ...) 或 len 特例。"""
    def fn(resp: dict) -> bool:
        cur = resp
        for k in path.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return False
        if value == "__list__":
            return isinstance(cur, list)
        return cur == value
    return fn


class JsonRpcProc:
    """一个 stdio JSON-RPC 子进程的轻量封装：send(id,method,params) → read(id,timeout)。"""

    def __init__(self, cmd: list[str], env: Optional[dict] = None):
        self._cmd = cmd
        self._env = env
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=self._env, bufsize=1, text=True,
            encoding="utf-8", errors="replace")

    def notify(self, method: str, params: Optional[dict] = None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def send(self, rid: int, method: str, params: Optional[dict] = None):
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def read(self, want_id: int, timeout_s: float = 10.0) -> Optional[dict]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (ValueError, TypeError):
                continue
            if msg.get("id") == want_id:
                return msg
        return None

    def close(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def roundtrip(proc: JsonRpcProc, cases: list[tuple[int, str, Optional[dict], Callable[[dict], bool]]]) -> list[dict]:
    """按顺序 send→read 并跑期望谓词。返回 [{id,method,status,detail}]。"""
    results = []
    for rid, method, params, pred in cases:
        proc.send(rid, method, params)
        resp = proc.read(rid)
        ok = resp is not None and pred(resp)
        results.append({"id": rid, "method": method, "status": "PASS" if ok else "FAIL",
                        "detail": (None if resp is None else json.dumps(resp, ensure_ascii=False)[:120])})
    return results


# -- 内置 demo：自给自足的最小 JSON-RPC 服务源码 -------------------
_DEMO_SERVER = r'''
import sys, json
TOOLS = ["ping"]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    rid = msg.get("id")
    m = msg.get("method")
    if rid is None:                     # 通知：不回（验证不挂起）
        continue
    if m == "initialize":
        out = {"result": {"name": "demo", "protocolVersion": "1"}}
    elif m == "tools/list":
        out = {"result": {"tools": TOOLS}}
    elif m == "tools/call":
        name = (msg.get("params") or {}).get("name")
        if name == "ping":
            out = {"result": {"content": "pong"}}
        else:
            out = {"result": {"content": "unknown tool", "isError": True}}  # 优雅降级
    else:
        out = {"error": {"code": -32601, "message": "method not found"}}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, **out}) + "\n")
    sys.stdout.flush()
'''


def _demo_cases() -> list:
    return [
        (1, "initialize", {"client": "test"}, has_result),
        # 通知不带 id：read 应跳过它继续拿到下一条应答（验证不挂起）
        (2, "tools/list", None, expect_field("result.tools", "__list__")),
        (3, "tools/call", {"name": "ping"}, has_result),
        (4, "does/not/exist", None, has_error),
    ]


def _run_demo() -> int:
    import os
    proc = JsonRpcProc([sys.executable, "-c", _DEMO_SERVER], env=dict(os.environ))
    proc.start()
    try:
        # 先发一个 initialized 通知（无 id），验证服务不因此挂起：后续 read 仍能拿到应答
        proc.notify("notifications/initialized", {})
        results = roundtrip(proc, _demo_cases())
    finally:
        proc.close()
    fails = [r for r in results if r["status"] == "FAIL"]
    for r in results:
        print(f"[{r['status']}] id={r['id']} {r['method']}  {r['detail'] or ''}")
    print("—— subprocess_harness demo 结束 ——" if not fails else f"失败 {len(fails)} 项")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="subprocess_harness",
        description="子进程 JSON-RPC 往返验证模板（harness）；CLI 跑内置 demo，实际项目用库接口。")
    ap.add_argument("--demo", action="store_true", help="运行内置最小 JSON-RPC 往返自检")
    resolved = sys.argv[1:] if argv is None else list(argv)
    args = ap.parse_args(resolved)
    if not resolved or args.demo:
        return _run_demo()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
