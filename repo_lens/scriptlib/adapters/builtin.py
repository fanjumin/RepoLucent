# -*- coding: utf-8 -*-
"""内置脚本适配器（kind=builtin，等价于改造前的唯一路径）。

保留 entry 前缀校验（安全边界不放宽），只是把执行结果归一化为
NormalizedResult，并顺带捕获 stdout（EXT-6），供 serve/MCP/CLI 消费。

v1.8.0（OPEN-B / EXT-5）：退出码由「原始 returncode 直接透传」改为经
``normalize_exit_code`` 收敛到 0/1/2/3 契约；契约内取值恒等变换，
故既有脚本行为不变，仅契约外取值（如 127）被归入环境错。
"""
from __future__ import annotations

import contextlib
import importlib
import io

from .base import (ALLOWED_ENTRY_PREFIXES, Adapter, AdapterError,
                   NormalizedResult, entry_allowed, maybe_json,
                   normalize_exit_code, with_raw_exit_code)

_MAX_STDOUT = 20_000


class BuiltinAdapter(Adapter):
    kind = "builtin"

    def run(self, spec: dict, argv: list[str], timeout_s: float | None = None) -> NormalizedResult:
        entry = spec.get("entry")
        if not entry:
            raise AdapterError("kind=builtin 缺少 entry")
        if not entry_allowed(entry):
            # 安全边界：绝不 import 白名单包之外的模块
            raise AdapterError(
                f"拒绝：entry={entry} 不在允许包 {ALLOWED_ENTRY_PREFIXES} 下")

        try:
            mod = importlib.import_module(entry)
        except Exception as e:  # noqa: BLE001
            return NormalizedResult(ok=False, exit_code=3, error="import_failed",
                                    hint=f"{type(e).__name__}: {e}", meta={"entry": entry})
        main = getattr(mod, "main", None)
        if not callable(main):
            return NormalizedResult(ok=False, exit_code=3, error="no_entry_point",
                                    hint=f"{entry} 无 main(argv) 入口", meta={"entry": entry})

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = int(main(list(argv)) or 0)
        except SystemExit as e:  # 脚本内部 argparse 退出
            code = int(e.code) if isinstance(e.code, int) else 0
        except Exception as e:  # noqa: BLE001
            return NormalizedResult(ok=False, exit_code=3, error="script_exception",
                                    hint=f"{type(e).__name__}: {e}",
                                    stdout=buf.getvalue()[:_MAX_STDOUT], meta={"entry": entry})

        out = buf.getvalue()[:_MAX_STDOUT]
        # EXT-5：脚本若 `sys.exit(N)` 跳出 0..3 契约，归一为 2/3 语义；
        # 契约内取值恒等变换，故既有脚本的退出码不发生变化。
        norm = normalize_exit_code(code)
        return NormalizedResult(
            ok=(norm == 0), exit_code=norm, stdout=out, json=maybe_json(out),
            meta=with_raw_exit_code({"entry": entry}, code, norm))
