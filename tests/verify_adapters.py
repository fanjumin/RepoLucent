# -*- coding: utf-8 -*-
"""v1.8.0 阶段三 3-C（OPEN-A / EXT-2、OPEN-B / EXT-5）验收：多 kind 适配器收口。

把 EXT-1/2/3 的 registry 多 kind 从 PARTIAL 收口为 DONE：
  kind=builtin（进程内 importlib）/ mcp（outbound JSON-RPC）
  + **binary**（子进程执行注册表声明的可执行程序，EXT-2）
  + **python_pkg**（子进程 `python -m <第三方包>`，EXT-3）

覆盖矩阵：
  1) 注册表四种 kind 齐全，且未注册 kind 仍被拒（回归保护：verify_mcp_outbound 的断言）
  2) binary：正常执行 → 退出 0、stdout/JSON 捕获
  3) binary：**无 shell 注入面**——含 `$(...)` 的参数被原样传递（shell=True 会被替换）
  4) binary：argv 限长生效（结构断言 meta['argv'] 长度）
  5) binary：契约外退出码归一（127 → 2）且 raw_exit_code 留存
  6) binary：未能启动（命令不存在）→ 2 spawn_failed（环境错，非工具判问题）
  7) binary：超时 → 2 timeout（不挂死调用方）
  8) binary：缺 command → AdapterError → dispatch 归 exit 2
  9) python_pkg：缺 module / 非法模块名（`-c`、路径、数字开头）→ 拒绝执行
 10) python_pkg：未安装的包 → 2 package_missing，且提示可操作
 11) python_pkg：已安装模块真实跑通（`python -m json.tool --help`）→ 0
 12) EXT-5 纯函数映射表：0/1/2/3 恒等，126/127 → 2，负数与其它 → 3
 13) EXT-5 端到端：builtin 脚本返回 127 → exit_code=2 且 meta 记 raw_exit_code=127；
     返回 1/2 时**不被改写**（既有语义不变）
 14) 门控现状固化：binary / python_pkg 条目**当前不可经 serve 执行**
     （run_script_api 对非 tool/mcp 的 kind 一律 not_runnable_via_api，
     属刻意 fail-closed，非疏漏）

启动方式：python verify_adapters.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/adapters_verify.json。
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lens.scriptlib.adapters import ADAPTERS, dispatch            # noqa: E402
from repo_lens.scriptlib.adapters.base import normalize_exit_code      # noqa: E402

RESULTS: list[dict] = []
PY = sys.executable

def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:240]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

# ------------------------------------------------------------------ 1) 注册表 ----

def case_registry() -> None:
    kinds = sorted(ADAPTERS)
    check("all_four_kinds_registered",
          kinds == ["binary", "builtin", "mcp", "python_pkg"], f"kinds={kinds}")

    # 回归保护：未知 kind 必须仍被拒（verify_mcp_outbound.py 依赖此行为）
    r = dispatch({"kind": "quantum"}, [])
    check("unknown_kind_still_rejected",
          r.exit_code == 2 and r.error == "unsupported_kind" and not r.ok,
          f"exit={r.exit_code} error={r.error}")

# ------------------------------------------------------------------ 2~8) binary ----

def case_binary() -> None:
    spec = {"kind": "binary", "command": PY,
            "args": ["-c", "import json;print(json.dumps({'ok':1}))"]}
    r = dispatch(spec, [])
    check("binary_runs_and_captures_stdout",
          r.ok and r.exit_code == 0 and (r.json or {}).get("ok") == 1,
          f"exit={r.exit_code} json={r.json}")

    # 无 shell 注入：$(...) 若被 shell 解释就会被替换成空
    probe = "$(echo pwned)"
    spec2 = {"kind": "binary", "command": PY,
             "args": ["-c", "import sys;print(sys.argv[1])"]}
    r2 = dispatch(spec2, [probe])
    check("binary_has_no_shell_injection_surface",
          r2.stdout.strip() == probe,
          f"stdout={r2.stdout.strip()!r} want={probe!r}")

    # argv 限长：固定参数 + 至多 40 个调用方参数
    spec3 = {"kind": "binary", "command": PY, "args": ["-c", "pass"]}
    r3 = dispatch(spec3, [str(i) for i in range(100)])
    argv_len = len((r3.meta or {}).get("argv") or [])
    check("binary_argv_length_capped",
          argv_len == 2 + 40, f"argv_len={argv_len} (want 2 fixed + 40)")

    # 契约外退出码 → 归一为 2，原值留存
    spec4 = {"kind": "binary", "command": PY, "args": ["-c", "import sys;sys.exit(127)"]}
    r4 = dispatch(spec4, [])
    ok4 = (r4.exit_code == 2 and not r4.ok and r4.error == "nonzero_exit"
           and (r4.meta or {}).get("raw_exit_code") == 127)
    check("binary_exit_code_normalized_with_raw_kept", ok4,
          f"exit={r4.exit_code} raw={(r4.meta or {}).get('raw_exit_code')}")

    # 契约内退出码不被改写
    spec5 = {"kind": "binary", "command": PY, "args": ["-c", "import sys;sys.exit(1)"]}
    r5 = dispatch(spec5, [])
    check("binary_in_contract_exit_preserved", r5.exit_code == 1, f"exit={r5.exit_code}")

    # 未能启动 → 环境错
    r6 = dispatch({"kind": "binary", "command": "__repolens_no_such_binary__"}, [])
    check("binary_spawn_failure_is_env_error",
          r6.exit_code == 2 and r6.error == "spawn_failed",
          f"exit={r6.exit_code} error={r6.error}")

    # 超时 → 环境错（不挂死）
    r7 = dispatch({"kind": "binary", "command": PY, "timeout_s": 0.5,
                   "args": ["-c", "import time;time.sleep(20)"]}, [])
    check("binary_timeout_is_env_error",
          r7.exit_code == 2 and r7.error == "timeout",
          f"exit={r7.exit_code} error={r7.error}")

    # 缺 command → AdapterError（dispatch 归 2）
    r8 = dispatch({"kind": "binary"}, [])
    check("binary_missing_command_rejected",
          r8.exit_code == 2 and r8.error == "adapter_error",
          f"exit={r8.exit_code} error={r8.error}")

# -------------------------------------------------------------- 9~11) python_pkg ----

def case_python_pkg() -> None:
    r = dispatch({"kind": "python_pkg"}, [])
    check("python_pkg_missing_module_rejected",
          r.exit_code == 2 and r.error == "adapter_error", f"error={r.error}")

    bad = []
    for mod in ("-c", "--version", "../evil", "a/b", "1bad", "a..b;rm", ""):
        rr = dispatch({"kind": "python_pkg", "module": mod}, [])
        bad.append((mod, rr.exit_code, rr.error))
    ok = all(e == 2 and err == "adapter_error" for _, e, err in bad)
    check("python_pkg_illegal_module_rejected", ok, f"results={bad}")

    r2 = dispatch({"kind": "python_pkg", "module": "__repolens_absent_pkg__"}, [])
    check("python_pkg_missing_package_actionable",
          r2.exit_code == 2 and r2.error == "package_missing"
          and "pip install" in (r2.hint or ""),
          f"exit={r2.exit_code} hint={r2.hint}")

    # 已安装的 stdlib 模块真实跑通（--help 避免读 stdin 而阻塞）
    r3 = dispatch({"kind": "python_pkg", "module": "json.tool"}, ["--help"])
    check("python_pkg_runs_installed_module",
          r3.ok and r3.exit_code == 0 and "usage" in (r3.stdout or "").lower(),
          f"exit={r3.exit_code} stdout_head={(r3.stdout or '')[:40]!r}")

# ------------------------------------------------------------------ 12~13) EXT-5 ----

def case_exit_codes() -> None:
    table = [(0, 0), (1, 1), (2, 2), (3, 3), (126, 2), (127, 2),
             (-9, 3), (130, 3), (9, 3), (255, 3)]
    got = [(c, normalize_exit_code(c)) for c, _ in table]
    ok = all(normalize_exit_code(c) == want for c, want in table)
    check("normalize_exit_code_table", ok, f"got={got}")

    # 端到端：借 sys.modules 注入探针模块，走真实 builtin 通道
    name = "repo_lens.scriptlib._verify_exit_probe"

    def _probe(code):
        mod = types.ModuleType(name)
        mod.main = lambda argv: code
        sys.modules[name] = mod

    seen = {}
    try:
        for code in (127, 1, 2, 0):
            _probe(code)
            rr = dispatch({"kind": "builtin", "entry": name}, [])
            seen[code] = (rr.exit_code, (rr.meta or {}).get("raw_exit_code"), rr.ok)
    finally:
        sys.modules.pop(name, None)

    ok13 = (seen[127] == (2, 127, False)          # 归一 + 原值留存
            and seen[1] == (1, None, False)       # 契约内：不改写、不留 raw
            and seen[2] == (2, None, False)
            and seen[0] == (0, None, True))
    check("builtin_exit_normalization_end_to_end", ok13, f"seen={seen}")

# ------------------------------------------------------------------ 14) 门控现状 ----

def case_gate_posture() -> None:
    from repo_lens.script_cmd import run_script_api

    out = []
    for kind, spec in (("binary", {"command": PY}),
                       ("python_pkg", {"module": "json.tool"})):
        s = {"id": "_verify_probe", "kind": kind, "status": "active", **spec}
        # 借 _index 覆盖：直接调用 run_script_api 需注册表命中，故改用等价门控判定
        from repo_lens import script_cmd as SC
        orig = SC._index
        SC._index = lambda reg, _s=s: {_s["id"]: _s}
        try:
            out.append(SC.run_script_api("_verify_probe", [], confirm=True))
        finally:
            SC._index = orig

    ok = all(r.get("error") == "not_runnable_via_api" for r in out)
    detail = [r.get("error") for r in out]
    check("new_kinds_not_runnable_via_serve_yet", ok,
          f"errors={detail}（刻意 fail-closed：kind 白名单只在 run_script_api，"
          f"未随适配器注册而放宽）")

def main() -> int:
    case_registry()
    case_binary()
    case_python_pkg()
    case_exit_codes()
    case_gate_posture()

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "adapters_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
