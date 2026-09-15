# -*- coding: utf-8 -*-
"""dep_graph 单元：验证符号级边的解析精度（连对的、丢外部、同文件局部、确定性）。"""
from __future__ import annotations

import pytest

from repo_lucent import dep_graph

pytestmark = pytest.mark.unit


def _entry(rel, *, imports=None, facts=None, calls=None, classes=None, functions=None):
    e = {"path": rel, "loc_code": 10, "imports": imports or [],
         "import_facts": facts or [], "calls": calls or [],
         "classes": classes or [], "functions": functions or []}
    return rel, e


def _cls(name, methods=()):
    return {"name": name, "methods": [{"name": m} for m in methods]}


def _fn(name):
    return {"name": name}


def _cache(*entries):
    return {rel: e for rel, e in entries}


def test_cross_file_call_via_import_resolves():
    # a.py 定义 helper()；b.py `from a import helper` 并调用 → 期望一条 b→a 的边
    a = _entry("pkg/a.py", functions=[_fn("helper")])
    b = _entry("pkg/b.py",
               facts=[{"kind": "from", "module": "pkg.a", "level": 0, "name": "helper", "alias": None}],
               calls=[{"caller": "run", "target": "helper"}],
               functions=[_fn("run")])
    g = dep_graph.build_graph(_cache(a, b))
    pairs = {(e["from"], e["to"]) for e in g["edges"]}
    assert ("pkg/b.py::run", "pkg/a.py::helper") in pairs


def test_same_file_local_call():
    a = _entry("pkg/a.py", functions=[_fn("outer"), _fn("inner")],
               calls=[{"caller": "outer", "target": "inner"}])
    g = dep_graph.build_graph(_cache(a))
    assert any(e["from"] == "pkg/a.py::outer" and e["to"] == "pkg/a.py::inner"
               for e in g["edges"])


def test_external_call_dropped():
    # 调用未定义于仓内的第三方/内建名 → 必须丢弃（precision-first）
    a = _entry("pkg/a.py",
               facts=[{"kind": "import", "module": "requests", "level": 0, "name": None, "alias": None}],
               calls=[{"caller": "f", "target": "requests.get"}],
               functions=[_fn("f")])
    g = dep_graph.build_graph(_cache(a))
    assert g["stats"]["edges_total"] == 0


def test_class_method_call_dotted_target():
    m = _entry("pkg/model.py", classes=[_cls("User", ["save"])])
    c = _entry("pkg/view.py",
               facts=[{"kind": "from", "module": "pkg.model", "level": 0, "name": "User", "alias": None}],
               calls=[{"caller": "do", "target": "User.save"}],
               functions=[_fn("do")])
    g = dep_graph.build_graph(_cache(m, c))
    assert any(e["to"].endswith("pkg/model.py::save") for e in g["edges"])


def test_deterministic_output():
    entries = [_cache(
        _entry("pkg/a.py", functions=[_fn("helper")]),
        _entry("pkg/b.py",
               facts=[{"kind": "from", "module": "pkg.a", "level": 0, "name": "helper", "alias": None}],
               calls=[{"caller": "run", "target": "helper"}], functions=[_fn("run")]))][0]
    g1 = dep_graph.build_graph(entries)
    g2 = dep_graph.build_graph(entries)
    assert g1["edges"] == g2["edges"] and g1["nodes"] == g2["nodes"]


def test_file_rollup_counts_weight():
    a = _entry("pkg/a.py", functions=[_fn("helper")])
    b = _entry("pkg/b.py",
               facts=[{"kind": "from", "module": "pkg.a", "level": 0, "name": "helper", "alias": None}],
               calls=[{"caller": "run", "target": "helper"}], functions=[_fn("run")])
    g = dep_graph.build_graph(_cache(a, b))
    assert {"from": "pkg/b.py", "to": "pkg/a.py", "weight": 1} in g["file_edges"]


def _inc_entry(rel, includes):
    """构造带 includes 的文件事实（模拟 cxx_ast 产物）。"""
    e = {"path": rel, "loc_code": 4, "imports": [], "import_facts": [], "calls": [],
         "classes": [], "functions": [],
         "includes": [{"target": t, "system": False} for t in includes], "defines": 0}
    return rel, e


def test_include_cycle_detected_and_excludes_non_participants():
    pc = _cache(
        _inc_entry("a/a.h", ["b/b.h"]),
        _inc_entry("a/b.h", ["a/a.h"]),      # a↔b 成环
        _inc_entry("a/c.h", ["a/a.h"]),      # 单向，不在环
    )
    g = dep_graph.build_graph(pc)
    assert g["stats"]["include_cycles"] == 1
    assert g["include_cycles"] == [["a/a.h", "a/b.h", "a/a.h"]]


def test_no_cycle_is_empty():
    pc = _cache(_inc_entry("a.h", ["b.h"]), _inc_entry("b.h", ["c.h"]),
                _inc_entry("c.h", []))
    assert dep_graph.build_graph(pc)["include_cycles"] == []


def test_file_includes_records_resolution():
    pc = _cache(_inc_entry("pkg/a.h", ["pkg/b.h"]), _inc_entry("pkg/b.h", []))
    g = dep_graph.build_graph(pc)
    assert g["file_includes"]["pkg/a.h"] == [{"target": "pkg/b.h", "resolved": "pkg/b.h"}]


def test_gate_include_cycle_fails_when_cycle_present(tmp_path):
    import json
    from types import SimpleNamespace
    from repo_lucent import gate
    graph = {"schema": 1, "include_cycles": [["a.h", "b.h", "a.h"]], "stats": {}}
    (tmp_path / "repo_lucent_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    res = gate._check_include_cycle({}, SimpleNamespace(out_dir=tmp_path))
    assert res.passed is False and len(res.hits) == 1 and res.name == "include-cycle"
    res2 = gate._check_include_cycle({}, SimpleNamespace(out_dir=tmp_path / "nope"))
    assert res2.passed is True
