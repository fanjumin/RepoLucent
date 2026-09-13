# -*- coding: utf-8 -*-
"""离线数据库替身桩（通用化自 AI 脚本 plugins/chatbot/tests/_stubs.py）。

价值：不连真库、不装 DB 依赖，就能对"以 `with db() as conn: conn.execute(sql, params)
      .fetchall()/.fetchone()` 为契约"的业务代码跑离线单测。原始桩把表结构硬绑到 chatbot；
本桩做成**表无关**：用 `SimpleTable` 声明任意内存表 + 关键列即可自动支持 SELECT/INSERT/
UPDATE/DELETE，或用 `db.rule(prefix, fn)` 接管任意 SQL；`install_stub(module, db)` 把被测
模块的 `_db()` 边界替换成桩并返回还原函数。

用法（作为库导入）：
    from repo_lucent.scriptlib.offline_db_stub import FakeDB, SimpleTable, install_stub
    db = FakeDB(); db.add_table("plugin_configs", SimpleTable(key_cols=["plugin_name","key"]))
    restore = install_stub(my_module, db)   # my_module._db() 现返回该桩
    ...断言...; restore()

CLI（自检，证明替身行为正确）：
    python -m repo_lucent.scriptlib.offline_db_stub --self-test
    repolucent.py script run offline_db_stub

退出码：0=自检通过；1=自检失败。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Optional


class FakeCursor:
    def __init__(self, rows: Optional[list] = None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class SimpleTable:
    """一个内存表：以 key_cols 唯一定位行；rows 为 list[dict]。"""

    def __init__(self, key_cols: list[str], seed: Optional[list[dict]] = None):
        self.key_cols = key_cols
        self.rows: list[dict] = [dict(r) for r in (seed or [])]

    def key_of(self, row: dict) -> tuple:
        return tuple(row.get(k) for k in self.key_cols)

    def find(self, kv: dict) -> Optional[dict]:
        for r in self.rows:
            if all(r.get(k) == v for k, v in kv.items()):
                return r
        return None

    def upsert(self, row: dict) -> None:
        ex = self.find({k: row[k] for k in self.key_cols if k in row})
        if ex:
            ex.update(row)
        else:
            self.rows.append(dict(row))

    def delete(self, kv: dict) -> int:
        before = len(self.rows)
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in kv.items())]
        return before - len(self.rows)


class FakeDB:
    """表无关的内存 SQL 语义替身。仅实现被测代码用到的语句子集（按归一化 SQL 前缀分派）。"""

    def __init__(self):
        self.tables: dict[str, SimpleTable] = {}
        self._rules: list[tuple[str, Callable[[str, tuple, "FakeDB"], FakeCursor]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.executed: list[str] = []
        self.closed = False

    # -- 装配 ---------------------------------------------------------
    def add_table(self, name: str, table: SimpleTable) -> "FakeDB":
        self.tables[name] = table
        low = name.lower()
        # SELECT ... FROM <name>
        self.rule(f"select", _make_select(low, name))
        self.rule(f"insert into {low}", _make_insert(name))
        self.rule(f"update {low}", _make_update(name))
        self.rule(f"delete from {low}", _make_delete(name))
        return self

    def rule(self, prefix: str, fn) -> "FakeDB":
        self._rules.append((prefix.lower().strip(), fn))
        return self

    # -- 契约方法 -----------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        norm = " ".join(sql.split())
        low = norm.lower()
        self.executed.append(low[:120])
        p = tuple(params or ())
        # DDL：忽略并记录
        if low.startswith(("create table", "create index", "alter table", "set search_path")):
            return FakeCursor([])
        for prefix, fn in self._rules:
            if low.startswith(prefix):
                return fn(low, p, self)
        raise AssertionError(f"FakeDB 未实现的 SQL：{sql[:160]}")


# -- 内置分派处理器工厂 --------------------------------------------
def _make_select(low_tbl: str, name: str):
    def fn(low: str, p: tuple, db: FakeDB) -> FakeCursor:
        t = db.tables.get(name)
        rows = [dict(r) for r in (t.rows if t else [])]
        # 简易 WHERE col=%s 等值过滤（够用即止）
        if " where " in low and rows:
            where = low.split(" where ", 1)[1]
            cols = [c.strip().split("=")[0].strip() for c in where.replace(" and ", ",").split(",")]
            kv = {c: v for c, v in zip(cols, p) if c in rows[0]}
            if kv:
                rows = [r for r in rows if all(r.get(k) == v for k, v in kv.items())]
        return FakeCursor(rows)
    return fn


def _make_insert(name: str):
    def fn(low: str, p: tuple, db: FakeDB) -> FakeCursor:
        t = db.tables[name]
        cols = low.split("(", 1)[1].split(")", 1)[0]
        colnames = [c.strip() for c in cols.split(",")]
        row = {c: (p[i] if i < len(p) else None) for i, c in enumerate(colnames)}
        t.upsert(row)
        return FakeCursor([], rowcount=1)
    return fn


def _make_update(name: str):
    def fn(low: str, p: tuple, db: FakeDB) -> FakeCursor:
        t = db.tables[name]
        if " set " not in low:
            return FakeCursor([], rowcount=0)
        setpart = low.split(" set ", 1)[1].split(" where ", 1)[0]
        setcols = [c.strip().split("=")[0].strip() for c in setpart.split(",")]
        tail = p[len(setcols):]
        row = {c: p[i] for i, c in enumerate(setcols)}
        kv = {k: tail[j] for j, k in enumerate(t.key_cols) if j < len(tail)}
        tgt = t.find(kv) if kv else None
        if tgt:
            tgt.update(row)
            return FakeCursor([], rowcount=1)
        return FakeCursor([], rowcount=0)
    return fn


def _make_delete(name: str):
    def fn(low: str, p: tuple, db: FakeDB) -> FakeCursor:
        t = db.tables[name]
        if " where " in low:
            where = low.split(" where ", 1)[1]
            cols = [c.strip().split("=")[0].strip() for c in where.replace(" and ", ",").split(",")]
            kv = {c: v for c, v in zip(cols, p)}
            return FakeCursor([], rowcount=t.delete(kv))
        n = len(t.rows)
        t.rows.clear()
        return FakeCursor([], rowcount=n)
    return fn


def install_stub(module, db: FakeDB, attr: str = "_db"):
    """把被测模块的 `attr()`（默认 `_db`）替换为返回该桩的工厂，返回还原函数。"""
    original = getattr(module, attr)
    setattr(module, attr, lambda: db)

    def restore():
        setattr(module, attr, original)
    return restore


def _self_test() -> int:
    fails = []

    def check(name, cond):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    db = FakeDB().add_table("plugin_configs", SimpleTable(key_cols=["plugin_name", "key"],
                        seed=[{"plugin_name": "chatbot", "key": "enabled", "value": "0"}]))

    def run_sql(sql, params=None):
        with db as conn:
            return conn.execute(sql, params).fetchall()

    rows = run_sql("SELECT value FROM plugin_configs WHERE plugin_name=%s AND key=%s",
                   ("chatbot", "enabled"))
    check("SELECT 命中种子行", rows and rows[0]["value"] == "0")

    with db as conn:
        conn.execute("INSERT INTO plugin_configs (plugin_name,key,value) VALUES (%s,%s,%s)",
                     ("chatbot", "model", "qwen-turbo"))
        conn.commit()
    check("INSERT 后行数=2", len(db.tables["plugin_configs"].rows) == 2)
    check("commit 计数=1", db.commits == 1)

    with db as conn:
        conn.execute("UPDATE plugin_configs SET value=%s WHERE plugin_name=%s AND key=%s",
                     ("qwen-max", "chatbot", "model"))
    got = run_sql("SELECT value FROM plugin_configs WHERE key=%s", ("model",))
    check("UPDATE 生效", got and got[0]["value"] == "qwen-max")

    n = run_sql("SELECT * FROM plugin_configs")
    check("全表 SELECT", len(n) == 2)

    # 异常时 __exit__ 触发 rollback
    try:
        with db as conn:
            conn.execute("DROP TABLE x")  # 未实现 → 抛错
    except AssertionError:
        pass
    check("异常退出触发 rollback", db.rollbacks >= 1)

    print("—— offline_db_stub 自检测试结束 ——" if not fails else f"失败：{fails}")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="offline_db_stub",
                                 description="离线 DB 替身桩（harness）；作为库导入使用，CLI 仅自检。")
    ap.add_argument("--self-test", action="store_true", help="运行内置自检测试")
    resolved = sys.argv[1:] if argv is None else list(argv)
    args = ap.parse_args(resolved)
    if not resolved or args.self_test:
        return _self_test()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
