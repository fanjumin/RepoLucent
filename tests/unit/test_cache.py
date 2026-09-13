# -*- coding: utf-8 -*-
"""`cache` 模块边界用例（阶段二 2-C 单元层 / 2-B 语义固化）。

重点固化 2-B 的两条语义：
  1. **签名预筛优先**——签名一致即命中，全程零哈希、零读盘；
  2. **内容兜底**——签名失真（git checkout / 拷贝 / touch）时用内容哈希救回命中。
并覆盖台账读写、损坏回源、截断口径等边界。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from repo_lens.cache import (CACHE_VERSION, HASH_LEN, cache_key, hash_file,
                             load, load_hashes, resolve, save, signature)
from repo_lens.fs_scan import MAX_TEXT_BYTES


def _mk(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _rec(entry, content: bytes, fpath: Path) -> dict:
    return {"sig": signature(fpath), "hash": cache_key(content), "entry": entry}


# ------------------------------------------------------------ cache_key ----

def test_cache_key_length_and_charset():
    k = cache_key(b"hello")
    assert len(k) == HASH_LEN == 12
    assert all(c in "0123456789abcdef" for c in k)


def test_cache_key_deterministic():
    assert cache_key(b"abc") == cache_key(b"abc")


def test_cache_key_differs_on_content():
    assert cache_key(b"abc") != cache_key(b"abd")


def test_cache_key_is_pure_over_full_content():
    """`cache_key` 自身不做截断——截断责任在调用方（见下一条用例）。"""
    assert cache_key(b"x" * 10) != cache_key(b"x" * 11)


def test_resolve_digest_uses_truncated_content(tmp_path):
    """`resolve` 只哈希前 MAX_TEXT_BYTES 字节——与解析的截断口径严格一致。

    这是刻意行为：哈希必须标识「实际被解析的那段内容」，否则截断点之后的
    差异会造成无意义的缓存失效（解析结果明明相同，却每次都重解析）。
    """
    big = b"x" * (MAX_TEXT_BYTES + 500)
    p = _mk(tmp_path, "big.py", big)
    assert resolve(None, p).digest == cache_key(big[:MAX_TEXT_BYTES])


# ------------------------------------------------------------- resolve ----

def test_resolve_no_record_is_miss_new(tmp_path):
    p = _mk(tmp_path, "a.py", b"x = 1\n")
    v = resolve(None, p)
    assert v.state == "miss" and v.reason == "new" and v.entry is None
    assert v.digest == cache_key(b"x = 1\n")


def test_resolve_sig_match_hits_without_reading_disk(tmp_path):
    """签名一致 → 命中，且**不读盘**（删掉文件仍能命中，证明确实零 IO）。"""
    p = _mk(tmp_path, "b.py", b"x = 1\n")
    rec = _rec({"path": "b.py"}, b"x = 1\n", p)
    sig = rec["sig"]
    p.unlink()
    v = resolve(rec, p, sig=sig)
    assert v.state == "hit" and v.reason == "sig" and v.entry == {"path": "b.py"}
    assert v.digest == rec["hash"]          # 沿用记录里的哈希，未重算


def test_resolve_sig_changed_but_content_same_hits(tmp_path):
    """核心场景：内容未变、mtime 变了 → 哈希救回命中（2-B 的主要收益）。"""
    p = _mk(tmp_path, "c.py", b"x = 1\n")
    rec = _rec({"path": "c.py"}, b"x = 1\n", p)
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert signature(p) != rec["sig"]       # 确认签名确实失真
    v = resolve(rec, p)
    assert v.state == "hit" and v.reason == "hash" and v.entry == {"path": "c.py"}
    assert v.digest == rec["hash"]


def test_resolve_sig_changed_and_content_changed_misses(tmp_path):
    p = _mk(tmp_path, "d.py", b"x = 1\n")
    rec = _rec({"path": "d.py"}, b"x = 1\n", p)
    p.write_bytes(b"x = 2\n")
    # 新旧内容**字节数相同**（各 6B），签名 `mtime_ns:size` 里只有 mtime 能区分二者，
    # 因此必须像上一条用例那样**显式前移 mtime**，不能依赖写入本身必定改变时间戳：
    # 文件系统时间戳粒度偏粗时两次写入会落在同一刻度，签名不变 → 判定退化为
    # hit/sig，用例随机失败（实测同一套件三次运行分别出现 6/5/1 个失败）。
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert signature(p) != rec["sig"]       # 确认签名确实失真
    v = resolve(rec, p)
    assert v.state == "miss" and v.reason == "changed" and v.entry is None
    assert v.digest == cache_key(b"x = 2\n")


def test_resolve_record_without_entry_uses_ledger(tmp_path):
    """台账有哈希但缓存无 entry（缓存被清）→ 仍按 miss，原因可区分。"""
    p = _mk(tmp_path, "e.py", b"x = 1\n")
    h = cache_key(b"x = 1\n")
    v = resolve({}, p, prev_hash=h, sig="0:1")
    assert v.state == "miss" and v.reason == "entry_missing"


def test_resolve_record_hash_takes_precedence_over_ledger(tmp_path):
    """记录自带哈希与台账冲突时以记录为准（它才是与 entry 配对的那份）。"""
    p = _mk(tmp_path, "f.py", b"x = 1\n")
    digest = cache_key(b"x = 1\n")
    rec = {"sig": "0:1", "hash": "deadbeefcafe", "entry": {"path": "f.py"}}
    v = resolve(rec, p, prev_hash=digest)
    assert v.state == "miss" and v.reason == "changed"


def test_resolve_stat_error(tmp_path):
    v = resolve(None, tmp_path / "gone.py")
    assert v.state == "miss" and v.reason == "stat_error" and v.digest is None


def test_resolve_null_entry_treated_as_miss(tmp_path):
    """记录存在但 entry 为 null（写入中断）→ 不得当作命中复用。"""
    p = _mk(tmp_path, "g.py", b"x = 1\n")
    rec = {"sig": signature(p), "hash": cache_key(b"x = 1\n"), "entry": None}
    v = resolve(rec, p)
    assert v.state == "miss"


# ---------------------------------------------------------- 台账读写 ----

def test_save_load_roundtrip(tmp_path):
    save(tmp_path, {"a.py": {"sig": "1:2", "hash": "abc", "entry": {"x": 1}}},
         {"a.py": "abc"})
    entries = load(tmp_path)
    assert entries["a.py"]["entry"] == {"x": 1} and entries["a.py"]["hash"] == "abc"
    assert load_hashes(tmp_path) == {"a.py": "abc"}


def test_save_without_hashes_skips_ledger(tmp_path):
    save(tmp_path, {"a.py": {"sig": "1:2", "hash": None, "entry": {}}})
    assert load(tmp_path) != {}
    assert not hash_file(tmp_path).exists()
    assert load_hashes(tmp_path) == {}


def test_load_missing_files(tmp_path):
    assert load(tmp_path / "nope") == {}
    assert load_hashes(tmp_path / "nope") == {}


def test_load_corrupted_json_falls_back(tmp_path):
    d = tmp_path / ".insight_cache"
    d.mkdir(parents=True)
    (d / "ast_cache.json").write_text("{not json", encoding="utf-8")
    (d / "filehash.json").write_text("{not json", encoding="utf-8")
    assert load(tmp_path) == {} and load_hashes(tmp_path) == {}


def test_load_wrong_cache_version_invalidates(tmp_path):
    """结构版本不符 → 整体失效（保证旧结构不会被误读为命中）。"""
    d = tmp_path / ".insight_cache"
    d.mkdir(parents=True)
    (d / "ast_cache.json").write_text(json.dumps(
        {"cache_version": CACHE_VERSION - 1, "entries": {"a.py": {}}}), encoding="utf-8")
    assert load(tmp_path) == {}


def test_load_non_dict_entries(tmp_path):
    d = tmp_path / ".insight_cache"
    d.mkdir(parents=True)
    (d / "ast_cache.json").write_text(json.dumps(
        {"cache_version": CACHE_VERSION, "entries": []}), encoding="utf-8")
    assert load(tmp_path) == {}


def test_cache_version_is_three():
    """2-B 引入内容寻址，记录结构新增 hash 字段 → CACHE_VERSION 必须为 3。"""
    assert CACHE_VERSION == 3


def test_end_to_end_touch_then_hit(tmp_path):
    """端到端：写缓存 → touch → 仍命中；改内容 → 未命中。"""
    p = _mk(tmp_path, "z.py", b"x = 1\n")
    rec = _rec({"path": "z.py"}, b"x = 1\n", p)
    save(tmp_path, {"z.py": rec}, {"z.py": rec["hash"]})

    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    entries, ledger = load(tmp_path), load_hashes(tmp_path)
    assert resolve(entries["z.py"], p, prev_hash=ledger["z.py"]).state == "hit"

    p.write_bytes(b"y = 9\n")
    assert resolve(entries["z.py"], p, prev_hash=ledger["z.py"]).state == "miss"
