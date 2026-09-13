#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[已废弃] 旧入口 shim —— 本工具已更名为 RepoLens。

仅作过渡期兼容：转发到 repolens.py（正式入口），不再承载任何逻辑。
后续版本将移除本文件，请改用：

    python repolens.py ...        或    python -m repo_lens ...
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stderr.write(
    "[RepoLens] 提示：insight.py 已更名为 repolens.py（本 shim 为过渡期兼容，将被移除）。\n")

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))

from repolens import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
