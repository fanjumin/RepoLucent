# -*- coding: utf-8 -*-
"""构建合成测试仓库（验证工具通用性 + 异常处理路径）。"""
import json
import pathlib

root = pathlib.Path(__file__).resolve().parent / "fixture_repo"

files = {
    "plugin_manager/base.py": '''#!/usr/bin/env python3
"""Plugin System - BasePlugin abstract class.
All plugins must inherit from BasePlugin.
"""
from abc import ABC, abstractmethod

class BasePlugin(ABC):
    name: str = ''
    def __init__(self):
        self.manager = None
    @abstractmethod
    def setup(self):
        'Called once before activate.'
    @abstractmethod
    def activate(self):
        'Enable plugin features.'
    def deactivate(self):
        'Tear down.'
    def t(self, text, locale=None):
        'Translate text via plugin i18n.'
        return text
''',
    "plugin_manager/manager.py": '''from .base import BasePlugin

class PluginManager:
    'Load and manage plugins.'
    def is_enabled(self, pid): return True
    def get_config(self, pid): return {}
''',
    "plugin_manager/discovery.py": '''"""Plugin Manager - PluginDiscovery.
Rules:
  1. Must be plugins/<name>/ subdir
  2. Must contain __init__.py
  3. Must contain plugin.json (valid JSON)
  4. Ignore dirs starting with _ or .
"""''',
    "shared/__init__.py": "def pooled():\n    return None\n",
    "i18n/__init__.py": "def _(s):\n    return s\n",
    "plugins/__init__.py": "# plugins namespace package\n",
    "plugins/_base/db.py": "def get_pooled_connection():\n    return None\n",
    "plugins/demo/__init__.py": '''from plugin_manager.base import BasePlugin

class DemoPlugin(BasePlugin):
    'Demo plugin for tests.'
    name = 'demo'
    def setup(self): pass
    def activate(self): pass
''',
    "plugins/demo/plugin.json": json.dumps({
        "identifier": "demo", "name": "Demo", "version": "1.0.0",
        "description": "demo", "author": "t", "min_app_version": "0.10.0",
        "agent_role": "business", "capabilities": ["demo.x"],
        "depends_on": {"shop": ">=1.0.0"}}),
    "plugins/demo/routes.py": '''from flask import Blueprint
demo_bp = Blueprint('demo', __name__, url_prefix='/admin/demo')

@demo_bp.route('/ping', methods=['GET', 'POST'])
def ping():
    return 'pong'

@demo_bp.route('/list')
def list_items():
    return []
''',
    "plugins/bad/__init__.py": "class BadPlugin:\n    pass\n",
    "plugins/bad/plugin.json": json.dumps({
        "identifier": "Bad-Id", "name": "Bad", "version": "1.0"}),
    "docs/plugin-manifest.schema.json": json.dumps({
        "required": ["identifier", "name", "version", "description", "author",
                     "min_app_version", "agent_role", "capabilities"],
        "properties": {
            "agent_role": {"enum": ["athena", "content", "business", "builder"]},
            "category": {"enum": ["system", "shop", "tools"]}}}),
}
for rel, content in files.items():
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

# GBK 编码文件（模拟仓库里 AGENTS.md 的编码情况）
(root / "AGENTS.md").write_text(
    "### 路径最高铁律\n操作前必须确认路径。\n### 流水线铁律\n禁止手动 push。\n",
    encoding="gbk")
print("fixture ready:", root)
