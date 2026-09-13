# -*- coding: utf-8 -*-
"""v1.8.0 阶段三 3-A 验收：pack 分层（写能力从只读内核剥离）。

分层目标：内核（``repo_lens/*.py`` + ``registry.core.json``）只保留通用只读分析；
git 工作流（push/pull/sync/group/batch）与业务探针（store_probe/ssh_readonly_probe）
收敛到 ``repo_lens/packs/<name>/``。本套件验证结构与门控，核心断言是
「默认配置下既有行为零变化」。

覆盖矩阵：
  1) 可用的 pack 与登记一致，且目录存在
  2) 内置 profile 的默认启用集（verorun → gitflow+verorun）
  3) settings.packs.enabled=[] → 纯只读内核；["gitflow"] → 仅该 pack
  4) 未知 pack 名被忽略（配置笔误不致命）
  5) 注册表聚合：内核 + pack == 拆分前条目全集（向后兼容硬断言）
  6) pack 条目的 entry 均落在安全白名单前缀内
  7) registry.core.json 不含任何 pack 条目（内核纯净）
  8) PACKS['gitflow'].commands 与 packs/gitflow/registry.json 声明一致（防双源分叉）
  9) 内核目录不再含已迁移模块（git_push/git_pull/multi_remote_sync/repo_group）
 10) CLI 默认 profile：--help 含 push/pull/sync/group/batch
 11) CLI pack 关闭：push → rc=2 且 stderr 含启用指引（未启用要有可操作提示）
 12) CLI pack 关闭：script list 不含 pack 探针；script run store_probe 被拒
 13) CLI pack 启用：script list 含 pack 探针；两个探针 --help 均可运行（白名单放行）
 14) 安全边界：白名单前缀为 scriptlib + packs 两者，其他一律拒绝

启动方式：python verify_packs.py
退出码 0=全 PASS；1=有 FAIL。结果另存 out/packs_verify.json。
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHONUNBUFFERED", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repo_lens import packs                                   # noqa: E402
from repo_lens.script_cmd import (_ALLOWED_PKGS, _load_core_registry,   # noqa: E402
                                  _load_registry)

RESULTS: list[dict] = []


def check(name, ok, detail=""):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": str(detail)[:240]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


def _cli(args: list[str], settings_raw: str | None = None):
    """跑一次 CLI，返回 (rc, combined_output)。settings_raw 非 None 时注入临时配置。"""
    env = dict(os.environ, PYTHONPATH=str(HERE))
    env.pop("REPO_LENS_SETTINGS", None)
    tmp = None
    if settings_raw is not None:
        fd, tmp = tempfile.mkstemp(prefix="packs_settings_", suffix=".json")
        os.close(fd)
        Path(tmp).write_text(settings_raw, encoding="utf-8")
        env["REPO_LENS_SETTINGS"] = tmp
    try:
        r = subprocess.run([sys.executable, "-m", "repo_lens"] + args,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(HERE))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _with_settings(raw: str):
    """上下文管理器：临时把 REPO_LENS_SETTINGS 指向给定内容（进程内即时生效）。

    可行是因为 settings.load_settings() 每次调用都重读搜索链，故环境变量变更
    立刻反映到 packs.enabled_packs()。
    """
    @contextlib.contextmanager
    def _cm():
        old = os.environ.get("REPO_LENS_SETTINGS")
        fd, tmp = tempfile.mkstemp(prefix="packs_s_", suffix=".json")
        os.close(fd)
        Path(tmp).write_text(raw, encoding="utf-8")
        os.environ["REPO_LENS_SETTINGS"] = tmp
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("REPO_LENS_SETTINGS", None)
            else:
                os.environ["REPO_LENS_SETTINGS"] = old
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return _cm()


#: 拆分前（v1.7.0）registry.json 的条目全集——向后兼容的硬基线。
BASELINE_SCRIPT_IDS = {
    "dangerous_api_scan", "anchor_assert", "sloc_summary", "git_churn",
    "offline_db_stub", "subprocess_harness", "script_sweep", "copy_patchset",
    "attribution", "market_calendar", "store_probe", "ssh_readonly_probe",
    "ssh_tool",
}

MOVED_MODULES = ("git_push.py", "git_pull.py", "multi_remote_sync.py", "repo_group.py")


def main() -> int:
    # ---- 1) 可用 pack 与登记一致 ----------------------------------------------
    avail = packs.available_packs()
    check("available_packs_registered_and_present",
          avail == [n for n in packs.PACKS if (HERE / "repo_lens" / "packs" / n).is_dir()],
          f"available={avail}")

    # ---- 2) 内置 profile 默认启用集 -------------------------------------------
    check("default_packs_for_verorun_profile",
          packs.enabled_packs("verorun") == ["gitflow", "verorun"],
          f"verorun={packs.enabled_packs('verorun')}")
    check("default_packs_for_generic_python_profile",
          packs.enabled_packs("generic-python") == ["gitflow"],
          f"generic-python={packs.enabled_packs('generic-python')}")

    # ---- 3) settings.packs.enabled 三种取值 -----------------------------------
    with _with_settings('{"packs": {"enabled": []}}'):
        got = packs.enabled_packs()
    check("settings_enabled_empty_yields_kernel_only", got == [], f"got={got}")

    with _with_settings('{"packs": {"enabled": ["gitflow"]}}'):
        got = packs.enabled_packs()
    check("settings_enabled_subset_respected", got == ["gitflow"], f"got={got}")

    # ---- 4) 未知 pack 名被忽略（配置笔误不致命）--------------------------------
    with _with_settings('{"packs": {"enabled": ["gitflow", "nope", "nope2"]}}'):
        got = packs.enabled_packs()
    check("settings_unknown_pack_ignored", got == ["gitflow"], f"got={got}")

    # ---- 5) 注册表聚合：向后兼容硬断言 ----------------------------------------
    reg = _load_registry()
    ids = {s["id"] for s in reg.get("scripts", []) if s.get("id")}
    check("registry_aggregate_covers_all_baseline_ids",
          ids == BASELINE_SCRIPT_IDS,
          f"missing={sorted(BASELINE_SCRIPT_IDS - ids)} extra={sorted(ids - BASELINE_SCRIPT_IDS)}")

    # ---- 6) pack 条目 entry 落在安全前缀内 ------------------------------------
    pack_entries = [s for s in reg["scripts"] if s.get("pack")]
    check("pack_entries_under_allowed_prefix",
          bool(pack_entries) and all(
              any(str(s.get("entry") or "").startswith(p) for p in _ALLOWED_PKGS)
              for s in pack_entries),
          f"n={len(pack_entries)} entries={[s.get('entry') for s in pack_entries]}")

    # ---- 7) 内核注册表不含 pack 条目 -----------------------------------------
    core_ids = {s["id"] for s in _load_core_registry().get("scripts", [])}
    check("core_registry_is_pack_free",
          not (core_ids & {"store_probe", "ssh_readonly_probe"}),
          f"core_n={len(core_ids)}")

    # ---- 8) gitflow commands 声明与 registry.json 一致 ------------------------
    gj = json.loads((HERE / "repo_lens" / "packs" / "gitflow" / "registry.json")
                    .read_text(encoding="utf-8"))
    file_cmds = [c["name"] for c in gj.get("commands", [])]
    check("gitflow_commands_single_source_of_truth",
          tuple(file_cmds) == tuple(packs.PACKS["gitflow"]["commands"]),
          f"file={file_cmds} code={list(packs.PACKS['gitflow']['commands'])}")

    # ---- 9) 内核目录不含已迁移模块 --------------------------------------------
    left = [m for m in MOVED_MODULES if (HERE / "repo_lens" / m).exists()]
    check("no_migrated_modules_left_in_kernel", not left, f"left={left}")

    # ---- 10) CLI 默认：pack 命令可用 ------------------------------------------
    rc_help, out_help = _cli(["--help"])
    cmds = packs.PACKS["gitflow"]["commands"]
    check("cli_default_registers_pack_commands",
          rc_help == 0 and all(c in out_help for c in cmds),
          f"rc={rc_help} missing={[c for c in cmds if c not in out_help]}")

    # ---- 11) CLI pack 关闭：push 给出启用指引，rc=2 ---------------------------
    off = '{"packs": {"enabled": []}}'
    rc_off, out_off = _cli(["push", "--dry-run"], off)
    check("cli_disabled_pack_command_reports_hint",
          rc_off == 2 and "pack" in out_off and "gitflow" in out_off
          and "packs" in out_off,
          f"rc={rc_off} head={out_off.strip().splitlines()[:1]}")

    # ---- 12) CLI pack 关闭：脚本条目不可见且不可运行 ---------------------------
    rc_l, out_l = _cli(["script", "list"], off)
    rc_r, out_r = _cli(["script", "run", "store_probe", "--help"], off)
    check("cli_disabled_pack_scripts_hidden",
          rc_l == 0 and "store_probe" not in out_l and "ssh_readonly_probe" not in out_l,
          f"rc={rc_l} listed={'store_probe' in out_l}")
    check("cli_disabled_pack_script_run_rejected",
          rc_r != 0 and "白名单" in out_r,
          f"rc={rc_r} out={out_r.strip()[:120]}")

    # ---- 13) CLI pack 启用：脚本条目可见且可运行 -------------------------------
    rc_l2, out_l2 = _cli(["script", "list"])
    rc_p1, out_p1 = _cli(["script", "run", "store_probe", "--help"])
    rc_p2, out_p2 = _cli(["script", "run", "ssh_readonly_probe", "--help"])
    check("cli_enabled_pack_scripts_visible",
          rc_l2 == 0 and "store_probe" in out_l2 and "ssh_readonly_probe" in out_l2,
          f"rc={rc_l2}")
    check("cli_enabled_pack_scripts_runnable",
          rc_p1 == 0 and rc_p2 == 0,
          f"store_probe rc={rc_p1} ssh_readonly_probe rc={rc_p2}")

    # ---- 14) 安全边界前缀 -----------------------------------------------------
    check("allowed_prefixes_cover_kernel_and_packs",
          _ALLOWED_PKGS == ("repo_lens.scriptlib.", "repo_lens.packs."),
          f"{_ALLOWED_PKGS}")

    from repo_lens.scriptlib.adapters.base import (ALLOWED_ENTRY_PREFIXES,
                                                   entry_allowed)
    check("allowed_prefixes_single_source_in_sync",
          _ALLOWED_PKGS == ALLOWED_ENTRY_PREFIXES,
          f"script_cmd={_ALLOWED_PKGS} adapters_base={ALLOWED_ENTRY_PREFIXES}")
    check("entry_allowed_rejects_foreign_module",
          entry_allowed("repo_lens.scriptlib.sloc_summary")
          and entry_allowed("repo_lens.packs.verorun.store_probe")
          and not entry_allowed("os.system")
          and not entry_allowed("repo_lens.evil")
          and not entry_allowed("")
          and not entry_allowed(None), "")

    fails = [r for r in RESULTS if not r["ok"]]
    print("\n" + ("—— 全部通过 ——" if not fails
                  else f"失败 {len(fails)} 项：" + ", ".join(r["case"] for r in fails)))
    try:
        (HERE / "out").mkdir(parents=True, exist_ok=True)
        (HERE / "out" / "packs_verify.json").write_text(
            json.dumps({"exit": 1 if fails else 0, "cases": RESULTS},
                       ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
