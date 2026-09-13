#!/usr/bin/env python3
"""Plugin System - BasePlugin abstract class.
All plugins must inherit from BasePlugin.
"""
from abc import ABC, abstractmethod

class BasePlugin(ABC):
    name: str = ''
    def __init__(self):
        self.manager = None
    @abstractmethod
    def setup(self):
        'Called once before activate.'
    @abstractmethod
    def activate(self):
        'Enable plugin features.'
    def deactivate(self):
        'Tear down.'
    def t(self, text, locale=None):
        'Translate text via plugin i18n.'
        return text
