# -*- coding: utf-8 -*-
"""`py_ast` / 并行解析 / 缓存判定的边界用例（阶段二 2-C 单元层）。

覆盖方案 2-C 点名的边界：畸形缩进、语法错误文件、中文类名与函数名、装饰器叠加、
async def、lambda 赋值、type hint 泛型、空文件、BOM、CRLF、GBK 误编码、
超深嵌套、Blueprint 变量路由、多装饰器 route、symlink 循环、0 字节 .py、仅注释文件。

另含 2-A/2-B 新增能力的契约用例：串行与并行结果一致、worker 兜底 entry 形状、
workers 语义解析、`raw` 参数等价性。
本层零外部依赖，全部在 tmp_path 内自造文件，不读取真实仓库。
"""
from __future__ import annotations

import ast
import codecs
import os
from pathlib import Path

import pytest

from repo_lens.cache import cache_key
from repo_lens.config import RepoConfig
from repo_lens.fs_scan import count_lines, decode_text
from repo_lens.py_ast import (_failed_entry, _parse_one, _up,
                              parse_files_parallel, parse_python_file,
                              resolve_workers)


# ----------------------------------------------------------------- 工具 ----

def _parse(tmp_path: Path, name: str, data, rel: str | None = None) -> dict:
    """写入临时文件并解析。data 为 bytes 时按原样落盘（可造 BOM / GBK）。"""
    raw = data if isinstance(data, bytes) else str(data).encode("utf-8")
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return parse_python_file(p, rel or name.replace("\\", "/"))


# ----------------------------------------------------------- 空 / 畸形 ----

def test_empty_file_zero_bytes(tmp_path):
    """0 字节 .py：语法合法（空模块），LOC 为 0。"""
    e = _parse(tmp_path, "empty.py", b"")
    assert e["syntax_ok"] is True and e["error"] is None
    assert (e["loc_total"], e["loc_code"]) == (0, 0)
    assert e["classes"] == [] and e["functions"] == [] and e["routes"] == []


def test_empty_string_equivalent(tmp_path):
    assert _parse(tmp_path, "e2.py", "")["syntax_ok"] is True


def test_only_comments(tmp_path):
    """仅注释文件：合法，代码行为 0。"""
    e = _parse(tmp_path, "c.py", "# a\n# b\n# c\n")
    assert e["syntax_ok"] is True and e["loc_code"] == 0 and e["loc_total"] == 3


def test_only_blank_lines(tmp_path):
    e = _parse(tmp_path, "b.py", "\n\n\n")
    assert e["syntax_ok"] is True and e["loc_code"] == 0


def test_malformed_indentation_returns_syntax_error(tmp_path):
    """畸形缩进：记 SyntaxError，绝不抛异常。"""
    e = _parse(tmp_path, "bad.py", "def f():\nreturn 1\n")
    assert e["syntax_ok"] is False and "SyntaxError" in e["error"]


def test_missing_function_body_returns_syntax_error(tmp_path):
    e = _parse(tmp_path, "trunc.py", "def f():\n")
    assert e["syntax_ok"] is False and "SyntaxError" in e["error"]


def test_null_bytes_tolerated(tmp_path):
    """含 NUL 字节：必须返回结构完好的结果，绝不抛异常。

    实测 CPython 3.13 已放开 `ast.parse` 对 NUL 的限制（3.11 及更早抛
    `ValueError: source code string cannot contain null bytes`）——本工具的
    `except (ValueError, RecursionError)` 分支正是为该历史行为预留。
    故本用例只固化跨版本不变的契约：**不崩、结构完好**。
    """
    e = _parse(tmp_path, "nul.py", b"x = 1\x00\n")
    assert isinstance(e["syntax_ok"], bool)
    assert set(e) == set(_parse(tmp_path, "ref.py", "x = 1\n"))


def test_binary_garbage_does_not_crash(tmp_path):
    e = _parse(tmp_path, "bin.py", bytes(range(256)))
    assert e["syntax_ok"] is False and isinstance(e["error"], str)


def test_tab_and_space_mixed_indent(tmp_path):
    """Tab 与空格混用：Python 3 直接判语法错误，仍须容错返回。"""
    e = _parse(tmp_path, "mix.py", "def f():\n\tif 1:\n        return 1\n")
    assert isinstance(e["syntax_ok"], bool) and "error" in e


# ------------------------------------------------------------- 编码边界 ----

def test_utf8_bom_is_stripped(tmp_path):
    """核心回归：带 UTF-8 BOM 的文件必须正常解析（v1.7.0 修复点）。

    修复前 `ast.parse` 会抛 `invalid non-printable character U+FEFF`，
    导致整个文件的路由/类/函数事实被静默丢弃。
    """
    body = "x = 1\n"
    e = _parse(tmp_path, "bom.py", codecs.BOM_UTF8 + body.encode("utf-8"))
    assert e["syntax_ok"] is True, e["error"]
    assert e["error"] is None


def test_utf8_bom_class_and_function_extracted(tmp_path):
    """BOM 文件的事实必须完整可见（证明修复有实际收益，而非仅不报错）。"""
    src = ('class Demo:\n    def run(self):\n        return 1\n\n\n'
           'def helper(a):\n    return a\n')
    e = _parse(tmp_path, "bom2.py", codecs.BOM_UTF8 + src.encode("utf-8"))
    assert e["syntax_ok"] is True
    assert [c["name"] for c in e["classes"]] == ["Demo"]
    assert [f["name"] for f in e["functions"]] == ["helper"]
    assert e["classes"][0]["lineno"] == 1
    assert e["functions"][0]["lineno"] == 6


def test_decode_text_absent_and_present_bom(tmp_path):
    """decode_text 对有无 BOM 的同内容文本产出等价结果。"""
    assert decode_text(codecs.BOM_UTF8 + "中文".encode()) == decode_text("中文".encode())


def test_crlf_line_endings(tmp_path):
    e = _parse(tmp_path, "crlf.py", b"def f():\r\n    return 1\r\n")
    assert e["syntax_ok"] is True and e["loc_total"] == 2
    assert [f["name"] for f in e["functions"]] == ["f"]


def test_cr_only_does_not_crash(tmp_path):
    """CR-only（旧 Mac 换行）：只要求不崩，不规定是否解析成功。"""
    e = _parse(tmp_path, "cr.py", b"x = 1\ry = 2\r")
    assert isinstance(e["syntax_ok"], bool)


def test_gbk_encoded_chinese_docstring(tmp_path):
    """GBK 误编码：回退 GB18030 解码，中文 docstring 不得丢。"""
    src = '"""中文模块说明。"""\n\ndef 函数():\n    return 1\n'
    e = _parse(tmp_path, "gbk.py", src.encode("gb18030"))
    assert e["syntax_ok"] is True, e["error"]
    assert "中文模块说明" in (e["docstring"] or "")
    assert [f["name"] for f in e["functions"]] == ["函数"]


def test_gbk_chinese_class_name(tmp_path):
    e = _parse(tmp_path, "gbk2.py", 'class 订单:\n    pass\n'.encode("gb18030"))
    assert e["syntax_ok"] is True and [c["name"] for c in e["classes"]] == ["订单"]


# --------------------------------------------------------- Python 语法 ----

def test_chinese_function_name_public(tmp_path):
    e = _parse(tmp_path, "cn.py", "def 处理订单(数量):\n    return 数量\n")
    assert e["syntax_ok"] is True
    f = e["functions"][0]
    assert f["name"] == "处理订单" and f["public"] is True


def test_private_function_not_public(tmp_path):
    e = _parse(tmp_path, "pv.py", "def _internal():\n    pass\n")
    assert e["functions"][0]["public"] is False


def test_async_def_extracted(tmp_path):
    e = _parse(tmp_path, "a.py", "import asyncio\n\n\nasync def fetch(x):\n    return x\n")
    assert [f["name"] for f in e["functions"]] == ["fetch"]
    assert e["functions"][0]["signature"].startswith("def fetch")


def test_decorator_stack_recorded(tmp_path):
    src = ("class C:\n"
           "    @property\n"
           "    @staticmethod\n"
           "    def v():\n"
           "        return 1\n")
    e = _parse(tmp_path, "dec.py", src)
    m = e["classes"][0]["methods"][0]
    assert m["decorators"] == ["property", "staticmethod"]


def test_lambda_assignment_no_crash(tmp_path):
    """lambda 赋值不进 functions（只取顶层 def），但不得报错。"""
    e = _parse(tmp_path, "lam.py", "f = lambda x: x + 1\n")
    assert e["syntax_ok"] is True and e["functions"] == []


def test_type_hint_generics_in_signature(tmp_path):
    e = _parse(tmp_path, "th.py",
               "def f(x: list[int], y: dict[str, int]) -> tuple[int, ...]:\n    return ()\n")
    sig = e["functions"][0]["signature"]
    assert "list[int]" in sig and "dict[str, int]" in sig
    assert "tuple[int, ...]" in sig


def test_positional_only_marker(tmp_path):
    e = _parse(tmp_path, "po.py", "def f(a, b, /, c):\n    return c\n")
    assert "/ " in e["functions"][0]["signature"] or ", /, " in e["functions"][0]["signature"]


def test_keyword_only_marker(tmp_path):
    e = _parse(tmp_path, "ko.py", "def f(a, *, b):\n    return b\n")
    assert "*" in e["functions"][0]["signature"]


def test_varargs_and_kwargs(tmp_path):
    e = _parse(tmp_path, "va.py", "def f(*args, **kwargs):\n    return args\n")
    sig = e["functions"][0]["signature"]
    assert "*args" in sig and "**kwargs" in sig


def test_default_values_and_annotations(tmp_path):
    e = _parse(tmp_path, "dv.py", "def f(a: int = 3, b='x'):\n    return a\n")
    sig = e["functions"][0]["signature"]
    assert "a: int = 3" in sig and "b='x'" in sig


def test_abstract_method_detection(tmp_path):
    src = ("import abc\n\n\n"
           "class A(abc.ABC):\n"
           "    @abstractmethod\n"
           "    def run(self):\n"
           "        ...\n")
    e = _parse(tmp_path, "ab.py", src)
    c = e["classes"][0]
    assert c["abstract_methods"] == ["run"]
    assert c["methods"][0]["abstract"] is True


def test_class_bases_and_base_plugin_flag(tmp_path):
    src = ("class Mine(BasePlugin):\n"
           "    pass\n\n\n"
           "class Other(Mixin, abc.ABC):\n"
           "    pass\n")
    e = _parse(tmp_path, "bs.py", src)
    assert e["classes"][0]["bases"] == ["BasePlugin"]
    assert e["classes"][0]["inherits_base_plugin"] is True
    assert e["classes"][1]["inherits_base_plugin"] is False


def test_class_methods_have_lineno(tmp_path):
    src = "class C:\n    def a(self):\n        pass\n\n    def b(self):\n        pass\n"
    e = _parse(tmp_path, "cl.py", src)
    assert [m["lineno"] for m in e["classes"][0]["methods"]] == [2, 5]
    assert e["classes"][0]["lineno"] == 1


def _nested_if(n: int) -> str:
    """生成**逐层递进**的 n 层嵌套 if（每层缩进递增 4 空格）。

    注意：不可写成 `"if 1:\\n" + "    if 1:\\n" * n`——那是同级语句而非嵌套，
    会得到 `expected an indented block`，测不到真正的深度边界。
    """
    return ("".join("    " * i + "if 1:\n" for i in range(n))
            + "    " * n + "pass\n")


def test_deeply_nested_blocks_ok(tmp_path):
    """90 层嵌套：在 CPython 缩进上限（100 层）以内，必须正常解析。"""
    e = _parse(tmp_path, "deep.py", _nested_if(90))
    assert e["syntax_ok"] is True, e["error"]


def test_nesting_beyond_indent_limit_tolerated(tmp_path):
    """超出 CPython 缩进上限：须容错返回，而非崩溃。

    实测 200 层触发 `IndentationError: too many levels of indentation`。
    `IndentationError` 是 `SyntaxError` 的子类，走既有 syntax_ok=False 通道，
    不会中断整体分析。
    """
    e = _parse(tmp_path, "deep2.py", _nested_if(200))
    assert e["syntax_ok"] is False
    assert "indentation" in e["error"].lower()


# ------------------------------------------------------- 路由 / 文档 / 导入 ----

def test_blueprint_url_prefix(tmp_path):
    e = _parse(tmp_path, "bp.py",
               "from flask import Blueprint\n"
               "bp = Blueprint('d', __name__, url_prefix='/admin/d')\n")
    assert e["blueprints"][0]["url_prefix"] == "'/admin/d'"
    assert e["blueprints"][0]["var"] == "bp"


def test_blueprint_attribute_form(tmp_path):
    """flask.Blueprint(...) 属性调用形式同样识别。"""
    e = _parse(tmp_path, "bp2.py",
               "import flask\nbp = flask.Blueprint('d', __name__)\n")
    assert len(e["blueprints"]) == 1


def test_multiple_decorators_route(tmp_path):
    src = ("from flask import Blueprint\n"
           "bp = Blueprint('d', __name__)\n\n\n"
           "def login_required(f):\n"
           "    return f\n\n\n"
           "@bp.route('/x')\n"
           "@login_required\n"
           "def x():\n"
           "    return 1\n")
    e = _parse(tmp_path, "mr.py", src)
    assert len(e["routes"]) == 1
    assert e["routes"][0]["rule"] == "'/x'" and e["routes"][0]["endpoint"] == "x"


def test_route_methods_normalized_and_sorted(tmp_path):
    src = ("from flask import Blueprint\n"
           "bp = Blueprint('d', __name__)\n\n\n"
           "@bp.route('/m', methods=['post', 'get', 'GET'])\n"
           "def m():\n"
           "    return 1\n")
    e = _parse(tmp_path, "rm.py", src)
    assert e["routes"][0]["methods"] == ["GET", "POST"]      # 去重 + 大写 + 排序


def test_route_lineno_matches_def_line(tmp_path):
    src = ("from flask import Blueprint\n"
           "bp = Blueprint('d', __name__)\n"
           "def _filler():\n"
           "    pass\n\n\n"
           "@bp.route('/z')\n"
           "def z():\n"
           "    return 1\n")
    e = _parse(tmp_path, "rl.py", src)
    assert e["routes"][0]["lineno"] == 8


def test_route_default_method_is_get(tmp_path):
    src = ("from flask import Blueprint\nbp = Blueprint('d', __name__)\n\n\n"
           "@bp.route('/g')\ndef g():\n    return 1\n")
    e = _parse(tmp_path, "rg.py", src)
    assert e["routes"][0]["methods"] == ["GET"]


def test_route_url_prefix_resolved_from_blueprint(tmp_path):
    src = ("from flask import Blueprint\n"
           "bp = Blueprint('d', __name__, url_prefix='/api/v1')\n\n\n"
           "@bp.route('/item')\ndef item():\n    return 1\n")
    e = _parse(tmp_path, "rp.py", src)
    assert e["routes"][0]["url_prefix"] == "'/api/v1'"


def test_module_docstring_first_line_and_full(tmp_path):
    e = _parse(tmp_path, "ds.py", '"""第一行。\n\n第二段。\n"""\nx = 1\n')
    assert e["docstring"] == "第一行。"
    assert "第二段。" in e["docstring_full"]


def test_imports_collection(tmp_path):
    src = ("import os\nimport os.path\nfrom flask import Blueprint\n"
           "from . import sibling\nfrom .pkg import thing\n\nx = 1\n")
    e = _parse(tmp_path, "im.py", src)
    assert {"os", "os.path", "flask", "pkg"} <= set(e["imports"])
    assert e["imports"] == sorted(e["imports"])


def test_long_default_is_truncated(tmp_path):
    """超长默认值经 `_up` 截断，避免单行撑爆产物。"""
    e = _parse(tmp_path, "tr.py", "def f(x=" + repr("z" * 300) + "):\n    return x\n")
    sig = e["functions"][0]["signature"]
    assert "..." in sig and len(sig) < 200


def test_up_truncation_boundary():
    """`_up` 截断边界：超限恰好 80 字符且以 `...` 收尾；未超限原样返回。"""
    long_node = ast.parse("x = " + repr("z" * 300)).body[0].value
    assert len(_up(long_node)) == 80 and _up(long_node).endswith("...")
    assert _up(ast.parse("x = 1").body[0].value) == "1"


def test_loc_matches_count_lines(tmp_path):
    """LOC 与 fs_scan.count_lines 同口径（跨模块一致性）。"""
    src = "# comment\n\nx = 1\n"
    e = _parse(tmp_path, "loc.py", src)
    assert (e["loc_total"], e["loc_code"]) == count_lines(src)


# --------------------------------------------------- 2-A / 2-B 新增契约 ----

def test_entry_shape_matches_contract(tmp_path):
    """worker 兜底 entry 与正常 entry 的键集必须完全一致。"""
    normal = _parse(tmp_path, "ok.py", "x = 1\n")
    fallback = _failed_entry("a/b.py", "OSError: x")
    assert set(fallback) == set(normal)
    assert fallback["syntax_ok"] is False and set(fallback["routes"]) == set()


def test_parse_one_returns_digest(tmp_path):
    """2-B：worker 返回内容哈希，且与 cache_key 口径一致。"""
    raw = b"x = 1\n"
    p = tmp_path / "d.py"
    p.write_bytes(raw)
    rel, entry, digest = _parse_one((str(p), "d.py"))
    assert rel == "d.py" and digest == cache_key(raw) and entry["syntax_ok"] is True


def test_parse_one_missing_file(tmp_path):
    """文件消失：返回兜底 entry 且 digest 为 None（调用方按 miss 处理）。"""
    rel, entry, digest = _parse_one((str(tmp_path / "nope.py"), "nope.py"))
    assert rel == "nope.py" and digest is None
    assert entry["syntax_ok"] is False and entry["error"].startswith("OSError")


def test_raw_param_equivalent(tmp_path):
    """2-A：传入 raw 与自行读盘必须产出完全相同的 entry。"""
    src = "def f():\n    return 1\n"
    p = tmp_path / "eq.py"
    p.write_bytes(src.encode("utf-8"))
    assert parse_python_file(p, "eq.py") == parse_python_file(
        p, "eq.py", raw=src.encode("utf-8"))


def test_resolve_workers_semantics():
    """workers 语义：0/None=auto、1=串行、负数=串行、显式值生效。"""
    assert resolve_workers(1) == 1
    assert resolve_workers(-3) == 1
    assert resolve_workers(8) == 8
    auto = resolve_workers(0)
    assert auto == resolve_workers(None) and 1 <= auto <= 4


def test_serial_and_parallel_agree(tmp_path):
    """2-A 正确性前提：同一批文件串行与并行结果逐项一致。

    用 min_files=1 强制走进程池；若环境禁用子进程，函数内部自动串行兜底，
    断言依然成立（同时覆盖了降级路径）。
    """
    items = []
    for i in range(6):
        p = tmp_path / f"m{i}.py"
        p.write_bytes(f"def f{i}():\n    return {i}\n".encode())
        items.append((str(p), f"m{i}.py"))
    serial = [_parse_one(it) for it in items]
    parallel = parse_files_parallel(items, workers=2, min_files=1)
    assert parallel == serial
    assert [r[0] for r in parallel] == [it[1] for it in items]     # 保序


def test_parse_files_parallel_empty():
    assert parse_files_parallel([]) == []


def test_symlink_loop_terminates(tmp_path):
    """symlink 循环：默认 follow_symlinks=False 时遍历必须终止。"""
    root = tmp_path / "repo"
    (root / "plugins").mkdir(parents=True)
    (root / "plugin_manager").mkdir(parents=True)
    (root / "plugins" / "a.py").write_text("x = 1\n", encoding="utf-8")
    try:
        (root / "plugins" / "loop").symlink_to(root / "plugins", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建目录符号链接（需管理员权限）")
    cfg = RepoConfig(repo_root=root, out_dir=tmp_path / "out")
    cfg.follow_symlinks = False
    from repo_lens.fs_scan import invalidate_walk_index, iter_repo_files
    invalidate_walk_index()
    names = [rel.as_posix() for rel, _ in iter_repo_files(cfg)]
    assert "plugins/a.py" in names
    assert len(names) < 50          # 未因循环而无限展开
