# -*- coding: utf-8 -*-
"""部署 verorun-dev-insight 到仓库 tools/dev_insight + 配置 git 忽略 + README 标注。

仅执行写入/复制，不改动任何已有业务代码；只在 README 末尾追加"本地工具"说明段。
"""
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
DEST = Path(r"D:\projects\verorun-code\tools\dev_insight")

ITEMS = ["insight.py", "vr_insight", "开发者必读.md", "开发者必读.html"]

def ignore(d, names):
    return {n for n in names if n.startswith("__pycache__") or n in ("out", "tests", "fixture_out")}

print("== 复制到仓库 ==")
DEST.mkdir(parents=True, exist_ok=True)
for item in ITEMS:
    s = SRC / item
    d = DEST / item
    if s.is_dir():
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=ignore)
    else:
        shutil.copy2(s, d)
    print("  +", item)

# 列出目标
print("== 部署后目录 ==")
for p in sorted(DEST.rglob("*")):
    if "__pycache__" in str(p) or ".pyc" in str(p):
        continue
    print("   ", p.relative_to(DEST))

# ---- .git/info/exclude ----
exclude = Path(r"D:\projects\verorun-code\.git\info\exclude")
add_lines = ["tools/dev_insight/"]
text = exclude.read_text(encoding="utf-8", errors="replace")
missing = [ln for ln in add_lines if ln not in text]
if missing:
    with exclude.open("a", encoding="utf-8") as f:
        f.write("\n# VeroRun 本地开发辅助工具（不进入版本控制）\n")
        for ln in missing:
            f.write(ln + "\n")
    print("== .git/info/exclude 已追加 ==")
else:
    print("== .git/info/exclude 已包含，跳过 ==")

# ---- README 标注（幂等） ----
def annotate(readme: Path, heading: str, body: str):
    if readme.exists():
        t = readme.read_text(encoding="utf-8", errors="replace")
        if "dev_insight" in t:
            print(f"== {readme.name} 已标注，跳过 ==")
            return
        with readme.open("a", encoding="utf-8") as f:
            f.write("\n\n---\n\n" + heading + "\n\n" + body + "\n")
        print(f"== {readme.name} 已追加标注 ==")

en_body = (
    "### Local Development Tool (not versioned)\n\n"
    "- `tools/dev_insight/` is a **local-only** developer tool that analyzes the VeroRun "
    "architecture (system core + business plugins) and emits JSON / Markdown / HTML reports "
    "plus a token-friendly `AI_CONTEXT.md` for AI assistants.\n"
    "- It is **excluded from Git** via `.git/info/exclude`; do **not** commit it or its `out/` output.\n"
    "- Usage: `python tools/dev_insight/insight.py`"
)
zh_body = (
    "### 本地开发工具（不进入版本控制）\n\n"
    "- `tools/dev_insight/` 是一个**仅本地**的开发辅助工具，用于分析 VeroRun 架构（系统核心 + 业务插件），"
    "产出 JSON / Markdown / HTML 报告，以及面向 AI 助手的精简版 `AI_CONTEXT.md`。\n"
    "- 已通过 `.git/info/exclude` **从 Git 排除**；请**不要**提交它或其 `out/` 产物。\n"
    "- 使用：`python tools/dev_insight/insight.py`"
)
annotate(Path(r"D:\projects\verorun-code\README.md"), "## Local Development Tools", en_body)
annotate(Path(r"D:\projects\verorun-code\README.zh-CN.md"), "## 本地开发工具", zh_body)
print("DONE")
