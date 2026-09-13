# -*- coding: utf-8 -*-
"""gitflow pack 的 CLI 子命令贡献（push / pull / sync / group / batch）。

契约（由 ``repo_lens/packs/__init__.py:register_cli`` 调用）::

    build(sub, ctx) -> dict[str, handler]
        sub: argparse 的 subparsers 对象（用 add_parser 挂子命令）
        ctx: {"cli": <repo_lens.cli 模块>}，供 handler 复用内核函数与公共参数

未启用本 pack 时 ``build`` 不会被调用，上述命令也就不会出现在 argparse 里
——这是 pack 分层的 CLI 门控点（``cli.run()`` 对缺失的 pack 命令给出启用提示）。

参数定义与 v1.7.0 完全一致（含 ``--dry-run`` 默认开启、真实执行需 ``--confirm``
的既有安全语义），仅位置从内核搬入本 pack。
"""
from __future__ import annotations


def build(sub, ctx: dict) -> dict:
    cli = ctx["cli"]
    _add_shared_args = cli._add_shared_args

    gp = sub.add_parser("group", help="管理仓库组配置")
    _add_shared_args(gp)
    gp_sub = gp.add_subparsers(dest="group_cmd", metavar="SUBCOMMAND")

    ga = gp_sub.add_parser("add", help="向组添加仓库")
    ga.add_argument("group_name", help="组名")
    ga.add_argument("--repo", required=True, help="仓库路径")
    ga.add_argument("--remote-github", metavar="URL", help="GitHub remote URL")
    ga.add_argument("--remote-gitee", metavar="URL", help="Gitee remote URL")

    gl = gp_sub.add_parser("list", help="列出仓库组")
    gl.add_argument("--name", metavar="GROUP", help="只显示指定组")

    gr = gp_sub.add_parser("remove", help="从组移除仓库")
    gr.add_argument("group_name", help="组名")
    gr.add_argument("--repo", required=True, help="仓库路径")

    bp = sub.add_parser("batch", help="批量执行命令于仓库组")
    _add_shared_args(bp)
    bp.add_argument("command", choices=("log", "remote-diff", "untracked"),
                    help="要执行的命令")
    bp.add_argument("--group", required=True, help="仓库组名")
    bp.add_argument("--since", type=int, default=90, help="log: 扫描天数")
    bp.add_argument("--remote", default="origin", help="remote-diff: remote 名称")
    bp.add_argument("--branch", default=None, help="remote-diff: 分支名")
    bp.add_argument("--format", choices=("json", "md", "table"), default="table",
                    help="输出格式")

    # ---- Git 工作流扩展子命令（阶段 H）----
    pp = sub.add_parser("push", help="智能推送到远程仓库（含预检和 CI 检查）")
    _add_shared_args(pp)
    pp.add_argument("--remote", default="origin", help="remote 名称（默认 origin）")
    pp.add_argument("--branch", default=None, help="分支名（默认当前分支）")
    pp.add_argument("--dry-run", action="store_true", default=True,
                    help="只模拟执行，不真实推送（默认开启）")
    pp.add_argument("--no-dry-run", action="store_false", dest="dry_run",
                    help="关闭 dry-run 模式（仍需 --confirm）")
    pp.add_argument("--check-ci", action="store_true",
                    help="检查 CI 状态后再推送")
    pp.add_argument("--confirm", action="store_true",
                    help="确认执行真实推送")

    plp = sub.add_parser("pull", help="智能拉取远程更新（含冲突预警）")
    _add_shared_args(plp)
    plp.add_argument("--remote", default="origin", help="remote 名称（默认 origin）")
    plp.add_argument("--branch", default=None, help="分支名（默认当前分支）")
    plp.add_argument("--dry-run", action="store_true", default=True,
                    help="只模拟执行，不真实拉取（默认开启）")
    plp.add_argument("--no-dry-run", action="store_false", dest="dry_run",
                    help="关闭 dry-run 模式（仍需 --confirm）")
    plp.add_argument("--rebase", action="store_true",
                    help="使用 rebase 模式拉取")
    plp.add_argument("--confirm", action="store_true",
                    help="确认执行真实拉取")

    sp = sub.add_parser("sync", help="多仓库同步（GitHub ↔ Gitee）")
    _add_shared_args(sp)
    sp.add_argument("--group", required=True, help="仓库组名")
    sp.add_argument("--direction",
                    choices=("github-to-gitee", "gitee-to-github", "bidirectional"),
                    default="bidirectional", help="同步方向（默认双向）")
    sp.add_argument("--dry-run", action="store_true", default=True,
                    help="只模拟执行（默认开启）")
    sp.add_argument("--no-dry-run", action="store_false", dest="dry_run",
                    help="关闭 dry-run 模式（仍需 --confirm）")
    sp.add_argument("--confirm", action="store_true",
                    help="确认执行真实同步")

    return {"group": cli._cmd_group, "batch": cli._cmd_batch,
            "push": cli._cmd_push, "pull": cli._cmd_pull, "sync": cli._cmd_sync}
