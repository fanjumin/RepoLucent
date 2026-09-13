# -*- coding: utf-8 -*-
"""LLM 出站通道（P2 需求2，可选叠加）：本工具自身挂一个 LLM 客户端。

默认关闭：不配置 models[] 或不开 llm_enabled 时，一切照旧（评审认可的既有边界
「工具不接 LLM / 编排—判定分离」保持为默认）。启用方式二选一：
- settings.json 配 models[] + llm_enabled（密钥仍只走环境变量）
- CLI：`repolucent.py audit --with-llm`

公开 API（延迟导出，避免导入期触发重依赖）：
    run_semantic_audit(cfg, data, dims, ...) -> SemanticRun
"""
from __future__ import annotations


def __getattr__(name: str):
    if name in ("run_semantic_audit", "SemanticRun", "resolve_model",
                "build_provider", "extract_json", "llm_settings"):
        from . import runner
        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["run_semantic_audit", "SemanticRun", "resolve_model",
           "build_provider", "extract_json", "llm_settings"]
