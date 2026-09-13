# -*- coding: utf-8 -*-
"""query 的 SQLite 加速层（v1.8.0 阶段三 3-B）。

定位：**加速层，不是事实源**。事实源始终是 ``repo_lens.json`` 与符号索引产物；
本层缺失 / 损坏 / 过期（仓库文件签名变化）**一律回退**到「全量分析 + 内存查询」
原路径。加速层可牺牲，事实源不可损。

真实收益：``query`` 原先必须先跑完整分析（大仓库冷跑 40s 量级）才能查询；有了
index.db 之后，只要库存在且新鲜，``query`` 可取行后直接返回 → 毫秒级。

表结构（方案 §3-B 的四表 + 一张必要的行存储补充）::

    plugins(identifier, version, agent_role, routes, payload)
    routes(rule, methods, plugin, file, lineno)
    symbols(name, kind, file, line, owner)
    files(relpath, loc, lang, plugin)
    core_modules(module, payload)     # core scope 的行存储（四表外的必要补充）
    meta(key, value)                  # fingerprint / tool_version / built_at / tables

``payload`` 列保存**原始行 dict 的 JSON**，这是结果一致性的关键：``load_rows`` 取回
的就是写库时的行对象，再交给 ``query.run_query(rows=...)`` 走**同一段**投影/过滤代码，
故「加速路径」与「全量分析路径」的结果必然逐字段一致，无需维护两套实现。

落点：稳定根 ``.insight_cache/index.db``（与 AST 缓存、热点缓存同根，跨日归档不失效）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

#: 库文件名（落在 cfg.cache_dir）
DB_NAME = "index.db"

#: scope → 行存储表。未列出的 scope 不支持加速（回退原路径）。
_SCOPE_TABLE: dict[str, str] = {"plugins": "plugins", "core": "core_modules"}

_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS plugins(
    identifier TEXT PRIMARY KEY, version TEXT, agent_role TEXT,
    routes INTEGER, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS routes(
    rule TEXT, methods TEXT, plugin TEXT, file TEXT, lineno INTEGER);
CREATE TABLE IF NOT EXISTS symbols(
    name TEXT, kind TEXT, file TEXT, line INTEGER, owner TEXT);
CREATE TABLE IF NOT EXISTS files(
    relpath TEXT PRIMARY KEY, loc INTEGER, lang TEXT, plugin TEXT);
CREATE TABLE IF NOT EXISTS core_modules(module TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_plugins_role ON plugins(agent_role);
CREATE INDEX IF NOT EXISTS idx_routes_rule ON routes(rule);
CREATE INDEX IF NOT EXISTS idx_routes_plugin ON routes(plugin);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_files_plugin ON files(plugin);
"""

#: 扩展名 → 语言标签（仅用于 files.lang，未知回落扩展名本身）
_LANGS = {".py": "python", ".js": "javascript", ".ts": "typescript",
          ".vue": "vue", ".md": "markdown", ".json": "json",
          ".sh": "shell", ".bat": "batch", ".yml": "yaml", ".yaml": "yaml",
          ".html": "html", ".css": "css", ".sql": "sql"}


def db_path(cfg) -> Path:
    return Path(cfg.cache_dir) / DB_NAME


def _dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)


def _lang_of(rel: str) -> str:
    ext = Path(rel).suffix.lower()
    return _LANGS.get(ext, ext.lstrip(".") or "unknown")


# ------------------------------------------------------------------ 新鲜度 ----

def repo_fingerprint(cfg) -> str:
    """仓库文件签名集合的 sha1：``(relpath, mtime_ns, size)`` 排序后哈希。

    纯 stat，不读内容 —— 765 文件的仓库约 0.2s，远低于一次全量分析的开销。
    任一文件增删/改名/触碰都会改变指纹，从而让库判定为过期并回退原路径。
    """
    from .fs_scan import iter_repo_files
    items: list[tuple[str, int, int]] = []
    for rel, fpath in iter_repo_files(cfg):
        try:
            st = fpath.stat()
        except OSError:
            continue
        items.append((rel.as_posix(), st.st_mtime_ns, st.st_size))
    items.sort()
    h = hashlib.sha1()
    for rel, mt, sz in items:
        h.update(f"{rel}|{mt}|{sz}\n".encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------------ 建库 ----

def connect(cfg, must_exist: bool = True) -> sqlite3.Connection | None:
    """打开库；不存在（must_exist）或损坏 → None。

    注意：损坏分支**必须显式关闭连接**。``sqlite3.connect`` 是惰性握手——即便文件
    内容不是库，连接也会成功建立并持有 OS 句柄；随后的探测语句才抛错。若此处直接
    ``return None`` 而不关闭，句柄会残留在进程中，Windows 下将锁住 ``index.db``，
    使后续 ``build`` 的 ``os.replace`` 原子替换恒抛 ``WinError 5``（自愈永久失效）。
    """
    p = db_path(cfg)
    if must_exist and not p.is_file():
        return None
    conn = None
    try:
        conn = sqlite3.connect(str(p))
        conn.execute("SELECT 1 FROM meta LIMIT 1")       # 触发损坏/缺表检测
        return conn
    except sqlite3.Error:
        if conn is not None:                             # 关掉这个已建立但不可用的连接
            try:
                conn.close()
            except sqlite3.Error:
                pass
        return None


def _symbols_index(cfg, data: dict) -> dict:
    """取符号索引：主 JSON 的 index_file 指针 → out_dir 约定路径 → 空。"""
    from .symbol_index import index_from_file
    ptr = (data.get("symbols") or {}).get("index_file")
    cands = [Path(ptr)] if ptr else []
    cands += [Path(cfg.out_dir) / "repo_lens_symbols.json",
              Path(cfg.cache_dir) / "repo_lens_symbols.json"]
    for c in cands:
        try:
            if c.is_file():
                return index_from_file(c)
        except OSError:
            continue
    return {}


def build(cfg, data: dict, symbols: dict | None = None) -> Path | None:
    """从分析结果派生建库（原子替换）。失败返回 None，绝不抛出、绝不半写。

    ``symbols`` 可显式传入符号索引（调用方通常握有内存中的 parse_cache，比读落盘
    产物更新更准）；缺省时按 data 的 index_file 指针 / out_dir 约定路径回落。
    """
    p = db_path(cfg)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            tmp.unlink()
        conn = sqlite3.connect(str(tmp))
        try:
            conn.executescript(_DDL)
            _fill(conn, cfg, data, symbols)
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                         ("fingerprint", repo_fingerprint(cfg)))
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                         ("built_at", str(int(time.time()))))
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                         ("tables", ",".join(("plugins", "routes", "symbols",
                                              "files", "core_modules"))))
            conn.commit()
        finally:
            conn.close()
        tmp.replace(p)                      # 原子替换：避免读到半写库
        return p
    except (sqlite3.Error, OSError, ValueError):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return None


def _fill(conn: sqlite3.Connection, cfg, data: dict,
          symbols: dict | None = None) -> None:
    plugins = ((data.get("plugins") or {}).get("items") or [])
    file_plugin: dict[str, str] = {}
    for p in plugins:
        if not isinstance(p, dict):
            continue
        ident = str(p.get("identifier") or "")
        if not ident:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO plugins(identifier, version, agent_role,"
            " routes, payload) VALUES(?,?,?,?,?)",
            (ident, p.get("version"), p.get("agent_role"),
             len(p.get("routes") or []), _dumps(p)))
        for rt in (p.get("routes") or []):
            if isinstance(rt, dict):
                conn.execute(
                    "INSERT INTO routes(rule, methods, plugin, file, lineno)"
                    " VALUES(?,?,?,?,?)",
                    (rt.get("rule"), _dumps(rt.get("methods") or []), ident,
                     rt.get("file") or p.get("dir"), rt.get("lineno")))

    for m in ((data.get("core") or {}).get("modules") or []):
        # core 模块行的主键字段是 name（非 module）
        if isinstance(m, dict) and m.get("name"):
            conn.execute("INSERT OR REPLACE INTO core_modules(module, payload)"
                         " VALUES(?,?)", (str(m["name"]), _dumps(m)))

    # symbols / files：优先用调用方传入的内存索引，其次回落落盘产物
    idx = symbols if symbols is not None else _symbols_index(cfg, data)
    for name, entries in (idx.get("symbols") or {}).items():
        for e in (entries if isinstance(entries, list) else [entries]):
            if not isinstance(e, dict):
                continue
            rel = e.get("file")
            owner = e.get("owner")
            conn.execute("INSERT INTO symbols(name, kind, file, line, owner)"
                         " VALUES(?,?,?,?,?)",
                         (name, e.get("kind"), rel, e.get("line"), owner))
            if rel and rel not in file_plugin:
                file_plugin[rel] = owner or ""
    for rel, owner in file_plugin.items():
        conn.execute("INSERT OR REPLACE INTO files(relpath, loc, lang, plugin)"
                     " VALUES(?,?,?,?)", (rel, None, _lang_of(rel), owner))


# ------------------------------------------------------------------ 取数 ----

def load_rows(cfg, scope: str) -> list[dict] | None:
    """从库取指定 scope 的行；库不可用 / 过期 / scope 不支持 → None（回退原路径）。"""
    table = _SCOPE_TABLE.get(scope)
    if table is None:
        return None
    conn = connect(cfg)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
        if not row or row[0] != repo_fingerprint(cfg):
            return None                                  # 过期：回退全量分析
        return [json.loads(r[0]) for r in
                conn.execute(f"SELECT payload FROM {table}")]     # noqa: S608
    except (sqlite3.Error, ValueError, OSError):
        return None
    finally:
        conn.close()


def stats(cfg) -> dict:
    """库概况（供 doctor / 自检使用）；不可用时返回 ``{"available": False}``。"""
    conn = connect(cfg)
    if conn is None:
        return {"available": False, "path": str(db_path(cfg))}
    out: dict = {"available": True, "path": str(db_path(cfg))}
    try:
        for t in ("plugins", "routes", "symbols", "files", "core_modules"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
        fp = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
        out["fingerprint"] = fp[0] if fp else None
        out["fresh"] = bool(fp and fp[0] == repo_fingerprint(cfg))
    except (sqlite3.Error, OSError):
        out["available"] = False
    finally:
        conn.close()
    return out
