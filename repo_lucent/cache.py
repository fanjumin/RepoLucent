# -*- coding: utf-8 -*-
"""AST 解析结果缓存：内容寻址（content-addressed）+ 签名预筛，跨运行复用解析结果。

设计边界（重要）：
- 只缓存【AST 解析】这一最昂贵的步骤，**不缓存任何分析结论**。
  分析结论每次都基于 parse_cache 重新计算，因此缓存永远不影响输出正确性。
- 缓存目录位于 out/.insight_cache/，随 dev_insight 目录被排除在代码统计之外。
- 任何异常（缓存损坏、结构变更、磁盘不可写）都静默回源，绝不中断主流程。

v1.7.0（阶段二 2-B）：缓存键由「(mtime_ns, size) 单一口径」升级为**双层判定**：

  1. 签名未变 → 直接命中，**零哈希成本**（热跑路径不引入任何额外 IO）；
  2. 签名变了（git 操作 / 拷贝 / touch 后 mtime 普遍失真）→ 再比内容哈希，
     内容相同仍判命中。

哈希取 SHA-1 前 12 位十六进制（48 bit）。仓库规模下碰撞概率可忽略；即便碰撞，
后果是**按 miss 处理**（重新解析），正确性无损，只损失一点性能。
配套 `.insight_cache/filehash.json` 持久化 `relpath → hash` 台账，使
「本轮重解析文件数」等增量指标无需二次读盘即可给出。
"""
from __future__ import annotations

import hashlib
import json
from collections import namedtuple
from pathlib import Path

from .fs_scan import MAX_TEXT_BYTES

CACHE_DIRNAME = ".insight_cache"
CACHE_FILE = "ast_cache.json"
HASH_FILE = "filehash.json"

#: 哈希截断长度（十六进制字符数）。
HASH_LEN = 12

#: 缓存结构版本号。改动 entry 存储格式时必须递增，使旧缓存整体失效。
#: v1.6.0 → 2：entry 内 classes/functions/routes 新增 lineno 字段，
#:              旧缓存条目缺该字段会让符号索引（symbol_index）丢失行号。
#: v1.7.0 → 3：缓存记录新增 `hash` 字段并引入内容寻址判定，
#:              旧的「仅签名」记录无法参与哈希比对，故整体失效重建。
CACHE_VERSION = 3


#: 单文件缓存判定结果。
#:   state  —— "hit"（可直接复用 entry）| "miss"（需重新解析）
#:   entry  —— 命中时的 AST 解析事实；未命中为 None
#:   digest —— 本次算出的内容哈希；未算（签名直接命中 / stat 失败）为 None
#:   reason —— 判定原因，供测试断言与增量指标使用
CacheHit = namedtuple("CacheHit", "state entry digest reason")


def cache_key(content: bytes) -> str:
    """内容寻址键：SHA-1 前 12 位十六进制。

    以**内容**而非元数据标识文件，使 git checkout / 拷贝 / touch 造成的
    mtime 失真不再引发无谓的重解析。
    """
    return hashlib.sha1(content).hexdigest()[:HASH_LEN]


def signature(fpath: Path) -> str:
    """文件签名：修改时间(纳秒) + 字节数。作为**零成本预筛**使用。"""
    st = fpath.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def cache_file(out_dir: Path) -> Path:
    return out_dir / CACHE_DIRNAME / CACHE_FILE


def hash_file(out_dir: Path) -> Path:
    return out_dir / CACHE_DIRNAME / HASH_FILE


def resolve(old_rec: dict | None, fpath: Path,
            prev_hash: str | None = None, sig: str | None = None) -> CacheHit:
    """判定单文件是否命中缓存（阶段二 2-B 的核心判定，纯函数、可单测）。

    判定顺序：
      1. 取当前签名；与记录签名一致 → 命中（`reason="sig"`，**零哈希成本**）；
      2. 签名变了 → 读内容算哈希；与记录哈希（或台账哈希）一致 → 命中
         （`reason="hash"`，覆盖 git checkout / 拷贝 / touch 场景）；
      3. 否则 → 未命中（`reason="changed"` / `"new"`）。

    `sig` 可由调用方预先算好传入（批量扫描时避免同一文件被 stat 两次）；
    为 None 时本函数自行取签名。`prev_hash` 来自 filehash.json 台账。

    `reason="entry_missing"` 表示台账里有哈希但缓存里没有可复用的 entry
    （缓存被清理或损坏），此时仍按 miss 处理——加速层可牺牲，事实源不可损。

    返回的 `digest` 供调用方写入 filehash.json 台账。
    """
    if sig is None:
        try:
            sig = signature(fpath)
        except OSError:
            return CacheHit("miss", None, None, "stat_error")

    if old_rec and old_rec.get("entry") is not None and old_rec.get("sig") == sig:
        return CacheHit("hit", old_rec["entry"], old_rec.get("hash"), "sig")

    # 签名不可信（git checkout / 拷贝 / touch 后 mtime 失真）：上内容哈希再判。
    # 与解析共用同一截断上限，保证「哈希的内容 == 被解析的内容」。
    try:
        digest = cache_key(fpath.read_bytes()[:MAX_TEXT_BYTES])
    except OSError:
        return CacheHit("miss", None, None, "stat_error")

    if old_rec and old_rec.get("entry") is not None:
        known = old_rec.get("hash") or prev_hash
        if known and known == digest:
            return CacheHit("hit", old_rec["entry"], digest, "hash")
        return CacheHit("miss", None, digest, "changed")
    if prev_hash and prev_hash == digest:
        return CacheHit("miss", None, digest, "entry_missing")
    return CacheHit("miss", None, digest, "new")


def load(out_dir: Path) -> dict:
    """读取缓存。返回 {relpath: {"sig": str, "hash": str|None, "entry": dict}}。"""
    p = cache_file(out_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}                      # 缓存损坏：静默回源
    if not isinstance(data, dict) or data.get("cache_version") != CACHE_VERSION:
        return {}                      # 结构变更：整体失效
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def load_hashes(out_dir: Path) -> dict:
    """读取哈希台账 {relpath: hash}。损坏/缺失一律返回空表。"""
    p = hash_file(out_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("cache_version") != CACHE_VERSION:
        return {}
    hashes = data.get("hashes")
    return hashes if isinstance(hashes, dict) else {}


def save(out_dir: Path, entries: dict, hashes: dict | None = None) -> None:
    """写入缓存（及可选的哈希台账）。任何写失败都不影响主流程。"""
    try:
        p = cache_file(out_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"cache_version": CACHE_VERSION, "entries": entries},
                                ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    if hashes is None:
        return
    try:
        hash_file(out_dir).write_text(
            json.dumps({"cache_version": CACHE_VERSION, "hashes": hashes},
                       ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
