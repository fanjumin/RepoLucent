# -*- coding: utf-8 -*-
"""VeroRun 服务器 只读 SSH 探针（通用化自上线前测试脚本）。

沉淀自"应用商店上线前真实环境测试"的服务器侧只读探针（见 origin.source_path）：
经 SSH 采集版本/git、edition 与商店相关环境变量（脱敏）、systemd 服务态、负载，
以及服务器侧对 store_catalog.json 的可达性。全部命令只读，内置破坏性 token 拒绝名单。

安全边界：
- 仅只读命令；命中 rm/del/mkfs/dd/:()/git push/systemctl (stop|restart|disable)/tee/覆盖写 等
  一律拒绝执行。可用 --cmd 追加单条自定义只读命令（同样受名单约束）。
- 凭证仅从环境变量读取：VR_SSH_USER / VR_SSH_PASS（不落 argv、不回显）。
- paramiko 惰性导入：未安装时 script doctor 仍通过，仅 run 时报环境错(退出码 2)。

退出码：0=采集成功且无异常发现；1=命令执行到但检出可疑/降级项；2=参数/环境/网络错误；3=内部异常。

用法：
    repolucent.py script run ssh_readonly_probe --host 47.103.204.180 [--repo /home/x/verorun] [--json]
    （需先 export VR_SSH_USER / VR_SSH_PASS）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_DENY = ("rm ", "rm\t", "rmdir", "del ", "mkfs", "dd ", " :()", ":(){", ">", "tee ",
         "git push", "git reset --hard", "systemctl stop", "systemctl restart",
         "systemctl disable", "service ", "shutdown", "reboot", "kill ", "pkill",
         "chmod", "chown", "mv ", "cp ", "truncate", "> /")


def _is_readonly(cmd: str) -> bool:
    low = " " + cmd.lower() + " "
    return not any(tok in low for tok in _DENY)


DEFAULT_CMDS = [
    ("version", "cat {repo}/VERSION 2>/dev/null; echo GIT=$(cd {repo} && git rev-parse --short HEAD 2>/dev/null)"),
    ("load", "uptime | tr -s ' '"),
    ("services", "systemctl list-units --type=service --state=running --no-legend 2>/dev/null | grep -iE 'verorun|nginx' | head"),
    ("store_env", "grep -aoE '^(VR_EDITION|RELEASE_EDITION|DEPLOY_TYPE|DEPLOY_ENV|STORE_CATALOG_URL|STORE_CATALOG_URLS|DOWNLOAD_MIRROR_PREFIX)=.*' {repo}/.env 2>/dev/null | sed -E 's#(://[^:/]+:)[^@]*@#\\1***@#'"),
    ("catalog_fetch", "cd {repo} && timeout 25 python3 -c \"import urllib.request,json;d=json.loads(urllib.request.urlopen('https://raw.githubusercontent.com/fanjumin/verorun-store/main/store_catalog.json',timeout=20).read());print('CATALOG_OK plugins=',len(d.get('plugins',[])))\" 2>&1 | tail -2"),
]


def _connect(host, user, pwd, retries=3):
    import paramiko  # 惰性导入：doctor 无 paramiko 不报错
    import socket
    import time as _t
    last = None
    for i in range(retries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            sock = socket.create_connection((host, 22), timeout=30)
            c.connect(host, username=user, password=pwd, sock=sock,
                      timeout=45, banner_timeout=90, auth_timeout=90,
                      allow_agent=False, look_for_keys=False)
            c.get_transport().set_keepalive(10)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            _t.sleep(3 + i * 3)
    raise last  # type: ignore[misc]


def _run(c, cmd, timeout=60):
    import time as _t
    ch = c.get_transport().open_session(timeout=30)
    ch.settimeout(5)
    ch.exec_command(cmd)
    out = b""; err = b""
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        try:
            if ch.recv_ready():
                out += ch.recv(65536); continue
            if ch.recv_stderr_ready():
                err += ch.recv_stderr(65536); continue
        except Exception:  # noqa: BLE001 - socket timeout
            pass
        if ch.exit_status_ready():
            while ch.recv_ready(): out += ch.recv(65536)
            while ch.recv_stderr_ready(): err += ch.recv_stderr(65536)
            break
        _t.sleep(0.3)
    timed_out = not ch.exit_status_ready()
    rc = ch.recv_exit_status() if ch.exit_status_ready() else None
    ch.close()
    o = out.decode("utf-8", "replace").strip(); e = err.decode("utf-8", "replace").strip()
    return o, e, rc, timed_out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ssh_readonly_probe",
                                 description="VeroRun 服务器只读 SSH 探针（破坏性命令拒绝执行）。")
    ap.add_argument("--host", required=True, help="目标主机 IP/域名")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--repo", default="/home/guxiao/verorun", help="部署仓库路径")
    ap.add_argument("--cmd", default=None, help="追加单条自定义只读命令")
    ap.add_argument("--timeout", type=int, default=60, help="单命令墙钟超时秒")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    user = os.environ.get("VR_SSH_USER"); pwd = os.environ.get("VR_SSH_PASS")
    if not (user and pwd):
        print("[ssh_readonly_probe] 缺少环境变量 VR_SSH_USER / VR_SSH_PASS", file=sys.stderr)
        return 2
    try:
        import paramiko  # noqa: F401
    except Exception:  # noqa: BLE001
        print("[ssh_readonly_probe] 未安装 paramiko（pip install paramiko）；无法 SSH", file=sys.stderr)
        return 2

    try:
        c = _connect(args.host, user, pwd)
    except Exception as e:  # noqa: BLE001
        print(f"[ssh_readonly_probe] SSH 连接失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    cmds = [(name, tmpl.format(repo=args.repo)) for name, tmpl in DEFAULT_CMDS]
    if args.cmd:
        if not _is_readonly(args.cmd):
            print("[ssh_readonly_probe] 拒绝：自定义命令命中破坏性名单，仅允许只读", file=sys.stderr)
            return 2
        cmds.append(("custom", args.cmd))

    rows = []
    for name, cmd in cmds:
        try:
            o, e, rc, to = _run(c, cmd, args.timeout)
        except Exception as ex:  # noqa: BLE001
            rows.append({"name": name, "cmd": cmd, "status": "error", "error": str(ex)[:120]})
            continue
        rows.append({"name": name, "cmd": cmd, "exit": rc, "timed_out": to,
                     "stdout": o[:400], "stderr": e[:200]})
    c.close()

    degrade = any(r.get("timed_out") or (r.get("exit") not in (0, None)) for r in rows)
    if args.as_json:
        print(json.dumps({"host": args.host, "degraded": degrade, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"=== VeroRun 服务器只读探针  host={args.host}  repo={args.repo} ===")
        for r in rows:
            print(f"\n$ [{r['name']}] {r['cmd']}")
            if r.get("error"): print("  ERROR:", r["error"])
            if r.get("stdout"): print(r["stdout"])
            if r.get("stderr"): print("  [stderr]", r["stderr"])
            if r.get("timed_out"): print(f"  [wall-clock timeout {args.timeout}s — 服务器负载高/命令慢]")
        print("\n结论：" + ("存在超时/非零退出，疑似负载或降级(退出码 1)" if degrade else "采集正常"))
    return 1 if degrade else 0


if __name__ == "__main__":
    sys.exit(main())
