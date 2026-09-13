# -*- coding: utf-8 -*-
"""gitflow pack：Git 工作流的**写能力**（智能推送 / 拉取 / 多远程同步 / 仓库组批量）。

分层动机：写远程是审计面上最敏感的能力，把它从只读内核剥离到本目录后，
"能改动远程"的代码集合即 ``repo_lens/packs/gitflow/`` 一个目录。

启用策略：默认随 ``verorun`` 与 ``generic-python`` profile 启用；显式
``settings.packs.enabled: []`` 可完全关闭（此时 ``push/pull/sync/group/batch``
子命令不可用）。模块本身始终可导入（导入无写副作用），门控只作用于 CLI 入口。
"""
