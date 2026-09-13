# -*- coding: utf-8 -*-
"""第三方 Python 包入口适配器（kind=python_pkg，审计 EXT-3）。

定位：让脚本资产库能**托管仓库外已安装的第三方包**的模块入口
（如 ``python -m bandit`` / ``python -m radon`` / ``python -m pylint``），
与 kind=builtin 形成互补——builtin 只允许 ``repo_lucent.scriptlib.`` /
``repo_lucent.packs.`` 两个前缀，**刻意不允许**指向任意第三方包。

为什么用**子进程**而不是 importlib 进程内调用：
第三方包的顶层导入可能执行任意代码、污染 ``sys.modules``、抛 ``SystemExit``
甚至挂死。派生 ``python -m <module>`` 让「可超时强杀」「崩溃不外溢」
「import 副作用不进主进程」三条同时成立，代价仅是一次进程创建开销。
故本适配器**不做进程内 import**。

与 ``kind=binary`` 的分工：需要指定**特定解释器 / 特定虚拟环境**时用 binary
（``command`` 直接写该解释器的绝对路径）；只需「当前解释器里已装好的包」时用
本适配器（自动跟随 ``sys.executable``，安装态与运行态天然一致）。

安全红线：
- ``module`` **只来自注册表**，且必须匹配 ``^[A-Za-z_][A-Za-z0-9_.]*$``：
  这既挡住 ``-c`` / ``--foo`` 这类**选项注入**（``python -m`` 会把以 ``-``
  开头的参数当选项解析），也挡住 ``./evil.py`` / ``a/b`` 这类路径注入；
- 一律 list 形式 spawn、``shell=False``；调用方 argv 限长；
- 未安装时先给可操作提示（用只解析**顶层名**的 ``find_spec`` 探测，
  探测本身不导入目标包）。

退出码（EXT-5）：缺包 / 未能启动 / 超时都归 2（环境错），
其余由 ``normalize_exit_code`` 归一，原值留存于 ``meta['raw_exit_code']``。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

from .base import (Adapter, AdapterError, NormalizedResult, maybe_json,
                   normalize_exit_code, with_raw_exit_code)

_MAX_STDOUT = 20_000
_MAX_STDERR = 4_000
_MAX_ARGV = 40
DEFAULT_TIMEOUT_S = 120.0

#: 合法模块名：点分标识符。首字符不得为数字，天然排除以 '-' 开头的选项注入。
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def top_level_of(module: str) -> str:
    """取点分模块名的顶层包名（``a.b.c`` → ``a``）。"""
    return module.split(".", 1)[0]


def is_installed(module: str) -> bool:
    """顶层包是否可被当前解释器解析。

    **只探测顶层名**：``find_spec('a.b')`` 会先**导入**父包 ``a``，那正是本适配器
    要避免的副作用；而 ``find_spec('a')`` 走 path finder，不执行目标代码。
    """
    import importlib.util

    top = top_level_of(module)
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError, AttributeError):
        return False


class PythonPkgAdapter(Adapter):
    """registry 里 kind=python_pkg 的条目走这里。"""

    kind = "python_pkg"

    def run(self, spec: dict, argv: list[str],
            timeout_s: float | None = None) -> NormalizedResult:
        module = spec.get("module")
        if not module:
            raise AdapterError("kind=python_pkg 条目需声明 module")
        module = str(module)
        if not _MODULE_RE.match(module):
            raise AdapterError(
                f"拒绝：module={module!r} 不是合法模块名"
                "（只允许字母/数字/下划线/点，且首字符不得为数字或 '-'）")

        meta = {"module": module, "python": sys.executable}
        if not is_installed(module):
            top = top_level_of(module)
            return NormalizedResult(
                ok=False, exit_code=2, error="package_missing",
                hint=f"当前解释器未安装 {top!r}：先 `pip install {top}` 再重试"
                     f"（或改用 kind=binary 指定其它解释器的绝对路径）",
                meta=meta)

        fixed = [str(a) for a in (spec.get("args") or [])]
        extra = [str(a) for a in list(argv or [])][:_MAX_ARGV]
        cmd = [sys.executable, "-m", module, *fixed, *extra]

        timeout = float(timeout_s or spec.get("timeout_s") or DEFAULT_TIMEOUT_S)
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        # 保证子进程输出按 UTF-8 解码，避免中文环境下的 GBK 乱码
        env.setdefault("PYTHONIOENCODING", "utf-8")
        meta["argv"] = [*fixed, *extra]
        meta["timeout_s"] = timeout

        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=spec.get("cwd") or None,
                               env=env, shell=False)
        except subprocess.TimeoutExpired:
            return NormalizedResult(
                ok=False, exit_code=2, error="timeout",
                hint=f"`python -m {module}` 超过 {timeout:g}s 未完成", meta=meta)
        except (OSError, ValueError) as e:
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
                        else f"`python -m {module}` 以退出码 {norm} 结束（无 stderr）")
        return res
