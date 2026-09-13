# -*- coding: utf-8 -*-
"""Provider 适配器层：新增模型厂商只需在 providers/ 下加一个模块并实现 Provider.chat()。"""
from __future__ import annotations

from .base import ChatResult, LLMError, Provider, ToolCall

__all__ = ["Provider", "ChatResult", "ToolCall", "LLMError"]
