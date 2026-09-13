# -*- coding: utf-8 -*-
"""P0 §4.1 配置外部化 · 验收脚本。

验收门（设计文档 §5）：settings 能注入而源码不动。

三用例：
  1) 无 settings.json  → _setup 产出的 cfg.max_* 等于内置默认（等价旧行为）
  2) 有 settings.json（max_plugins_in_ai_context=10）→ cfg 值变为 10（源码未改，仅配置生效）
  3) 密钥隔离          → get_secret 只读环境变量，绝不读 settings.json 明文
  +) render 集成       → 注入的 max_plugins 真正裁剪 AI 上下文插件表行数

输出纯 ASCII JSON 到 out/settings_verify.json（规避 PowerShell UTF-16 落盘问题）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from repo_lucent.config import ToolConfig
from repo_lucent.cli import _setup
from repo_lucent.settings import load_settings, get_secret
from repo_lucent.report_ai import render_ai_context

RESULTS = []

def record(name, ok, detail):
    RESULTS.append({"case": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")

def make_fake_repo(root: Path) -> Path:
    """构造最小 VeroRun 仓库（仅 plugins/ + plugin_manager/ 占位，满足 autodetect）。"""
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    (root / "plugin_manager").mkdir(parents=True, exist_ok=True)
    return root

def fake_args(repo: Path, tree_depth=None):
    return SimpleNamespace(repo=str(repo), tree_depth=tree_depth,
                           extra_repos=None, out=None, module=None, plugin=None)

# ---------------------------------------------------------------- 用例 1/3 ----
# 无 settings：删除任何 REPO_LUCENT_SETTINGS 指向，且项目根无 settings.json
repo = HERE / "out" / "_verify_repo"
if repo.exists():
    shutil.rmtree(repo)
make_fake_repo(repo)

os.environ.pop("REPO_LUCENT_SETTINGS", None)
cfg_default = _setup(fake_args(repo))
ok_1a = cfg_default.max_plugins_in_ai_context == 60
ok_1b = cfg_default.max_tree_depth == 2
ok_1c = ToolConfig.from_settings().mcp_enabled is True
record("no_settings_defaults", ok_1a and ok_1b and ok_1c,
       f"max_plugins={cfg_default.max_plugins_in_ai_context} max_tree_depth={cfg_default.max_tree_depth} "
       f"mcp_enabled={ok_1c} (期望 60/2/True)")

# ---------------------------------------------------------------- 用例 2/3 ----
# 有 settings：max_plugins_in_ai_context=10, mcp_enabled=false
settings_file = HERE / "out" / "_verify_settings.json"
settings_file.write_text(json.dumps({
    "max_plugins_in_ai_context": 10,
    "max_tree_depth": 3,
    "mcp_enabled": False,
}), encoding="utf-8")
os.environ["REPO_LUCENT_SETTINGS"] = str(settings_file)

cfg_injected = _setup(fake_args(repo))
ok_2a = cfg_injected.max_plugins_in_ai_context == 10      # 源码未改，仅配置生效
ok_2b = cfg_injected.max_tree_depth == 3
ok_2c = ToolConfig.from_settings().mcp_enabled is False
record("with_settings_injection", ok_2a and ok_2b and ok_2c,
       f"max_plugins={cfg_injected.max_plugins_in_ai_context} max_tree_depth={cfg_injected.max_tree_depth} "
       f"mcp_enabled={ok_2c} (期望 10/3/False；源码未改，仅配置注入)")

# CLI args 仍最高优先：显式 --tree-depth 11 应覆盖 settings 的 3
cfg_cli_wins = _setup(fake_args(repo, tree_depth=11))
ok_2d = cfg_cli_wins.max_tree_depth == 11
record("cli_args_override_settings", ok_2d,
       f"--tree-depth 11 → max_tree_depth={cfg_cli_wins.max_tree_depth} (期望 11，CLI 优先)")

# ---------------------------------------------------------------- 用例 3/3 ----
# 密钥隔离：settings 含明文密钥，get_secret 必须忽略 settings，只读环境变量
secret_settings = HERE / "out" / "_verify_secret_settings.json"
secret_settings.write_text(json.dumps({
    "REPO_LUCENT_TEST_KEY": "settings_plaintext_SHOULD_BE_IGNORED",
    "test_key": "leaked",
}), encoding="utf-8")
os.environ["REPO_LUCENT_SETTINGS"] = str(secret_settings)
os.environ["REPO_LUCENT_TEST_KEY"] = "env_value_123"

# 子用例 3a：环境变量存在时，get_secret 读 env（settings 被忽略）
v_env = get_secret("REPO_LUCENT_TEST_KEY")
ok_3a = v_env == "env_value_123"
record("secret_reads_env", ok_3a,
       f"get_secret(REPO_LUCENT_TEST_KEY)={v_env!r} (期望 'env_value_123'，settings 明文被忽略)")

# 子用例 3b：环境变量缺失时，get_secret 仍为 None（证明从不读 settings.json）
os.environ.pop("REPO_LUCENT_TEST_KEY", None)
v_none = get_secret("REPO_LUCENT_TEST_KEY")
ok_3b = v_none is None
record("secret_never_reads_settings", ok_3b,
       f"env 缺失时 get_secret(REPO_LUCENT_TEST_KEY)={v_none!r} (期望 None；settings 内的明文不被读取)")

# ---------------------------------------------------------------- render 集成 ----
# 注入的 max_plugins 真正裁剪 AI 上下文插件表行数
data = {
    "meta": {"generated_at": "2026-09-12T00:00:00", "repo_root": "fake"},
    "core": {"modules": [{"name": "m", "description": "d"}],
             "plugin_system": {}},   # 无 base_plugin → BasePlugin 块被跳过
    "plugins": {"items": [
        {"identifier": f"plugin_{i:02d}", "version": "1.0.0",
         "agent_role": "r", "route_count": i} for i in range(15)
    ]},
    "interactions": {
        "plugins_import_core": [],                 # [:6] 为空 → 该段跳过
        "boundary_observations": {"rule": "禁止私建数据库连接池"},
    },
    "standards": {"manifest_required": ["identifier", "name", "version"]},
}
row_re = re.compile(r"^\| .+ \| .+ \| .+ \| \d+ \|$")
# 默认注入（无 settings）→ 60，应保留全部 15 行
os.environ.pop("REPO_LUCENT_SETTINGS", None)
cfg_d = _setup(fake_args(repo))
out_d = render_ai_context(data, cfg_d.max_plugins_in_ai_context)
rows_d = sum(1 for ln in out_d.splitlines() if row_re.match(ln))
ok_rd = rows_d == 15

# 注入 10 → 应只保留 10 行（裁剪生效）
os.environ["REPO_LUCENT_SETTINGS"] = str(settings_file)
cfg_i = _setup(fake_args(repo))
out_i = render_ai_context(data, cfg_i.max_plugins_in_ai_context)
rows_i = sum(1 for ln in out_i.splitlines() if row_re.match(ln))
ok_ri = rows_i == 10
record("render_respects_injected_limit", ok_rd and ok_ri,
       f"默认(max60)→{rows_d} 行；注入(max10)→{rows_i} 行 (期望 15/10)")

# ---------------------------------------------------------------- 汇总 ----
fails = [r for r in RESULTS if not r["ok"]]
os.environ.pop("REPO_LUCENT_SETTINGS", None)  # 还原环境
shutil.rmtree(repo, ignore_errors=True)
for f in (settings_file, secret_settings):
    try:
        f.unlink()
    except OSError:
        pass

summary = {"exit": 1 if fails else 0, "cases": RESULTS}
(HERE / "out" / "settings_verify.json").write_text(
    json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
sys.exit(1 if fails else 0)
