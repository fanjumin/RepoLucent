# -*- coding: utf-8 -*-
"""档位 A 通用化（profile 配置化）· 验收脚本。

验收门（通用化-档位A-配置化实施方案 §5）：7 用例 + 零行为变化。
  1) 注入 verorun 预设      → 分析口径与 VeroRun 基线逐项一致
  2) 仓库签名可换          → 非 VeroRun 仓库（本工具自身）可被接受
  3) 核心知识库可换        → profile.core_modules 增量合并后 analyze_core 随之变化
  4) 签名约束生效          → files=["pyproject.toml"] 时仅含该文件的目录被接受
  5) 组件短路              → component.dir="" 时 plugins.items == [] 且不抛异常
  6) 门禁集可换            → default_gates=["file-too-large"] 时 expand() 只展开 1 项
  7) 前端仓库名可换        → frontend_repo_names=[] 时 discover_frontend_repos() 返回 []

去 VeroRun 硬编码改造（阶段 7）适配说明 —— 三条语义变更，逐条对应本文件的改法：
  A. 「基线口径」不再来自代码常量，而在 `profiles/verorun.json`。故用例 1/3/7
     注入的是**该预设原文**（否则等于用测试自编的口径去验自己）。
  B. 强制显式声明：未声明口径 / 名不存在 → 报错退出（退出码 2），不存在隐式回落。
     新增用例 8/9 分别验证 settings.json 的 `profile.name` 通道与"名不存在即报错"。
  C. `settings.json` 的 `profile` 段语义收紧为**预设名**（`{"profile": {"name":
     "<预设>"}}`），不再支持内联口径 dict。程序化注入改走 `set_profile_override(dict)`
     —— 与 `--repo-name` 按仓覆盖同一条通道。

输出纯 ASCII JSON 到 out/profile_verify.json（规避 PowerShell UTF-16 落盘问题）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent import config
from repo_lucent.cli import _setup
from repo_lucent.gate import default_gates, expand
from repo_lucent.frontend_analyzer import discover_frontend_repos
from repo_lucent.plugin_analyzer import analyze_plugins
from repo_lucent.core_analyzer import analyze_core
from repo_lucent.settings import set_profile_override


def _load_preset(name: str) -> dict:
    """读取随包 profile 预设。

    去硬编码后，「基线口径」不再存在于代码常量里，而在 `profiles/<name>.json`。
    因此本脚本注入的必须是**真实预设内容**——否则等于用测试自己编的口径去验自己。
    """
    p = HERE / "profiles" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8"))["profile"]


#: VeroRun 口径基线（= 随包预设原文，改造前等价于"代码内置默认"）
VERORUN = _load_preset("verorun")

SETTINGS_FILE = HERE / "out" / "_verify_profile_settings.json"

RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")

def use_profile(profile: dict | None) -> None:
    """注入当前生效口径（dict 通道；``None`` = 清除）。

    与 `--repo-name` 的按仓覆盖走同一条 `set_profile_override` 通道，故语义一致：
    该 dict 是**权威口径**，优先于环境变量与 settings.json。
    """
    set_profile_override(profile or None)

def write_settings_name(name: str) -> Path:
    """写一个只声明「预设名」的 settings 文件，并指向它（新契约的正式声明方式）。"""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"profile": {"name": name}}),
                             encoding="utf-8")
    os.environ["REPO_LUCENT_SETTINGS"] = str(SETTINGS_FILE)
    return SETTINGS_FILE

def make_verorun_fixture(root: Path) -> Path:
    """最小 VeroRun 形态仓库：plugins/ + plugin_manager/ + 一个自定义核心目录。"""
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    pm = root / "plugin_manager"
    pm.mkdir(parents=True, exist_ok=True)
    (pm / "__init__.py").write_text("", encoding="utf-8")
    core = root / "my_core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    return root

def fake_args(repo: Path):
    return SimpleNamespace(repo=str(repo), tree_depth=None,
                           extra_repos=None, out=None, module=None, plugin=None)

def make_cfg(repo: Path):
    """直接走 _setup（保证 apply_profile 先于 autodetect_repo，与真实入口同构）。"""
    return _setup(fake_args(repo))

# 准备临时区域
work = HERE / "out" / "_verify_profile_area"
if work.exists():
    shutil.rmtree(work)
work.mkdir(parents=True, exist_ok=True)
repo = make_verorun_fixture(work / "fake_verorun")
os.environ.pop("REPO_LUCENT_SETTINGS", None)
use_profile(None)

# ================================================================ 用例 1 ====
# 注入 verorun 预设：口径 = 随包 VeroRun 基线
# （改造前此处是「不写 settings 即回落内置 VeroRun」；强制显式声明后，
#   基线必须由 profile 提供，故改为注入真实预设原文。）
use_profile(VERORUN)
cfg = make_cfg(repo)
pr = analyze_plugins(cfg, {})
core_res = analyze_core(cfg, {})
ok1 = (config.PLUGINS_DIR == "plugins"
       and config.MANIFEST_NAME == "plugin.json"
       and config.repo_signature() == {"dirs": ["plugins", "plugin_manager"], "files": []}
       and "plugin_manager" in config.KNOWN_CORE_MODULES
       and pr["count"] == 0 and isinstance(pr["items"], list)
       and any(m["name"] == "my_core" for m in core_res["modules"]))
record("verorun_preset_matches_baseline", ok1,
       f"PLUGINS_DIR={config.PLUGINS_DIR} MANIFEST={config.MANIFEST_NAME} "
       f"sig={config.repo_signature()} plugins.count={pr['count']} "
       f"my_core_in_core={any(m['name'] == 'my_core' for m in core_res['modules'])}")

# ================================================================ 用例 2 ====
# 仓库签名可换：--repo 指向本工具自身（无 plugins/+plugin_manager/）不再 SystemExit
use_profile({
    "name": "tool-self",
    "repo_signature": {"dirs": [], "files": ["repolucent.py"]},
})
tool_self_cfg = make_cfg(HERE)          # 工具根含 repolucent.py，无 plugins/ 目录
ok2 = tool_self_cfg.repo_root == HERE.resolve()
record("repo_signature_accepts_non_verorun", ok2,
       f"repo_root={tool_self_cfg.repo_root} (期望 {HERE.resolve()})")

# ================================================================ 用例 3 ====
# 核心知识库可换（增量合并）：在 verorun 预设之上注入 my_core 描述 + 新目录，
# 预设既有键（plugin_manager 等）必须仍在。
use_profile({
    **VERORUN,
    "name": "cm-test",
    "core_modules": {**VERORUN.get("core_modules", {}),
                     "my_core": "自定义核心：注入测试",
                     "brand_new_dir": "全新模块"},
})
cfg3 = make_cfg(repo)
cm = analyze_core(cfg3, {})
m = next((x for x in cm["modules"] if x["name"] == "my_core"), None)
ok3 = (m is not None and m["description"] == "自定义核心：注入测试"
       and "plugin_manager" in config.KNOWN_CORE_MODULES)
record("core_modules_injection_merge", ok3,
       f"my_core.description={m['description'] if m else None!r} "
       f"预设 plugin_manager 保留={'plugin_manager' in config.KNOWN_CORE_MODULES}")

# ================================================================ 用例 4 ====
# 签名约束生效：files=["pyproject.toml"] 时，仅含该文件的目录被显式接受
sig_dir = work / "sig_target"
sig_dir.mkdir(parents=True, exist_ok=True)
(sig_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
use_profile({
    "name": "sig-test",
    "repo_signature": {"dirs": [], "files": ["pyproject.toml"]},
})
cfg4 = make_cfg(sig_dir)
ok4a = cfg4.repo_root == sig_dir.resolve()
# 不含 pyproject.toml 的目录：要么 SystemExit，要么返回的 repo_root 必须匹配签名
nonsig_dir = work / "nonsig_target"
nonsig_dir.mkdir(parents=True, exist_ok=True)
try:
    cfg4b = make_cfg(nonsig_dir)
    ok4b = (cfg4b.repo_root / "pyproject.toml").is_file()   # 绝不接受不匹配目录本身
except SystemExit:
    ok4b = True
record("repo_signature_constraint", ok4a and ok4b,
       f"匹配目录被接受={ok4a}；不匹配目录被拒/不误配={ok4b}")

# ================================================================ 用例 5 ====
# 组件短路：component.dir="" → analyze_plugins 返回空结构且不抛异常
use_profile({
    "name": "no-component",
    # repo_signature 已成为**必填**（改造前缺省回落内置 verorun 签名）：显式写
    # {"dirs": [], "files": []} 才是"接受任意目录"的合法表达。
    "repo_signature": VERORUN["repo_signature"],
    "component": {"dir": "", "manifest": "", "required_fields": [], "id_pattern": ""},
})
cfg5 = make_cfg(repo)
try:
    pr5 = analyze_plugins(cfg5, {})
    ok5 = pr5["count"] == 0 and pr5["items"] == []
except Exception as e:                                       # noqa: BLE001
    ok5 = False
    pr5 = {"error": repr(e)}
record("component_short_circuit", ok5,
       f"plugins.count={pr5.get('count')} items_empty={pr5.get('items') == []}")

# ================================================================ 用例 6 ====
# 门禁集可换：default_gates=["file-too-large"] → expand() 只展开 1 项
use_profile({"name": "gates-test", "repo_signature": VERORUN["repo_signature"],
             "default_gates": ["file-too-large"]})
make_cfg(repo)                                              # 触发 apply_profile
gates = default_gates()
chosen = expand(gates)
ok6a = gates == ["file-too-large"] and chosen == {"file-too-large"}
# 还原：null（无 default_gates 键）→ 维持历史行为（[]，不自动启用门禁）
use_profile({"name": "gates-off", "repo_signature": VERORUN["repo_signature"],
             "default_gates": None})
make_cfg(repo)
ok6b = default_gates() == []
record("default_gates_configurable", ok6a and ok6b,
       f"注入后 default_gates={gates} expand={sorted(chosen)}；"
       f"null 回落 default_gates={default_gates()} (期望 [])")

# ================================================================ 用例 7 ====
# 前端仓库名可换：verorun 预设可发现同级 verorun-workplace；覆盖为 [] 后返回 []
fe = work / "verorun-workplace"
fe.mkdir(parents=True, exist_ok=True)
(fe / "package.json").write_text("{}", encoding="utf-8")
use_profile(VERORUN)                                        # 预设原文（含前后端仓库名）
cfg7a = make_cfg(repo)
found_default = discover_frontend_repos(cfg7a)
ok7a = fe.resolve() in [p.resolve() for p in found_default]
use_profile({**VERORUN, "name": "fe-off", "frontend_repo_names": []})
cfg7b = make_cfg(repo)
found_empty = discover_frontend_repos(cfg7b)
ok7b = found_empty == []
record("frontend_repo_names_configurable", ok7a and ok7b,
       f"默认发现 {len(found_default)} 个（含 verorun-workplace={ok7a}）；"
       f"注入 [] 后发现 {len(found_empty)} 个")

# ================================================================ 用例 8 ====
# settings.json 的 `profile.name` 通道：只给名，内容取自 profiles/verorun.json
use_profile(None)
write_settings_name("verorun")
cfg8 = make_cfg(repo)
ok8 = (config.PLUGINS_DIR == "plugins" and config.MANIFEST_NAME == "plugin.json"
       and "plugin_manager" in config.KNOWN_CORE_MODULES)
record("settings_profile_name_channel", ok8,
       f"PLUGINS_DIR={config.PLUGINS_DIR} MANIFEST={config.MANIFEST_NAME} "
       f"core_modules={len(config.KNOWN_CORE_MODULES)}")

# ================================================================ 用例 9 ====
# 名不存在 → 报错退出（不再静默回落内置口径）
use_profile(None)
write_settings_name("__definitely_no_such_preset__")
try:
    make_cfg(repo)
    ok9, detail9 = False, "竟然没报错"
except SystemExit as e:
    ok9 = "未找到 profile 预设" in str(e)
    detail9 = str(e).splitlines()[0]
record("unknown_profile_name_exits", ok9, detail9)

# ---------------------------------------------------------------- 汇总 ----
fails = [r for r in RESULTS if not r["ok"]]
# 复位为「无口径」（防同进程后续使用污染）：强制显式声明下，无口径即报错，
# 不再存在可回落的"内置基线"。
use_profile(None)
os.environ.pop("REPO_LUCENT_SETTINGS", None)                 # 还原环境
shutil.rmtree(work, ignore_errors=True)
try:
    SETTINGS_FILE.unlink()
except OSError:
    pass

summary = {"exit": 1 if fails else 0, "cases": RESULTS}
(HERE / "out" / "profile_verify.json").write_text(
    json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
sys.exit(1 if fails else 0)
