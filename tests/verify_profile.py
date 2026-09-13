# -*- coding: utf-8 -*-
"""档位 A 通用化（profile 配置化）· 验收脚本。

验收门（通用化-档位A-配置化实施方案 §5）：7 用例 + 零行为变化。
  1) 无 settings 注入      → 分析口径与内置 VeroRun 基线逐项一致
  2) 仓库签名可换          → 非 VeroRun 仓库（本工具自身）可被接受
  3) 核心知识库可换        → profile.core_modules 注入后 analyze_core 随之变化
  4) 签名约束生效          → files=["pyproject.toml"] 时仅含该文件的目录被接受
  5) 组件短路              → component.dir="" 时 plugins.items == [] 且不抛异常
  6) 门禁集可换            → default_gates=["file-too-large"] 时 expand() 只展开 1 项
  7) 前端仓库名可换        → frontend_repo_names=[] 时 discover_frontend_repos() 返回 []

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

from repo_lens import config
from repo_lens.cli import _setup
from repo_lens.gate import default_gates, expand
from repo_lens.frontend_analyzer import discover_frontend_repos
from repo_lens.plugin_analyzer import analyze_plugins
from repo_lens.core_analyzer import analyze_core

RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")

def write_settings(profile: dict | None) -> Path:
    """写一个显式 settings 文件（仅 profile 段）到 out/，并指向它。"""
    p = HERE / "out" / "_verify_profile_settings.json"
    p.write_text(json.dumps({"profile": profile} if profile is not None else {},
                            ensure_ascii=False), encoding="utf-8")
    os.environ["REPO_LENS_SETTINGS"] = str(p)
    return p

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
os.environ.pop("REPO_LENS_SETTINGS", None)

# ================================================================ 用例 1 ====
# 无 settings 注入：口径 = 内置 VeroRun 基线
cfg = make_cfg(repo)
pr = analyze_plugins(cfg, {})
core_res = analyze_core(cfg, {})
ok1 = (config.PLUGINS_DIR == "plugins"
       and config.MANIFEST_NAME == "plugin.json"
       and config.repo_signature() == {"dirs": ["plugins", "plugin_manager"], "files": []}
       and "plugin_manager" in config.KNOWN_CORE_MODULES
       and pr["count"] == 0 and isinstance(pr["items"], list)
       and any(m["name"] == "my_core" for m in core_res["modules"]))
record("no_settings_matches_baseline", ok1,
       f"PLUGINS_DIR={config.PLUGINS_DIR} MANIFEST={config.MANIFEST_NAME} "
       f"sig={config.repo_signature()} plugins.count={pr['count']} "
       f"my_core_in_core={any(m['name'] == 'my_core' for m in core_res['modules'])}")

# ================================================================ 用例 2 ====
# 仓库签名可换：--repo 指向本工具自身（无 plugins/+plugin_manager/）不再 SystemExit
write_settings({
    "name": "tool-self",
    "repo_signature": {"dirs": [], "files": ["repolens.py"]},
})
tool_self_cfg = make_cfg(HERE)          # 工具根含 repolens.py，无 plugins/ 目录
ok2 = tool_self_cfg.repo_root == HERE.resolve()
record("repo_signature_accepts_non_verorun", ok2,
       f"repo_root={tool_self_cfg.repo_root} (期望 {HERE.resolve()})")

# ================================================================ 用例 3 ====
# 核心知识库可换（增量合并）：注入 my_core 描述 + 新目录，内置键仍在
write_settings({
    "name": "cm-test",
    "core_modules": {"my_core": "自定义核心：注入测试", "brand_new_dir": "全新模块"},
})
cfg3 = make_cfg(repo)
cm = analyze_core(cfg3, {})
m = next((x for x in cm["modules"] if x["name"] == "my_core"), None)
ok3 = (m is not None and m["description"] == "自定义核心：注入测试"
       and "plugin_manager" in config.KNOWN_CORE_MODULES)
record("core_modules_injection_merge", ok3,
       f"my_core.description={m['description'] if m else None!r} "
       f"内置 plugin_manager 保留={'plugin_manager' in config.KNOWN_CORE_MODULES}")

# ================================================================ 用例 4 ====
# 签名约束生效：files=["pyproject.toml"] 时，仅含该文件的目录被显式接受
sig_dir = work / "sig_target"
sig_dir.mkdir(parents=True, exist_ok=True)
(sig_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
write_settings({
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
write_settings({
    "name": "no-component",
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
write_settings({"name": "gates-test", "default_gates": ["file-too-large"]})
make_cfg(repo)                                              # 触发 apply_profile
gates = default_gates()
chosen = expand(gates)
ok6a = gates == ["file-too-large"] and chosen == {"file-too-large"}
# 还原：null（无 default_gates 键）→ 维持历史行为（[]，不自动启用门禁）
write_settings({"name": "gates-off", "default_gates": None})
make_cfg(repo)
ok6b = default_gates() == []
record("default_gates_configurable", ok6a and ok6b,
       f"注入后 default_gates={gates} expand={sorted(chosen)}；"
       f"null 回落 default_gates={default_gates()} (期望 [])")

# ================================================================ 用例 7 ====
# 前端仓库名可换：默认可发现同级 verorun-workplace；注入 [] 后返回 []
fe = work / "verorun-workplace"
fe.mkdir(parents=True, exist_ok=True)
(fe / "package.json").write_text("{}", encoding="utf-8")
write_settings(None)                                        # {} → 全部回落内置
cfg7a = make_cfg(repo)
found_default = discover_frontend_repos(cfg7a)
ok7a = fe.resolve() in [p.resolve() for p in found_default]
write_settings({"name": "fe-off", "frontend_repo_names": []})
cfg7b = make_cfg(repo)
found_empty = discover_frontend_repos(cfg7b)
ok7b = found_empty == []
record("frontend_repo_names_configurable", ok7a and ok7b,
       f"默认发现 {len(found_default)} 个（含 verorun-workplace={ok7a}）；"
       f"注入 [] 后发现 {len(found_empty)} 个")

# ---------------------------------------------------------------- 汇总 ----
fails = [r for r in RESULTS if not r["ok"]]
os.environ.pop("REPO_LENS_SETTINGS", None)                 # 还原环境
# 复位到内置基线（防同进程后续使用污染）
write_settings(None)
os.environ.pop("REPO_LENS_SETTINGS", None)
shutil.rmtree(work, ignore_errors=True)
try:
    (HERE / "out" / "_verify_profile_settings.json").unlink()
except OSError:
    pass

summary = {"exit": 1 if fails else 0, "cases": RESULTS}
(HERE / "out" / "profile_verify.json").write_text(
    json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
sys.exit(1 if fails else 0)
