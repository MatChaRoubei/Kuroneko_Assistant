import os
import json
import sys

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


def get_resource_root():
    """返回资源根目录（项目根），兼容 PyInstaller 打包与源码运行"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包运行：资源被解压到 _MEIPASS 临时目录
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    # 源码运行：config.py 位于 src/ 下，项目根为上一级目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_relative(path):
    """将相对路径解析为基于资源根目录的绝对路径"""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(get_resource_root(), path))


def _resolve_log_file(path):
    """日志文件写入可写目录：源码运行 -> 项目根；打包运行 -> exe 所在目录"""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = get_resource_root()
    return os.path.normpath(os.path.join(base, path))


DEFAULT_CONFIG = {
    'wake_words': ['你好小猪'],
    'language': 'zh-CN',
    'tts_engine': 'pyttsx3',
    'intents_path': _resolve_relative('data/intents.json'),
    'plugin_path': _resolve_relative('plugins'),
    'log_file': os.path.join(get_resource_root(), 'assistant.log'),
    'app_map_path': _resolve_relative('config/app_map.json'),
}


def load_config(path=None):
    print("Loading config...")
    if not path:
        yaml_path = _resolve_relative('config/config.yaml')
        json_path = _resolve_relative('config/config.json')
        if os.path.exists(yaml_path):
            path = yaml_path
        elif os.path.exists(json_path):
            path = json_path
        else:
            print("Config file not found, using default")
            return DEFAULT_CONFIG.copy()
    path = os.path.abspath(path)
    print(f"Config path: {path}")

    if not os.path.exists(path):
        print("Config file not found, using default")
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, 'r', encoding='utf-8') as f:
            if path.endswith('.yaml') or path.endswith('.yml'):
                user_cfg = yaml.safe_load(f) or {}
            else:
                user_cfg = json.load(f) or {}
        print("Config loaded from file")
    except Exception as e:
        print(f"Config load error: {e}")
        user_cfg = {}

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(user_cfg)

    # 将相对路径统一解析为基于资源根目录的绝对路径
    for key in ('intents_path', 'plugin_path', 'app_map_path',
                'speech_model_path', 'intent_descriptions_path'):
        if key in cfg and cfg[key]:
            cfg[key] = _resolve_relative(cfg[key])
    # 日志文件单独处理（写入 exe 所在目录/项目根，而非资源临时目录）
    if 'log_file' in cfg and cfg['log_file']:
        cfg['log_file'] = _resolve_log_file(cfg['log_file'])

    print("Config merged")

    return cfg
