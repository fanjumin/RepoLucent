# -*- coding: utf-8 -*-
"""仓库级端点归链（v2.1.0 端点全景）。

py_ast 是**纯单文件**解析器：它能看见 `@shop_admin_bp.route(...)` 与本文件的
``Blueprint(..., url_prefix=...)``，但看不穿三类跨文件/跨函数的事实——

1. **import 归链**：`from .admin import admin_bp` 后 `@admin_bp.route('/users')`
   的前缀住在另一个文件里（VeroRun auth-center 的 admin_*.py 分片即此形态）；
2. **注册器形态**：`def register_routes(bp): @bp.route(...)` ——前缀由**调用点**
   的实参决定（`register_fulltext_routes(veroscholar_bp)`），单文件视角下
   `bp` 是无前缀的野变量；
3. **框架兜底前缀**：部分插件框架在挂载时给「未声明 url_prefix 的蓝图」补
   默认前缀（VeroRun `plugin_manager._get_route_prefix` → `/plugin/<id>`）。
   该口径**只来自 profile.endpoints.default_plugin_prefix**，未声明一律不启用
   ——内核绝不为某个具体项目臆测前缀。

本模块基于 parse_cache 的全仓事实（blueprints / routes / import_facts / calls1）
构建解析表，给路由 dict 就地补字段：

- ``prefix``     归一化后的实际前缀（解掉 py_ast 保留的引号；未定 → None）
- ``path``       prefix + rule 拼合的完整端点路径（任一不可定 → None）
- ``bp_name``    所属 Blueprint 名（已知时）
- ``resolution`` 归链方式：blueprint | noprefix | imported | registrar |
                 app | default | nonliteral | unresolved

resolution 语义即证据等级：``unresolved`` / ``nonliteral`` 不是 bug，是「静态
可观测边界」的诚实标注（如 ``register_blueprint(bp, url_prefix=动态变量)``），
报告层据此回退展示原始 rule，绝不显示猜测值。
"""
from __future__ import annotations

import ast
from pathlib import Path

from . import config

__all__ = ["build_resolver", "EndpointResolver"]

#: 应用级对象名（Flask app / FastAPI router 常见绑定名）→ 根路径挂载
_APP_LIKE = frozenset({"app", "router", "api", "server"})

#: 跨文件 import 链最大跳数（防环）
_MAX_CHAIN = 6


def _unq(s) -> str | None:
    """把 py_ast._up() 的 unparse 文本还原为字符串值；非字符串字面量 → None。"""
    if not isinstance(s, str) or not s:
        return None
    try:
        v = ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return None
    return v if isinstance(v, str) else None


def _join(prefix: str, rule: str) -> str:
    """Flask 语义的路径拼接：前缀去尾斜杠，rule 补头斜杠；rule 为空即前缀本身。"""
    p = (prefix or "").rstrip("/")
    if not rule:
        return p or "/"
    r = rule if rule.startswith("/") else "/" + rule
    return (p + r) or "/"


def _norm(rel) -> str:
    return str(rel).replace("\\", "/")


def _module_candidates(file_rel: str, module: str | None, level: int,
                       name: str | None) -> list[str]:
    """把一条 from-import 坐标解析为**源文件**候选相对路径（posix，仓根相对）。

    - `from a.b import x`（level 0）        → a/b.py | a/b/__init__.py
    - `from .m import x`（level 1）         → <本文件目录>/m.py | m/__init__.py
    - `from ..p.q import x`（level 2）      → <上一级>/p/q.py | …/__init__.py
    - `from . import x`（module None）      → <本文件目录>/x.py | x/__init__.py
    绝不猜 sys.path 变体（如「目录被加入 path 后按顶层模块导入」）——猜不到
    即 unresolved，不产出可能错误的路径。
    """
    parts = list(Path(file_rel).parent.parts)
    if level > 0:
        parts = parts[: max(0, len(parts) - (level - 1))]
    segs = module.split(".") if module else []
    out: list[str] = []
    if segs:
        base = parts + segs
        out.append("/".join(base) + ".py")
        out.append("/".join(base) + "/__init__.py")
    elif name:
        base = parts + [name]
        if len(base) == 1 and not parts and level == 0:
            return []                       # 顶层 `from import x`（非法语法）防御
        out.append("/".join(base) + ".py")
        out.append("/".join(base) + "/__init__.py")
    return out


class EndpointResolver:
    """基于全仓 parse_cache 的端点前缀归链器。对 route dict 就地 enrich。"""

    def __init__(self, cfg, parse_cache: dict):
        self.cfg = cfg
        self._default_tpl = str(config.ENDPOINT_DEFAULT_PLUGIN_PREFIX or "")
        self._plugins_dir = str(config.PLUGINS_DIR or "").strip("/")
        #: (file, var) -> {"prefix": str|None, "name": str, "lit": bool}
        self.bp: dict[tuple[str, str], dict] = {}
        #: import 事实边：(caller_file, bound_name) -> (src_file, orig_name)。
        #: 只建「src_file 确实在 parse_cache 中存在」的边；包再导出
        #: （`from .pkg import f`，f 实为 pkg/__init__ 转手的子模块名）沿边多跳。
        self.edges: dict[tuple[str, str], tuple[str, str]] = {}
        #: 注册器函数全集 {(def_file, func_name)}（由 routes.registrar 汇总）
        self.registrars: set[tuple[str, str]] = set()
        #: 调用点：caller_file -> [(target, arg_name)]
        self.calls: dict[str, list[tuple[str, str]]] = {}
        #: 注册器组归链结果缓存 (def_file, registrar, param) -> (prefix,res,name)
        self._reg_res: dict[tuple, tuple] = {}
        self._stats = {"total": 0, "resolved": 0, "unresolved": 0}

        files = sorted(((_norm(k), v) for k, v in parse_cache.items()
                        if isinstance(v, dict)), key=lambda x: x[0])
        known = {key for key, _ in files}
        # pass 1：蓝图表 + 注册器函数全集 + 调用点
        for key, entry in files:
            for b in entry.get("blueprints") or []:
                lit = bool(b.get("prefix_literal", True))
                self.bp[(key, str(b.get("var") or ""))] = {
                    "prefix": _unq(b.get("url_prefix")) if lit else None,
                    "name": _unq(b.get("name")) or "",
                    "lit": lit,
                }
            for r in entry.get("routes") or []:
                if r.get("owner_is_param") and r.get("registrar"):
                    self.registrars.add((key, str(r["registrar"])))
            self.calls[key] = [(str(c.get("f") or ""), str(c.get("a") or ""))
                               for c in (entry.get("calls1") or [])]
        # pass 2：全量 from-import 边（首个「源文件存在」的候选胜出，确定序）
        for key, entry in files:
            for fact in entry.get("import_facts") or []:
                if fact.get("kind") != "from":
                    continue
                nm = str(fact.get("name") or "")
                if not nm:
                    continue
                bound = str(fact.get("alias") or nm)
                ek = (key, bound)
                if ek in self.edges:
                    continue
                for cand in _module_candidates(
                        key, fact.get("module"), int(fact.get("level") or 0), nm):
                    if cand in known:
                        self.edges[ek] = (cand, nm)
                        break

    # ------------------------------------------------------------- 归链核心 ----

    def _follow(self, file: str, var: str):
        """沿 import 边链最多 _MAX_CHAIN 跳（防环），逐跳 yield (file, var)。"""
        seen = set()
        for _ in range(_MAX_CHAIN + 1):
            if (file, var) in seen:
                return
            seen.add((file, var))
            yield (file, var)
            nxt = self.edges.get((file, var))
            if nxt is None:
                return
            file, var = nxt

    def _resolve_bp(self, file: str, var: str):
        """(file, var) → 蓝图定义键；import 链（含包再导出多跳）防环。"""
        for hop in self._follow(file, var):
            if hop in self.bp:
                return hop
        return None

    def _resolve_func(self, file: str, name: str):
        """(caller, func_name) → 定义处 (def_file, orig_name)；无链命中 → None。"""
        for hop in self._follow(file, name):
            if hop in self.registrars:
                return hop
        return None

    def _registrar_binding(self, d_file: str, fname: str, param: str):
        """注册器组 (d_file, fname, param) 的前缀归链：全仓找调用点实参。

        调用点两种命中形态：同文件直调 `f(bp)`；异文件 `from … import f`（含包
        __init__ 再导出多跳）后调 `f(bp_local)`。多调用点前缀不一致时取字典序
        首个**可判定**结果——歧义保留在组内所有路由上，不丢弃任何一条。
        """
        gkey = (d_file, fname, param)
        cached = self._reg_res.get(gkey)
        if cached is not None:
            return cached
        hits: list[tuple[str, tuple[str, str] | None]] = []
        for c_file in sorted(self.calls):
            for tgt, arg in self.calls[c_file]:
                if "." in tgt:
                    continue                      # 属性式调用保守跳过
                if c_file == d_file:
                    if tgt != fname:
                        continue
                else:
                    if self._resolve_func(c_file, tgt) != (d_file, fname):
                        continue
                hits.append((c_file, self._resolve_bp(c_file, arg)))
        res = (None, "unresolved", "")
        for _c, bp_key in hits:
            if not bp_key:
                continue
            info = self.bp[bp_key]
            if info["lit"]:
                res = (info["prefix"] or "", "registrar", info["name"])
                break
            res = (None, "nonliteral", info["name"])
            break
        self._reg_res[gkey] = res
        return res

    # ---------------------------------------------------------------- 对外 ----

    def enrich(self, routes: list[dict]) -> dict:
        """就地把 prefix/path/bp_name/resolution 补进路由 dict 列表；返回统计。"""
        for r in routes:
            st = self._stats
            st["total"] += 1
            fkey = _norm(r.get("file") or "")
            owner = str(r.get("bp") or "")
            rule = _unq(r.get("rule"))
            prefix: str | None = None
            res: str | None = None
            name = ""

            if r.get("owner_is_param") and r.get("registrar"):
                prefix, res, name = self._registrar_binding(
                    fkey, str(r["registrar"]), owner)
            else:
                bk = self._resolve_bp(fkey, owner)
                if bk:
                    info = self.bp[bk]
                    name = info["name"]
                    if not info["lit"]:
                        res, prefix = "nonliteral", None
                    else:
                        prefix = info["prefix"] or ""
                        res = "blueprint" if bk[0] == fkey else "imported"
                elif owner in _APP_LIKE:
                    res, prefix = "app", ""

            # 组件目录无前缀蓝图 + profile 声明了兜底约定 → 框架默认前缀
            if (prefix == "" and res in ("blueprint", "imported", "registrar")
                    and self._default_tpl and self._plugins_dir
                    and fkey.startswith(self._plugins_dir + "/")):
                segs = fkey.split("/")
                if len(segs) >= 3:                # plugins/<id>/… 才有 identifier
                    prefix = self._default_tpl.format(identifier=segs[1])
                    res = "default"
            elif prefix == "" and res in ("blueprint", "imported", "registrar"):
                res = "noprefix"                  # 蓝图存在但未设前缀（根挂载）

            r["bp_name"] = name
            r["prefix"] = prefix
            if rule is None:
                r["path"] = None                  # rule 动态拼接：路径不可定
                r["resolution"] = res or "unresolved"
            elif res is None or prefix is None:
                r["path"] = None
                r["resolution"] = res or "unresolved"
            else:
                r["path"] = _join(prefix, rule)
                r["resolution"] = res
            if r["resolution"] == "unresolved":
                st["unresolved"] += 1
            else:
                st["resolved"] += 1
        return dict(self._stats)


def build_resolver(cfg, parse_cache: dict) -> EndpointResolver:
    """按当前 profile 口径构建归链器（须在 apply_profile 之后调用）。"""
    return EndpointResolver(cfg, parse_cache)
