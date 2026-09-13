# -*- coding: utf-8 -*-
"""外部二进制适配器（kind=binary，审计 EXT-2）。

定位：让脚本资产库能**托管仓库外的可执行程序**（shell 脚本 / exe / bat / 系统命令），
与 kind=builtin（进程内 importlib 调用内核与 pack 模块）互补。典型用途：
把「仓库自带的维护脚本」「系统工具」「已装好的命令行程序」纳入同一资产目录，
使其在 `script list` / `script doctor` / MCP 工具清单里与内置脚本同等可见。

安全红线（与 outbound mcp 适配器同一取舍——**调用方只能选条目，不能选命令**）：
- 命令与固定参数**只来自注册表** ``spec['command']`` / ``spec['args']``，
  绝不接受调用方传入 command；
- 一律 list 形式 spawn、``shell=False`` 硬编码 —— 参数里的空格 / 引号 / 管道
  因此没有注入面；
- 调用方附加 argv 限长（与 ``run_script_api`` 的 40 token 上限一致）；
- 超时强制生效（``spec['timeout_s']``，默认 60s），避免外部程序挂死拖垮调用方；
- stdout / stderr 均截断留档；stderr 进 ``meta`` 而**不混入 stdout**，
  以免污染 stdout 的 JSON 解析。

退出码（EXT-5）：未能启动（不存在 / 不可执行 / cwd 非法）与超时都归 2（环境错），
其余由 ``normalize_exit_code`` 按 0/1/2/3 归一，原值留存于 ``meta['raw_exit_code']``。
"""
from __future__ import annotations

import os
import subprocess

from .base import (Adapter, AdapterError, NormalizedResult, maybe_json,
                   normalize_exit_code, with_raw_exit_code)

_MAX_STDOUT = 20_000
_MAX_STDERR = 4_000

#: 调用方附加参数上限（与 script_cmd.run_script_api 的 argv 截断一致）
_MAX_ARGV = 40

#: 默认超时（秒）。可由注册表条目的 timeout_s 覆盖。
DEFAULT_TIMEOUT_S = 60.0


class BinaryAdapter(Adapter):
    """registry 里 kind=binary 的条目走这里。"""

    kind = "binary"

    def run(self, spec: dict, argv: list[str],
            timeout_s: float | None = None) -> NormalizedResult:
        command = spec.get("command")
        if not command:
            raise AdapterError("kind=binary 条目需声明 command")

        fixed = [str(a) for a in (spec.get("args") or [])]
        extra = [str(a) for a in list(argv or [])][:_MAX_ARGV]
        cmd = [str(command), *fixed, *extra]

        timeout = float(timeout_s or spec.get("timeout_s") or DEFAULT_TIMEOUT_S)
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        env.setdefault("PYTHONIOENCODING", "utf-8")
        meta = {"command": str(command), "argv": [*fixed, *extra], "timeout_s": timeout}

        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=spec.get("cwd") or None,
                               env=env, shell=False)
        except subprocess.TimeoutExpired:
            return NormalizedResult(ok=False, exit_code=2, error="timeout",
                                    hint=f"命令超过 {timeout:g}s 未完成：{command}",
                                    meta=meta)
        except (OSError, ValueError) as e:
            # 文件不存在 / 无执行权限 / cwd 非法 —— 环境错，非工具判问题
            return NormalizedResult(ok=False, exit_code=2, error="spawn_failed",
                                    hint=f"{type(e).__name__}: {e}", meta=meta)

        out = (r.stdout or "")[:_MAX_STDOUT]
        err = (r.stderr or "")[:_MAX_STDERR]
        raw_code = int(r.returncode)
        norm = normalize_exit_code(raw_code)
        meta = with_raw_exit_code(meta, raw_code, norm)
        if err:
            meta["stderr"] = err

        res = NormalizedResult(ok=(norm == 0), exit_code=norm, stdout=out,
                               json=maybe_json(out), meta=meta)
        if norm != 0:
            res.error = "nonzero_exit"
            tail = err.strip().splitlines()
            res.hint = (tail[-1][:200] if tail
                        else f"命令以退出码 {norm} 结束（无 stderr）")
        return res
