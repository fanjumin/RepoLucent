# -*- coding: utf-8 -*-
"""目录浏览（只读）：路径点选的服务端基础。

浏览器安全模型拿不到本机绝对路径，故由本地 serve 进程代为枚举目录。
只列目录名与 .git 存在性（is_repo 徽标），不读任何文件内容、不执行任何东西；
`.` 开头的隐藏目录默认跳过（is_repo 判定不受影响——那是对子目录内部做存在性探测）。
"""
from __future__ import annotations

import os
import string
from pathlib import Path

#: 单次返回的子目录上限（防超大目录拖垮 UI；超出置 truncated=true）
DEFAULT_LIMIT = 500


def _drives() -> list[str]:
    """Windows 盘符探测（非 Windows 返回空列表）。空光驱等 OSError 一律跳过。"""
    out: list[str] = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\")
        try:
            if p.is_dir():
                out.append(str(p))
        except OSError:  # noqa: PERF203 —— 设备不存在/未就绪
            continue
    return out


def list_dir(raw: str | None, limit: int = DEFAULT_LIMIT) -> dict:
    """浏览目录。

    raw 为空 → 返回起点视图：盘符（Windows）+ 用户目录，不列任何子目录。
    raw 为目录 → 返回 {path, parent, dirs, truncated}；
    raw 非法（不存在/不是目录）→ ValueError（由端点转 400）。
    """
    if not raw:
        home = Path.home()
        return {"path": None, "parent": None, "home": str(home),
                "roots": _drives(), "dirs": [], "truncated": False}
    p = Path(raw).expanduser()
    if not p.is_dir():
        raise ValueError(f"目录不存在或不是目录：{raw}")
    p = p.resolve()
    dirs: list[dict] = []
    truncated = False
    try:
        entries = sorted(os.scandir(p), key=lambda e: e.name.lower())
    except PermissionError:
        entries = []                       # 无权限目录：返回空列表而非报错
    except OSError as e:
        raise ValueError(f"无法读取目录：{e}") from None
    for e in entries:
        if len(dirs) >= limit:
            truncated = True
            break
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:                    # 竞态消失/探测失败：跳过
            continue
        if e.name.startswith("."):
            continue                       # 隐藏目录默认不列
        git_marker = Path(e.path, ".git")
        dirs.append({"name": e.name, "path": e.path,
                     "is_repo": git_marker.exists()})
    parent = str(p.parent) if p.parent != p else None   # 盘符根的 parent 为 None
    return {"path": str(p), "parent": parent, "home": str(Path.home()),
            "roots": _drives(), "dirs": dirs, "truncated": truncated}
