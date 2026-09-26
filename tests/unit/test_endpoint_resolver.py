# -*- coding: utf-8 -*-
"""endpoint_resolver 单测（v2.1.0 端点全景归链）。

覆盖 8 种 resolution 形态 + py_ast 新增采集面（简写装饰器 / add_url_rule /
registrar / calls1）。全部用「源码字符串 → parse_python_file → enrich」的
最小闭环，不触真实仓库，保证与 VeroRun 实盘验收互补。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repo_lucent import config
from repo_lucent.endpoint_resolver import build_resolver
from repo_lucent.py_ast import parse_python_file


def make_cache(files: dict[str, str]) -> dict:
    cache = {}
    for rel, src in files.items():
        p = Path("MUST_NOT_READ") / rel          # raw 已传入，read 分支不会触发
        entry = parse_python_file(p, rel, raw=src.encode("utf-8"))
        # 与 core_analyzer / plugin_analyzer 聚合口径一致：route 携带 "file"
        for r in entry["routes"]:
            r["file"] = rel
        cache[rel] = entry
    return cache


def enrich(cache: dict, rel: str) -> list[dict]:
    r = build_resolver(None, cache)
    routes = cache[rel]["routes"]
    r.enrich(routes)
    return routes


# ------------------------------------------------------------------ py_ast ----

def test_py_ast_shorthand_decorator():
    src = (
        "from flask import Blueprint\n"
        "bp = Blueprint('b', __name__, url_prefix='/x')\n"
        "@bp.get('/g')\n"
        "def g():\n"
        '    """G 用途"""\n'
        "@bp.post('/p')\n"
        "def p(): pass\n")
    e = parse_python_file(Path("x.py"), "pkg/m.py", raw=src.encode("utf-8"))
    by_rule = {r["rule"]: r for r in e["routes"]}
    assert by_rule["'/g'"]["methods"] == ["GET"]
    assert by_rule["'/g'"]["via"] == "shorthand"
    assert by_rule["'/g'"]["purpose"] == "G 用途"
    assert by_rule["'/p'"]["methods"] == ["POST"]


def test_py_ast_add_url_rule_and_registrar():
    src = (
        "from flask import Blueprint\n"
        "bp = Blueprint('b', __name__)\n"
        "bp.add_url_rule('/u', 'ep_u', methods=['GET', 'post'])\n"
        "def register_routes(view):\n"
        "    @view.route('/inner')\n"
        "    def inner(): pass\n"
        "register_routes(bp)\n")
    e = parse_python_file(Path("x.py"), "m.py", raw=src.encode("utf-8"))
    by_rule = {r["rule"]: r for r in e["routes"]}
    assert by_rule["'/u'"]["via"] == "add_url_rule"
    assert by_rule["'/u'"]["methods"] == ["GET", "POST"]     # 归一大写排序
    assert by_rule["'/u'"]["endpoint"] == "ep_u"
    assert by_rule["'/inner'"]["owner_is_param"] is True
    assert by_rule["'/inner'"]["registrar"] == "register_routes"
    assert {"f": "register_routes", "a": "bp"} in e["calls1"]


# ---------------------------------------------------------------- resolver ----

def test_same_file_blueprint():
    cache = make_cache({
        "a.py": "from flask import Blueprint\n"
                "bp = Blueprint('b', __name__, url_prefix='/admin/x')\n"
                "@bp.route('/ping')\n"
                "def ping(): pass\n"})
    r = enrich(cache, "a.py")[0]
    assert r["resolution"] == "blueprint"
    assert r["path"] == "/admin/x/ping"


def test_noprefix_blueprint():
    cache = make_cache({
        "a.py": "from flask import Blueprint\n"
                "bp = Blueprint('b', __name__)\n"
                "@bp.route('/root')\n"
                "def x(): pass\n"})
    r = enrich(cache, "a.py")[0]
    assert r["resolution"] == "noprefix" and r["path"] == "/root"


def test_cross_file_import_prefix():
    cache = make_cache({
        "p/admin.py": "from flask import Blueprint\n"
                      "admin_bp = Blueprint('admin', __name__, url_prefix='/admin')\n",
        "p/users.py": "from .admin import admin_bp\n"
                      "@admin_bp.route('/users')\n"
                      "def users(): pass\n"})
    r = enrich(cache, "p/users.py")[0]
    assert r["resolution"] == "imported" and r["path"] == "/admin/users"


def test_package_reexport_two_hop():
    cache = make_cache({
        "p/routes.py": "from flask import Blueprint\n"
                       "main_bp = Blueprint('m', __name__, url_prefix='/main')\n"
                       "from .sub import register_sub\n"
                       "register_sub(main_bp)\n",
        "p/sub/__init__.py": "from .impl import register_sub\n",
        "p/sub/impl.py": "def register_sub(bp):\n"
                         "    @bp.route('/deep')\n"
                         "    def deep(): pass\n"})
    r = enrich(cache, "p/sub/impl.py")[0]
    assert r["resolution"] == "registrar" and r["path"] == "/main/deep"


def test_registrar_call_inside_function():
    """site_settings 形态：包 __init__ 的函数体内 from-import + 直调。"""
    cache = make_cache({
        "p/settings/__init__.py":
            "from flask import Blueprint\n"
            "s_bp = Blueprint('s', __name__, url_prefix='/admin/s')\n"
            "def go():\n"
            "    from .routes import register_routes\n"
            "    register_routes(s_bp)\n",
        "p/settings/routes.py":
            "def register_routes(bp):\n"
            "    @bp.route('/tokens')\n"
            "    def tokens(): pass\n"})
    r = enrich(cache, "p/settings/routes.py")[0]
    assert r["resolution"] == "registrar" and r["path"] == "/admin/s/tokens"


def test_default_prefix_convention_applies(monkeypatch):
    monkeypatch.setattr(config, "PLUGINS_DIR", "plugins")
    monkeypatch.setattr(config, "ENDPOINT_DEFAULT_PLUGIN_PREFIX",
                        "/plugin/{identifier}")
    cache = make_cache({
        "plugins/coupon/routes.py":
            "from flask import Blueprint\n"
            "coupon_bp = Blueprint('c', __name__)\n"
            "@coupon_bp.route('/admin/list')\n"
            "def lst(): pass\n"})
    r = enrich(cache, "plugins/coupon/routes.py")[0]
    assert r["resolution"] == "default" and r["path"] == "/plugin/coupon/admin/list"


def test_default_prefix_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "PLUGINS_DIR", "plugins")
    monkeypatch.setattr(config, "ENDPOINT_DEFAULT_PLUGIN_PREFIX", None)
    cache = make_cache({
        "plugins/coupon/routes.py":
            "from flask import Blueprint\n"
            "coupon_bp = Blueprint('c', __name__)\n"
            "@coupon_bp.route('/admin/list')\n"
            "def lst(): pass\n"})
    r = enrich(cache, "plugins/coupon/routes.py")[0]
    assert r["resolution"] == "noprefix" and r["path"] == "/admin/list"


def test_nonliteral_prefix_and_dynamic_rule():
    cache = make_cache({
        "a.py": "from cfg import PREFIX\n"
                "from flask import Blueprint\n"
                "bp = Blueprint('b', __name__, url_prefix=PREFIX)\n"
                "@bp.route('/ok')\n"
                "def ok(): pass\n",
        "d.py": "from flask import Blueprint\n"
                "bp = Blueprint('d', __name__, url_prefix='/d')\n"
                "import os\n"
                "@bp.route('/x/' + os.environ['T'])\n"
                "def dyn(): pass\n"})
    ra = enrich(cache, "a.py")[0]
    assert ra["resolution"] == "nonliteral" and ra["path"] is None
    rd = enrich(cache, "d.py")[0]
    assert rd["resolution"] == "blueprint" and rd["path"] is None  # rule 不可定


def test_app_like_owner():
    cache = make_cache({
        "srv.py": "@app.route('/health')\n"
                  "def health(): pass\n"})
    r = enrich(cache, "srv.py")[0]
    assert r["resolution"] == "app" and r["path"] == "/health"


def test_stats_shape_and_idempotent_fields():
    cache = make_cache({
        "a.py": "from flask import Blueprint\n"
                "bp = Blueprint('b', __name__, url_prefix='/x')\n"
                "@bp.route('')\n"
                "def root(): pass\n"})
    r = build_resolver(None, cache)
    st = r.enrich(cache["a.py"]["routes"])
    assert st["total"] == 1 and st["resolved"] == 1 and st["unresolved"] == 0
    # 空 rule → 前缀本身（Flask 语义）
    assert cache["a.py"]["routes"][0]["path"] == "/x"
