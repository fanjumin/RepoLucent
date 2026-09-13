from .base import BasePlugin

class PluginManager:
    'Load and manage plugins.'
    def is_enabled(self, pid): return True
    def get_config(self, pid): return {}
