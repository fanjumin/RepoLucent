# -*- coding: utf-8 -*-
"""cxx_ast 单元测试：验证 C/C++/Arduino 词法前端产出 py_ast 兼容事实。"""
from __future__ import annotations

import pytest

from repo_lucent import cxx_ast

pytestmark = pytest.mark.unit

_CPP = '''\
#include "widget.h"
#include <Arduino.h>
#include <vector>

#define MAX_N 8

class Widget : public Sensor, protected Base {
public:
  void begin(int baud) {
    Serial.begin(baud);
  }
  float read() { return 0.0f; }
};

void helper(int x) {
  foo(x);
}

int main() {
  return 0;
}
'''

_INO = '''\
#include "utils.h"

void setup() {
  Serial.begin(9600);
}

void loop() {
  tick();
}
'''


def _facts(text):
    return cxx_ast.parse_cxx_text("demo/demo.cpp", text)


def test_loc_and_shape_compatible_with_py_ast():
    e = _facts(_CPP)
    for key in ("path", "syntax_ok", "loc_total", "loc_code", "imports",
                "classes", "functions", "routes", "includes", "lang"):
        assert key in e, f"缺键 {key}"
    assert e["lang"] == "c"
    assert e["loc_code"] > 0


def test_includes_split_local_vs_system():
    e = _facts(_CPP)
    local = [i["target"] for i in e["includes"] if not i["system"]]
    system = [i["target"] for i in e["includes"] if i["system"]]
    assert "widget.h" in local
    assert "Arduino.h" in system and "vector" in system
    # imports（供 core 聚合）只收本地头的 stem
    assert "widget" in e["imports"]


def test_class_name_and_bases():
    e = _facts(_CPP)
    w = next((c for c in e["classes"] if c["name"] == "Widget"), None)
    assert w is not None
    assert set(w["bases"]) >= {"Sensor", "Base"}
    meths = {m["name"] for m in w["methods"]}
    assert "begin" in meths


def test_free_functions_detected():
    e = _facts(_CPP)
    names = {f["name"] for f in e["functions"]}
    assert {"helper", "main"} <= names


def test_defines_counted():
    e = _facts(_CPP)
    assert e["defines"] >= 1


def test_arduino_setup_loop():
    e = cxx_ast.parse_cxx_text("src/sketch.ino", _INO)
    names = {f["name"] for f in e["functions"]}
    assert {"setup", "loop"} <= names
    assert "utils" in e["imports"]


def test_deterministic_same_input():
    a = cxx_ast.parse_cxx_text("x.cpp", _CPP)
    b = cxx_ast.parse_cxx_text("x.cpp", _CPP)
    assert a["classes"] == b["classes"]
    assert a["functions"] == b["functions"]
    assert a["includes"] == b["includes"]
