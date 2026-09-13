# -*- coding: utf-8 -*-
"""通用 SSH 工具：对任意主机执行命令（默认只读约束，可显式解锁写操作）。

与 ssh_readonly_probe（VeroRun 部署探针，固定采集项）互补：本脚本不绑定业务，
由调用方给出命令清单，适合作为 Agent/运维的通用远程执行面。

安全模型（与 scriptlib 门控一致）：
- 凭证只来自环境变量：REPO_LUCENT_SSH_USER / REPO_LUCENT_SSH_PASS
  （兼容旧名 VR_SSH_USER / VR_SSH_PASS）；支持 --key 指定私钥文件（口令同样走 REPO_LUCENT_SSH_PASS）。
- 默认只读：内置破坏性 token 拒绝名单（rm/mkfs/dd/>/>>/systemctl stop|restart/
  shutdown/reboot/kill/chmod/chown/mv/git push 等）；命令命中即拒，逐条返回拒绝原因。
- --yes-i-know 显式解锁写操作（MCP/UI 通道另有 confirm 三重门控，此处是 CLI 侧最后防线）。
- paramiko 惰性导入：未安装时 doctor 可通过，run 返回 2。

用法：
    python -m repo_lucent.scriptlib.ssh_tool --host 10.0.0.2 --user root \
        --cmd "uname -a" --cmd "df -h /" --json
    python -m repo_lucent.scriptlib.ssh_tool --host h --cmd "systemctl restart nginx" --yes-i-know

输出 JSON：{host, port, user, rows: [{cmd, exit_code, stdout, stderr, timed_out, duration_ms}],
            rejected: [{cmd, reason}], degraded}
退出码：0 全部成功 / 1 有命令失败或被拒（降级） / 2 环境错（缺 paramiko/凭证/连接失败） / 3 内部异常
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

#: 破坏性 token 拒绝名单（词边界匹配；--yes-i-know 可显式越过）
_DENY_PATTERNS = (
    r"\brm\s+(-[a-zA-Z]*\s+)*", r"\bmkfs", r"\bdd\s+if=", r">\s*/dev/",
    r">>", r"\bsystemctl\s+(stop|restart|disable|mask)\b",
    r"\bservice\s+\S+\s+(stop|restart)\b", r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\bkill(all)?\b", r"\bchmod\b", r"\bchown\b", r"\bmv\b", r"\brmdir\b",
    r"\bgit\s+push\b", r"\bdocker\s+(rm|rmi|system\s+prune)\b",
    r"\biptables\b", r"\bufw\b", "\bcrontab\\b", r"\buserdel\b", r"\bgroupdel\b",
    r"\btruncate\b", r"\btee\s+/",
)


def check_readonly(cmd: str) -> str | None:
    """返回拒绝原因；None=通过。"""
    for pat in _DENY_PATTERNS:
        if re.search(pat, cmd):
            return f"命中破坏性名单：/{pat}/"
    return None


def _creds():
    user = os.environ.get("REPO_LUCENT_SSH_USER") or os.environ.get("VR_SSH_USER")
    pwd = os.environ.get("REPO_LUCENT_SSH_PASS") or os.environ.get("VR_SSH_PASS")
    return user, pwd


def _connect(host: str, port: int, user: str, pwd: str | None, key_path: str | None,
             retries: int = 3):
    import paramiko
    import socket
    last = None
    for i in range(retries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            sock = socket.create_connection((host, port), timeout=30)
            kw = {"pkey": None}
            if key_path:
                kw["key_filename"] = key_path
                kw["passphrase"] = pwd
            c.connect(host, port=port, username=user, password=pwd, sock=sock,
                      timeout=45, banner_timeout=90, auth_timeout=90,
                      allow_agent=False, look_for_keys=not key_path and not pwd,
                      **kw)
            c.get_transport().set_keepalive(10)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + i * 2)
    raise last  # type: ignore[misc]


def _run(c, cmd: str, timeout: int = 60):
    t0 = time.time()
    ch = c.get_transport().open_session(timeout=30)
    ch.settimeout(5)
    ch.exec_command(cmd)
    out = b""
    err = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if ch.recv_ready():
                out += ch.recv(65536)
                continue
            if ch.recv_stderr_ready():
                err += ch.recv_stderr(65536)
                continue
        except Exception:  # noqa: BLE001 —— 通道超时即轮询退出状态
            pass
        if ch.exit_status_ready():
            while ch.recv_ready():
                out += ch.recv(65536)
            while ch.recv_stderr_ready():
                err += ch.recv_stderr(65536)
            break
        time.sleep(0.3)
    timed_out = not ch.exit_status_ready()
    rc = ch.recv_exit_status() if ch.exit_status_ready() else None
    ch.close()
    return (out.decode("utf-8", "replace").strip(),
            err.decode("utf-8", "replace").strip(),
            rc, timed_out, round((time.time() - t0) * 1000))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ssh_tool",
        description="通用 SSH 工具（默认只读约束；--yes-i-know 显式解锁写操作）。")
    ap.add_argument("--host", required=True, help="目标主机 IP/域名")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default=None, help="SSH 用户（缺省取环境变量）")
    ap.add_argument("--cmd", action="append", default=[], dest="cmds",
                    help="要执行的命令（可重复）；受只读名单约束")
    ap.add_argument("--key", default=None, help="私钥文件路径（缺省密码认证）")
    ap.add_argument("--timeout", type=int, default=60, help="单命令墙钟超时秒")
    ap.add_argument("--yes-i-know", action="store_true",
                    help="解锁破坏性名单（写操作由调用方自行负责）")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    env_user, env_pwd = _creds()
    user = args.user or env_user
    pwd = env_pwd
    if not user:
        print("[ssh_tool] 缺少用户：传 --user 或设置 REPO_LUCENT_SSH_USER", file=sys.stderr)
        return 2
    if not args.key and not pwd:
        print("[ssh_tool] 缺少凭证：设置 REPO_LUCENT_SSH_PASS（或旧名 VR_SSH_PASS），"
              "或传 --key 用私钥", file=sys.stderr)
        return 2
    try:
        import paramiko  # noqa: F401
    except Exception:  # noqa: BLE001
        print("[ssh_tool] 未安装 paramiko（pip install paramiko）；无法 SSH", file=sys.stderr)
        return 2
    if not args.cmds:
        print("[ssh_tool] 未给出命令：用 --cmd 指定（可重复）", file=sys.stderr)
        return 2

    rows, rejected = [], []
    for cmd in args.cmds:
        if not args.yes_i_know:
            reason = check_readonly(cmd)
            if reason:
                rejected.append({"cmd": cmd, "reason": reason})
                continue
    live_cmds = [c for c in args.cmds
                 if c not in [r["cmd"] for r in rejected]]
    if not live_cmds and rejected:
        if args.as_json:
            print(json.dumps({"host": args.host, "port": args.port, "user": user,
                              "rows": [], "rejected": rejected, "degraded": True},
                             ensure_ascii=False, indent=2))
        else:
            for r in rejected:
                print(f"$ {r['cmd']}\n  [拒绝] {r['reason']}")
        return 1

    try:
        c = _connect(args.host, args.port, user, pwd, args.key)
    except Exception as e:  # noqa: BLE001
        print(f"[ssh_tool] SSH 连接失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    try:
        for cmd in live_cmds:
            out, err, rc, timed_out, ms = _run(c, cmd, args.timeout)
            rows.append({"cmd": cmd, "exit_code": rc, "stdout": out, "stderr": err,
                         "timed_out": timed_out, "duration_ms": ms})
    except Exception as e:  # noqa: BLE001
        print(f"[ssh_tool] 执行异常：{type(e).__name__}: {e}", file=sys.stderr)
        return 3
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass

    bad = any(r["exit_code"] not in (0,) or r["timed_out"] for r in rows) or bool(rejected)
    result = {"host": args.host, "port": args.port, "user": user,
              "rows": rows, "rejected": rejected, "degraded": bad}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"$ {r['cmd']}")
            if r["stdout"]:
                print(r["stdout"])
            if r["stderr"]:
                print(f"[stderr] {r['stderr']}")
            print(f"  [exit={r['exit_code']}"
                  + (" 超时" if r["timed_out"] else "") + f" {r['duration_ms']}ms]")
        for r in rejected:
            print(f"$ {r['cmd']}\n  [拒绝] {r['reason']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
