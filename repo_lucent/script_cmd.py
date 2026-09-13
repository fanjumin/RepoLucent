# -*- coding: utf-8 -*-
"""`repolucent.py script` 子命令：脚本资产库的编目 / 调用 / 自检。

零改内核的接入点：cli.py 的 run() handlers 里加一键 "script": _cmd_script，
本模块内部按 action 二级分发。既有 29 个分析器与其余子命令完全不感知本模块。

动作：
    script list [--category CAT] [--status active] [--json]   列出脚本库
    script info <id>                                          查看某脚本完整元数据
    script run <id> [脚本自有参数 ...]                         importlib 进程内调用（透传 argv）
    script doctor [<id>]                                      对 active 脚本做 import+入口 冒烟自检

安全：只执行注册表（内核 ``registry.core.json`` + 各启用 pack 的 ``registry.json``）
中 entry 非空、且位于 ``repo_lucent.scriptlib`` / ``repo_lucent.packs`` 下的模块；
planned/archived（entry=null）会被拒绝并给出下一步提示。绝不 exec 任意路径。
pack 未启用时其脚本条目不可见，故 list/run/doctor 与 MCP 的 script.* 工具清单
会随 ``settings.packs.enabled`` 一起收敛。
退出码沿用工具契约：0 通过 / 1 脚本判问题 / 2 参数或环境错 / 3 内部异常。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

#: 内核只读资产注册表（唯一事实源）。pack 贡献的条目由 packs.registry_entries() 聚合。
_CORE_REGISTRY_PATH = Path(__file__).resolve().parent / "registry.core.json"
#: 过渡期兼容：v1.8.0 之前名为 registry.json，若仍存在则作为回落读取。
_LEGACY_REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"
#: entry 白名单前缀（安全边界）：内核脚本库 + pack 目录，二者之外一律拒绝。
#: 语义等同 scriptlib/adapters/base.py:ALLOWED_ENTRY_PREFIXES（由 verify_packs 断言
#: 二者一致以防双源分叉；此处不直接 import 是为保住本模块的零重依赖特性）。
_ALLOWED_PKGS = ("repo_lucent.scriptlib.", "repo_lucent.packs.")


def _load_core_registry() -> dict:
    """读内核注册表；registry.core.json 缺失时回落过渡期旧名。"""
    for p in (_CORE_REGISTRY_PATH, _LEGACY_REGISTRY_PATH):
        if not p.is_file():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[repolucent] 错误：注册表 JSON 解析失败：{e}", file=sys.stderr)
            return {"scripts": []}
    print(f"[repolucent] 错误：注册表缺失 {_CORE_REGISTRY_PATH}", file=sys.stderr)
    return {"scripts": []}


def _load_registry() -> dict:
    """聚合「内核 + 各启用 pack」的脚本条目（pack 条目附 ``pack`` 归属字段）。

    pack 未启用时其条目不可见——这是 pack 分层的门控点：``script list/run/doctor``
    与 MCP 的 ``script.*`` 工具清单都会随之收敛。
    """
    from . import packs
    reg = _load_core_registry()
    out = dict(reg)
    out["scripts"] = list(reg.get("scripts", []) or []) + packs.registry_entries()
    return out


def _index(reg: dict) -> dict:
    return {s["id"]: s for s in reg.get("scripts", [])}


def _print_table(scripts: list[dict]) -> None:
    if not scripts:
        print("（无匹配脚本）")
        return
    print(f"{'ID':22} {'类别':13} {'状态':9} {'版本':8} 名称")
    print("-" * 78)
    for s in sorted(scripts, key=lambda x: (x.get("category", ""), x.get("id", ""))):
        print(f"{s['id']:22} {s.get('category',''):13} {s.get('status',''):9} "
              f"{s.get('version',''):8} {s.get('name','')}")
    print(f"\n共 {len(scripts)} 条；运行：repolucent.py script run <id> [-h 查参数]；"
          f"planned/archived 尚不可运行。")


def _act_list(args) -> int:
    # 主解析器用 REMAINDER 捕获 list 之后的所有 token，故本地再解析这些可选过滤参数
    p = argparse.ArgumentParser(prog="repolucent.py script list", add_help=False)
    p.add_argument("--category", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--json", action="store_true", dest="as_json")
    try:
        opts, _unknown = p.parse_known_args(list(getattr(args, "rest", []) or []))
    except SystemExit:
        return 2
    reg = _load_registry()
    scripts = reg.get("scripts", [])
    if opts.category:
        scripts = [s for s in scripts if s.get("category") == opts.category]
    if opts.status:
        scripts = [s for s in scripts if s.get("status") == opts.status]
    if opts.as_json:
        print(json.dumps(scripts, ensure_ascii=False, indent=1))
    else:
        _print_table(scripts)
    return 0


def _act_info(args) -> int:
    reg = _load_registry()
    sid = (args.rest or [None])[0]
    s = _index(reg).get(sid)
    if not s:
        print(f"[repolucent] 错误：未找到脚本 id={sid!r}；用 `script list` 查看全部。",
              file=sys.stderr)
        return 2
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


def _act_run(args) -> int:
    rest = list(args.rest or [])
    if not rest:
        print("[repolucent] 错误：script run 需要 <id>。", file=sys.stderr)
        return 2
    sid, script_argv = rest[0], rest[1:]
    reg = _load_registry()
    s = _index(reg).get(sid)
    if not s:
        print(f"[repolucent] 错误：脚本 id={sid!r} 不在注册表白名单内。", file=sys.stderr)
        return 2
    entry = s.get("entry")
    if not entry:
        st = s.get("status")
        print(f"[repolucent] 脚本 {sid} 状态={st}，尚不可运行。"
              f"{'（planned：阶段 B/C 落地后启用）' if st=='planned' else '（archived：一次性归档，仅溯源）'}",
              file=sys.stderr)
        return 2
    if not any(entry.startswith(p) for p in _ALLOWED_PKGS):
        print(f"[repolucent] 拒绝：脚本 entry={entry} 不在允许包 "
              f"{' / '.join(_ALLOWED_PKGS)} 下（安全边界）。", file=sys.stderr)
        return 3
    try:
        mod = importlib.import_module(entry)
    except Exception as e:  # noqa: BLE001
        print(f"[repolucent] 导入脚本失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 3
    main = getattr(mod, "main", None)
    if not callable(main):
        print(f"[repolucent] 脚本 {sid} 无 main(argv) 入口。", file=sys.stderr)
        return 3
    return int(main(script_argv) or 0)


def _act_doctor(args) -> int:
    reg = _load_registry()
    only = (args.rest or [None])[0]
    bad = 0
    checked = 0
    for s in reg.get("scripts", []):
        if only and s["id"] != only:
            continue
        if not s.get("entry"):
            continue
        checked += 1
        try:
            mod = importlib.import_module(s["entry"])
            ok = callable(getattr(mod, "main", None))
            print(f"[{'PASS' if ok else 'FAIL'}] {s['id']:20} import + main() 入口")
            bad += 0 if ok else 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {s['id']:20} {type(e).__name__}: {e}")
            bad += 1
    if checked == 0:
        print("（无可自检的 active 脚本）")
    return 1 if bad else 0


# ------------------------------------------------ 共用：解析入口并执行 ----
def _resolve_entry_and_run(reg: dict, sid: str, script_argv: list[str]):
    """校验注册白名单 + entry 合法并执行。返回 (exit_code, err_message)。"""
    s = _index(reg).get(sid)
    if not s:
        return 2, f"脚本 id={sid!r} 不在注册表白名单内"
    entry = s.get("entry")
    if not entry:
        return 2, f"脚本 {sid} 状态={s.get('status')}，尚不可运行"
    if not any(entry.startswith(p) for p in _ALLOWED_PKGS):
        return 3, f"拒绝：entry={entry} 不在允许包 {_ALLOWED_PKGS} 下"
    try:
        mod = importlib.import_module(entry)
    except Exception as e:  # noqa: BLE001
        return 3, f"导入脚本失败：{type(e).__name__}: {e}"
    main = getattr(mod, "main", None)
    if not callable(main):
        return 3, f"脚本 {sid} 无 main(argv) 入口"
    try:
        return int(main(list(script_argv)) or 0), None
    except SystemExit as e:  # 脚本内部 argparse 退出
        return (int(e.code) if isinstance(e.code, int) else 0), None


# ----------------------------------------------- 供 serve 的受控运行 ----
def run_script_api(sid: str | None, argv: list[str] | None = None,
                   confirm: bool = False) -> dict:
    """Agent/浏览器经 serve 调脚本的唯一入口。三重门控：仅 active 且 kind==tool 的白名单脚本、
    必须 confirm=true 才真执行（否则返回 dry-run 预览）、argv 限长防滥用。
    注：serve 已 127.0.0.1-only，本函数不放开任意脚本/harness 执行。"""
    argv = [str(a) for a in (argv or [])][:40]
    reg = _load_registry()
    s = _index(reg).get(sid or "")
    if not s:
        return {"ok": False, "error": "unknown_id", "hint": "用 GET /api/scripts 查看白名单"}
    # ---- 三重门控第 1~2 闸：状态白名单 + kind 可跑集（不因适配器分派而放宽）----
    kind = s.get("kind") or "tool"
    if kind == "mcp":
        runnable = (s.get("status") == "active" and bool(s.get("server")) and bool(s.get("tool")))
    else:
        runnable = (kind == "tool" and s.get("status") == "active" and bool(s.get("entry")))
    if not runnable:
        return {"ok": False, "error": "not_runnable_via_api",
                "detail": f"id={sid} kind={kind} status={s.get('status')}",
                "hint": "serve 仅允许运行 active 且 kind=tool/mcp 的条目；"
                        "harness/planned/archived 请用 CLI 自检或作库导入"}
    cmd_preview = f"repolucent.py script run {sid} " + " ".join(argv)
    if not confirm:
        return {"ok": True, "dry_run": True, "command": cmd_preview,
                "hint": "传 confirm=true 才真正执行（写操作/耗时脚本需显式确认）"}
    # ---- 按 kind 选适配器执行（未注册 kind 由 dispatch 拒绝，不会落到任意执行）----
    from .scriptlib.adapters import dispatch
    res = dispatch(s, argv)
    # 兼容既有返回契约：仅在适配器报错时 ok=False；非零退出码仍 ok=True（与改造前一致）
    out = {"ok": res.error is None, "dry_run": False, "exit_code": res.exit_code,
           "command": cmd_preview, "kind": kind}
    if res.stdout:
        out["stdout"] = res.stdout
    if res.error:
        out.update({"error": res.error, "detail": res.hint})
    return out


# ------------------------------------------------------ 动作：sweep ----
def _act_sweep(args) -> int:
    from .scriptlib import script_sweep
    return script_sweep.main(list(getattr(args, "rest", []) or []))


# -------------------------------------------------- 动作：snapshot/diff ----
def _out_dir(rest: list[str]) -> "Path":
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--out", default=None)
    opts, _ = p.parse_known_args(rest)
    from pathlib import Path as _P
    return _P(opts.out).resolve() if opts.out else (_P.cwd() / "out")


def _fingerprint(reg: dict) -> dict:
    return {s["id"]: {"version": s.get("version"), "status": s.get("status"),
                      "category": s.get("category"),
                      "origin_sha": (s.get("origin") or {}).get("source_sha256")}
            for s in reg.get("scripts", [])}


def _act_snapshot(args) -> int:
    from . import snapshot as S
    rest = list(getattr(args, "rest", []) or [])
    name = rest[0] if rest and not rest[0].startswith("-") else S.default_name()
    out = _out_dir(rest)
    reg = _load_registry()
    payload = {"kind": "script_registry", "script_meta_version":
               reg.get("meta_schema", {}).get("version"), "fingerprint": _fingerprint(reg)}
    path = S.history_dir(out) / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    fp = path / (S._safe_name(name) + ".json")
    fp.write_text(S._dumps(payload), encoding="utf-8", newline="\n")
    print(f"[script] 已保存脚本库基线：{fp}（{len(payload['fingerprint'])} 条）")
    return 0


def _load_script_baseline(name: str, out) -> dict | None:
    from . import snapshot as S
    d = S.history_dir(out) / "scripts"
    for cand in (d / f"{S._safe_name(name)}.json", Path(name)):
        if cand.is_file():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _act_diff(args) -> int:
    from . import snapshot as S
    rest = list(getattr(args, "rest", []) or [])
    baseline = rest[0] if rest and not rest[0].startswith("-") else None
    if not baseline:
        d = S.history_dir(_out_dir(rest)) / "scripts"
        names = sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
        print("用法：script diff <baseline>；现有基线：" + (", ".join(names) or "（无，先 script snapshot）"))
        return 2
    out = _out_dir(rest)
    base = _load_script_baseline(baseline, out)
    if base is None:
        print(f"[script] 找不到脚本基线：{baseline}", file=sys.stderr)
        return 2
    cur = _fingerprint(_load_registry())
    b = base["fingerprint"]
    added = sorted(set(cur) - set(b))
    removed = sorted(set(b) - set(cur))
    changed = []
    for k in sorted(set(cur) & set(b)):
        if cur[k] != b[k]:
            changed.append((k, b[k], cur[k]))
    print(f"=== 脚本库变更（vs 基线 {baseline}）===")
    print(f"新增 {len(added)}：{', '.join(added) or '—'}")
    print(f"删除 {len(removed)}：{', '.join(removed) or '—'}")
    print(f"变更 {len(changed)}：")
    for k, old, new in changed:
        diffs = "; ".join(f"{f}:{old.get(f)}→{new.get(f)}" for f in
                          ("version", "status", "category") if old.get(f) != new.get(f))
        print(f"  ~ {k}: {diffs}")
    if not (added or removed or changed):
        print("（无变更）")
    return 0


# -------------------------------------------------- 动作：manual 生成 ----
def _act_manual(args) -> int:
    rest = list(getattr(args, "rest", []) or [])
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--out", default=None)
    opts, _ = p.parse_known_args(rest)
    reg = _load_registry()
    md = _render_manual(reg)
    if opts.out:
        Path(opts.out).write_text(md, encoding="utf-8")
        print(f"[script] 已生成使用手册：{opts.out}")
    else:
        print(md)
    return 0


def _render_manual(reg: dict) -> str:
    """从注册表（内核 + 启用 pack）单一事实源渲染《脚本库使用手册》。"""
    from datetime import datetime
    L: list[str] = ["# 脚本资产库 · 使用手册", ""]
    L.append("> 本手册由 `repolucent.py script manual` 从 `repo_lucent/registry.core.json` "
             "与各启用 pack 的 `repo_lucent/packs/<name>/registry.json` 自动生成，"
             f"请勿手改；更新时间 {datetime.now():%Y-%m-%d %H:%M}。")
    L.append("")
    L.append(f"脚本元 schema：`{reg.get('meta_schema', {}).get('version')}` ｜ 注册表版本 "
             f"`{reg.get('registry_version')}`。共 "
             f"{len(reg.get('scripts', []))} 条（active/planned/archived 见下）。")
    L.append("")
    L.append("## 快速上手")
    L.append("")
    L.append("```bash\npython repolucent.py script list\n"
             "python repolucent.py script info <id>\n"
             "python repolucent.py script run <id> [脚本参数...]\n"
             "python repolucent.py script doctor\n```")
    L.append("")
    order = {"active": 0, "planned": 1, "deprecated": 2, "archived": 3}
    for s in sorted(reg.get("scripts", []), key=lambda x: (order.get(x.get("status"), 9), x.get("id", ""))):
        L.append(f"## `{s['id']}` — {s.get('name','')}")
        L.append("")
        L.append(f"- 状态/类型：`{s.get('status')}` / `{s.get('kind')}` ｜ 类别 `{s.get('category')}` ｜ 版本 `{s.get('version')}`")
        L.append(f"- 功能：{s.get('summary','')}")
        if s.get("inputs"):
            L.append("- 输入：" + "、".join(
                f"`{i['name']}`({i.get('type','')}{'' if i.get('required') else ',可选'}"
                + (f",默认 {i['default']}" if i.get("default") not in (None, "", "builtin") else "")
                + ")" for i in s["inputs"]))
        if s.get("outputs"):
            L.append(f"- 输出：{s['outputs'].get('stdout','')} ｜ 退出码 {s['outputs'].get('exit','')}")
        d = s.get("deps", {})
        L.append(f"- 依赖：{'仅标准库' if d.get('stdlib_only') else ('需 ' + ', '.join(d.get('third_party', [])) if d.get('third_party') else '标准库为主')}"
                 + ("；需 git 仓库" if d.get("needs_repo") else "")
                 + ("；含网络" if d.get("network") else ""))
        if s.get("reuses_platform"):
            L.append(f"- 复用平台地基：{'、'.join(s['reuses_platform'])}")
        if s.get("overlap"):
            L.append(f"- ⚠ 能力重叠：{s['overlap']}")
        if s.get("usage"):
            L.append(f"- 用法：`{s['usage']}`")
        if s.get("notes"):
            L.append(f"- 注意：{s['notes']}")
        if s.get("kind") == "harness":
            L.append(f"- 作为库导入：`{s.get('outputs',{}).get('import','')}`")
        if s.get("origin"):
            o = s["origin"]
            L.append(f"- 来源：`{o.get('source_path')}` (sha `{(o.get('source_sha256') or '—')[:12]}`，提炼于 {o.get('extracted_at')})")
        L.append("")
    L.append("---")
    L.append("归档/计划态脚本不入库运行；`archived` 仅溯源，`planned` 待后续阶段落地。"
             "新脚本沉淀流程见设计文档《脚本沉淀规范》与 §6 版本管理。")
    return "\n".join(L) + "\n"


def _build_script_argparser() -> argparse.ArgumentParser:
    """供 script_cmd 自持的分发解析（run 的剩余参数用 REMAINDER 透传）。"""
    return argparse.ArgumentParser(prog="repolucent.py script", add_help=True)


def _cmd_script(args) -> int:
    """cli.py run() 里注册的分发入口。args 由 cli 主解析器给出：action + rest。"""
    action = getattr(args, "action", None)
    dispatch = {"list": _act_list, "info": _act_info,
                "run": _act_run, "doctor": _act_doctor,
                "sweep": _act_sweep, "snapshot": _act_snapshot,
                "diff": _act_diff, "manual": _act_manual}
    if action not in dispatch:
        print("[repolucent] 用法：script "
              "{list|info|run|doctor|sweep|snapshot|diff|manual} ...", file=sys.stderr)
        return 2
    a = dispatch[action](args)
    return a


# 供独立调试（不走 cli.py）：python -m repo_lucent.script_cmd script list
def _standalone(argv: list[str]) -> int:
    ns = argparse.Namespace()
    if not argv:
        ns.action = "list"
        ns.rest = []
    else:
        ns.action = argv[0]
        ns.rest = argv[1:]
    # list 的可选过滤在 cli 侧提供；standalone 下用最小集
    ns.category = None
    ns.status = None
    ns.json = False
    return _cmd_script(ns)
