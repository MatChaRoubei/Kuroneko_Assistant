import importlib.util
import os


class PluginManager:
    def __init__(self, plugin_path):
        self.plugin_path = plugin_path
        self.plugins = []
        self.load_plugins()

    def load_plugins(self):
        if not os.path.isdir(self.plugin_path):
            return

        for entry in os.listdir(self.plugin_path):
            if entry.endswith('.py') and not entry.startswith('_'):
                path = os.path.join(self.plugin_path, entry)
                spec = importlib.util.spec_from_file_location(entry[:-3], path)
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                    if hasattr(module, 'execute') and hasattr(module, 'intent_name'):
                        self.plugins.append(module)
                except Exception as e:
                    print(f'加载插件{entry}失败: {e}')

    def try_execute(self, intent_name, slots):
        for plugin in self.plugins:
            if plugin.intent_name == intent_name:
                try:
                    return True, plugin.execute(slots)
                except Exception as e:
                    return False, f'插件执行失败: {e}'
        return False, None
