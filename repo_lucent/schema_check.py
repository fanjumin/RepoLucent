# -*- coding: utf-8 -*-
"""产物契约轻校验器（阶段一 1-D）：纯标准库递归校验，不依赖 jsonschema 包。

为什么手写而不引 jsonschema（约束 1「纯标准库 runtime」）：
本工具只需要契约里真正用到的子集——`type`（含联合类型）、`required`、
`properties`、`items`、`enum`、本地 `$ref`（`#/definitions/*`）。引一个第三方
依赖只为这 6 个关键字，会破坏"零运行时依赖、离线可用"这一核心壁垒。

用途：Agent 与 CI 在消费 repo_lucent.json 前做一次机器可校验的兼容性检查
（Google Tricorder 式「产物契约 + 持续验证」的最小闭环）。

设计边界：
- 不做远程 `$ref`、不做 `allOf/anyOf/oneOf/patternProperties` 等组合关键字；
  契约文件本身也不使用它们（见 schemas/repo_lucent.schema.json）。
- 未知属性一律忽略（等价 additionalProperties=true）——这正是 MINOR 兼容策略：
  消费方必须忽略未识别字段。
"""
from __future__ import annotations

import json
from pathlib import Path

#: 契约文件位置（随包分发）
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "repo_lucent.schema.json"

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # 注意：bool 是 int 的子类，必须显式排除，否则 true 会被判为合法 integer
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema(path=None) -> dict:
    """读取契约文件；不存在/损坏时抛 ValueError（契约缺失属配置错误，不静默）。"""
    p = Path(path) if path else SCHEMA_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"契约文件不可读：{p}（{e}）") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"契约文件不是合法 JSON：{p}（{e}）") from e
    if not isinstance(data, dict):
        raise ValueError(f"契约文件顶层必须是 JSON 对象：{p}")
    return data


def _resolve(node, root: dict) -> dict:
    """解析本地 $ref（`#/definitions/x`）；非 dict 或无法解析时原样返回。"""
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur = root
        for part in ref[2:].split("/"):
            if not isinstance(cur, dict):
                return node
            cur = cur.get(part)
        if isinstance(cur, dict):
            # $ref 同层的其它关键字（如 description）不参与校验，直接返回目标
            return cur
    return node


def _type_ok(value, spec) -> bool:
    """单值类型判定；spec 为 str 或 list[str]。"""
    names = spec if isinstance(spec, list) else [spec]
    for n in names:
        fn = _TYPE_CHECKS.get(n)
        if fn is not None and fn(value):
            return True
    return False


def validate(payload, schema: dict | None = None, *, root: dict | None = None,
             path: str = "$") -> list[str]:
    """递归校验，返回错误消息列表（空列表 = 通过）。"""
    schema = schema if schema is not None else load_schema()
    root = root if root is not None else schema
    errs: list[str] = []
    node = _resolve(schema, root)
    if not isinstance(node, dict):
        return errs

    if "enum" in node and payload not in node["enum"]:
        errs.append(f"{path}: 值 {payload!r} 不在枚举 {node['enum']} 内")

    if "type" in node and not _type_ok(payload, node["type"]):
        errs.append(f"{path}: 期望类型 {node['type']}，实际 {type(payload).__name__}")
        return errs                       # 类型不符时不再深入，避免级联噪声

    if isinstance(payload, dict):
        for key in (node.get("required") or []):
            if key not in payload:
                errs.append(f"{path}: 缺少必填字段 `{key}`")
        for key, sub in (node.get("properties") or {}).items():
            if key in payload:
                errs += validate(payload[key], sub, root=root, path=f"{path}.{key}")

    if isinstance(payload, list) and isinstance(node.get("items"), dict):
        for i, item in enumerate(payload):
            errs += validate(item, node["items"], root=root, path=f"{path}[{i}]")
    return errs


def validate_report(data, path=None) -> list[str]:
    """按随包契约校验一份 repo_lucent.json 载荷。"""
    return validate(data, load_schema(path))


def main(argv=None) -> int:
    """CLI：python -m repo_lucent.schema_check <repo_lucent.json> [schema.json]"""
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("用法：python -m repo_lucent.schema_check <repo_lucent.json> [schema.json]")
        return 2
    target = Path(args[0])
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[schema] 载荷不可读：{e}")
        return 2
    try:
        errs = validate_report(data, args[1] if len(args) > 1 else None)
    except ValueError as e:
        print(f"[schema] {e}")
        return 2
    if errs:
        print(f"[schema] FAIL：{len(errs)} 处不兼容")
        for e in errs[:40]:
            print(f"  - {e}")
        return 1
    print(f"[schema] PASS：{target.name} 符合契约 "
          f"（schema_version={((data.get('meta') or {}).get('schema_version'))}）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
