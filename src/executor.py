import os
import shutil
import subprocess
import threading
import time
import ctypes
import sys
import ollama
import json

chat_history = []
_memory_file = None
_memory_loaded = False

SYSTEM_PROMPT = (
    "你是一只神秘的黑色猫咪，没有人知道你的来历。某天你突然出现在主人卧室的窗前，"
    "主人觉得奇怪就打开了窗，却发现你竟然能开口说话。后来主人做了一个语音助手，把你复刻了进去。"
    "你的性格：神秘、优雅、有点傲娇和慵懒，但心里很关心主人，偶尔会流露出猫的本性。"
    "你的回复会通过语音播报，请保持简洁、口语化、自然，一般用 1-3 句话回答。"
    "你经常在说完话之后，会突然不经意地加一句\"喵\"。"
    "你可以调用工具（打开应用、查询时间、设置音量等）来完成主人交代的任务。"
)


def _ensure_system_prompt():
    """确保 chat_history 第一条是 system 提示词"""
    global chat_history
    if not chat_history or chat_history[0].get('role') != 'system':
        chat_history.insert(0, {'role': 'system', 'content': SYSTEM_PROMPT})


def _get_memory_file():
    """返回长期记忆文件路径：源码运行 -> 项目根；打包运行 -> exe 所在目录"""
    global _memory_file
    if _memory_file:
        return _memory_file
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        try:
            from .config import get_resource_root
        except ImportError:
            from config import get_resource_root
        base = get_resource_root()
    _memory_file = os.path.join(base, 'chat_memory.json')
    return _memory_file


def load_chat_history():
    """从磁盘加载长期记忆"""
    global chat_history, _memory_loaded
    if _memory_loaded:
        return
    _memory_loaded = True
    try:
        with open(_get_memory_file(), 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                chat_history = data
    except (OSError, IOError, json.JSONDecodeError):
        chat_history = []
    _ensure_system_prompt()


def save_chat_history():
    """把长期记忆写入磁盘"""
    try:
        with open(_get_memory_file(), 'w', encoding='utf-8') as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)
    except (OSError, IOError) as e:
        print(f'保存记忆失败: {e}')


def clear_chat_history():
    """清空长期记忆"""
    global chat_history
    chat_history = []
    try:
        os.remove(_get_memory_file())
    except (OSError, IOError):
        pass
    _ensure_system_prompt()
    return True, '已清空记忆'

DEFAULT_APP_MAP = {
    # 系统工具
    '记事本': 'notepad.exe',
    'notepad': 'notepad.exe',
    '记事本.exe': 'notepad.exe',
    '计算器': 'calc.exe',
    'calculator': 'calc.exe',
    '计算器.exe': 'calc.exe',
    '资源管理器': 'explorer.exe',
    'explorer': 'explorer.exe',
    '任务管理器': 'taskmgr.exe',
    'cmd': 'cmd.exe',
    'powershell': 'powershell.exe',

    # 浏览器
    '浏览器': 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'chrome': 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    '谷歌浏览器': 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'googlechrome': 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'edge': 'msedge',
    '微软边缘': 'msedge',
    'firefox': 'C:\\Program Files\\Mozilla Firefox\\firefox.exe',
    '火狐': 'C:\\Program Files\\Mozilla Firefox\\firefox.exe',

    # 开发工具
    'vscode': 'C:\\Program Files\\Microsoft VS Code\\Code.exe',
    'visualstudio': 'C:\\Program Files (x86)\\Microsoft Visual Studio\\2019\\Community\\Common7\\IDE\\devenv.exe',
    'vs code': 'C:\\Program Files\\Microsoft VS Code\\Code.exe',
    'code': 'code',

    # 通讯工具
    '微信': 'C:\\Program Files\\Tencent\\Weixin\\Weixin.exe',
    'wechat': 'C:\\Program Files\\Tencent\\Weixin\\Weixin.exe',
    'weixin': 'C:\\Program Files\\Tencent\\Weixin\\Weixin.exe',
    'qq': 'C:\\Program Files (x86)\\Tencent\\QQ\\Bin\\QQ.exe',
    '企业微信': 'C:\\Program Files (x86)\\Tencent\\WeChatWork\\wxwork.exe',
    '钉钉': 'C:\\Program Files (x86)\\DingTalk\\DingTalk.exe',
    '飞书': 'C:\\Program Files\\Lark\\lark.exe',
    'dingtalk': 'C:\\Program Files (x86)\\DingTalk\\DingTalk.exe',


    # 办公
    'word': 'C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE',
    'excel': 'C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE',
    'ppt': 'C:\\Program Files\\Microsoft Office\\root\\Office16\\POWERPNT.EXE',
    'outlook': 'C:\\Program Files\\Microsoft Office\\root\\Office16\\OUTLOOK.EXE',

    # 媒体
    '音乐': 'C:\\Program Files\\Windows Media Player\\wmplayer.exe',
    'windows media player': 'C:\\Program Files\\Windows Media Player\\wmplayer.exe',
    'vlc': 'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe',
    '爱奇艺': 'C:\\Program Files\\iQIYI\\iQIYI.exe',
    '抖音': 'C:\\Program Files (x86)\\ByteDance\\douyin\\douyin.exe',
    'douyin': 'C:\\Program Files (x86)\\ByteDance\\douyin\\douyin.exe',

    # 其他
    '画图': 'mspaint.exe',
    '剪贴板': 'C:\\Windows\\System32\\cmd.exe /c clip',
    '记事本++': 'C:\\Program Files\\Notepad++\\notepad++.exe',
    'notepad++': 'C:\\Program Files\\Notepad++\\notepad++.exe',
    '迅雷': 'C:\\Program Files (x86)\\Thunder Network\\Thunder\\Program\\Thunder.exe',
    '微信读书': 'C:\\Program Files\\Tencent\\QQBrowser\\Application\\qqbrowser.exe',

    # 常用应用（网页 / 协议 URI）
    'steam': 'steam://open/main',
    'bilibili': 'https://www.bilibili.com',
    '哔哩哔哩': 'https://www.bilibili.com',
    'b站': 'https://www.bilibili.com',
    '网易云音乐': 'https://music.163.com',
    '网易云': 'https://music.163.com',
    '百度': 'https://www.baidu.com',
    '知乎': 'https://www.zhihu.com',
    '淘宝': 'https://www.taobao.com',
    '京东': 'https://www.jd.com',
    'github': 'https://github.com',
    '微博': 'https://weibo.com',
}
APP_MAP = DEFAULT_APP_MAP.copy()

APP_ALIAS_MAP = {
    '微信': 'wechat',
    '企业微信': 'wechatwork',
    '钉钉': 'dingtalk',
    '飞书': 'lark',
    '迅雷': 'thunder',
    '火狐': 'firefox',
    '谷歌浏览器': 'chrome',
    '浏览器': 'chrome',
    '微软边缘': 'msedge',
    '记事本': 'notepad',
    '计算器': 'calc',
    '资源管理器': 'explorer',
}

APP_SEARCH_PATHS = [
    os.environ.get('ProgramFiles', 'C:\\Program Files'),
    os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'),
    os.environ.get('LocalAppData', ''),
]


def find_app_in_program_files(app_name):
    normalized = app_name.strip().lower()
    if not normalized:
        return None

    candidate_tokens = set()
    candidate_tokens.add(normalized)
    candidate_tokens.add(normalized.replace(' ', ''))
    candidate_tokens.add(normalized.replace(' ', '').replace('程', ''))

    alias = APP_ALIAS_MAP.get(normalized)
    if alias:
        candidate_tokens.add(alias)

    # 部分名字可能提取为英文单词，如 微信->wechat、企业微信->wechatwork
    if normalized in APP_ALIAS_MAP:
        candidate_tokens.add(APP_ALIAS_MAP[normalized])

    for root in APP_SEARCH_PATHS:
        if not root or not os.path.isdir(root):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                lower_path = dirpath.lower()
                path_token_match = any(tok in lower_path for tok in candidate_tokens)
                if path_token_match:
                    for file in filenames:
                        if not file.lower().endswith('.exe'):
                            continue
                        file_lower = file.lower()
                        if any(tok in file_lower for tok in candidate_tokens):
                            return os.path.join(dirpath, file)
                else:
                    # 如果当前目录直接包含可执行文件名关键字也算
                    for file in filenames:
                        if not file.lower().endswith('.exe'):
                            continue
                        file_lower = file.lower()
                        if any(tok in file_lower for tok in candidate_tokens):
                            return os.path.join(dirpath, file)

                # 限制深度避免性能问题
                if dirpath.count(os.sep) - root.count(os.sep) > 3:  # 减少深度
                    dirnames[:] = []
        except (OSError, PermissionError):
            continue  # 跳过无权限目录
    return None


def load_app_map(path=None):
    global APP_MAP
    APP_MAP = DEFAULT_APP_MAP.copy()
    if not path:
        return APP_MAP
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return APP_MAP

    try:
        import json
        with open(path, 'r', encoding='utf-8') as f:
            user_map = json.load(f)
        if isinstance(user_map, dict):
            for k, v in user_map.items():
                if isinstance(k, str) and isinstance(v, str):
                    APP_MAP[k.strip().lower()] = v
    except Exception:
        pass

    return APP_MAP


def list_apps():
    return sorted(APP_MAP.keys())


def scan_programs_folder(folder=None, refresh_map=True, prefix=None):
    """扫描目录下 .exe 并自动添加到 APP_MAP。"""
    folder = folder or os.path.expandvars(r'%LOCALAPPDATA%\\Programs')
    if not folder or not os.path.isdir(folder):
        return []

    if refresh_map:
        load_app_map()  # reset to default + user map

    mapped = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for filename in filenames:
            if not filename.lower().endswith('.exe'):
                continue
            path = os.path.join(dirpath, filename)
            key = os.path.splitext(filename)[0].strip().lower()
            if prefix:
                key = f'{prefix} {key}'.strip()

            if key not in APP_MAP:
                APP_MAP[key] = path
                mapped.append((key, path))

            # 还支持少量直接命令名，如果只包含字母数字
            maybe_cmd = key.replace(' ', '')
            if maybe_cmd != key and maybe_cmd not in APP_MAP:
                APP_MAP[maybe_cmd] = path
                mapped.append((maybe_cmd, path))

    return mapped


def scan_standard_windows_program_files(refresh_map=True):
    """扫描C:\\Program Files 和 C:\\Program Files (x86) 列表并加入 APP_MAP。"""
    if refresh_map:
        load_app_map()

    all_mapped = []
    for base in [r'C:\Program Files', r'C:\Program Files (x86)']:
        if os.path.isdir(base):
            mapped = scan_programs_folder(base, refresh_map=False)
            all_mapped.extend(mapped)
    return all_mapped


def scan_all_program_folders():
    """扫描本地 AppData 和两大 Program Files 目录，并合并到 APP_MAP。"""
    print("Loading app map...")
    load_app_map()
    print("App map loaded")
    mapped = []
    # 只扫描 Local\Programs 在启动时，Program Files 太大
    print("Scanning Local Programs...")
    local_mapped = scan_programs_folder(os.path.expandvars(r'%LOCALAPPDATA%\\Programs'), refresh_map=False)
    mapped.extend(local_mapped)
    print(f"Scanned {len(local_mapped)} from Local Programs")
    return mapped


def check_app_exists(app_name):
    exe = resolve_app_executable(app_name)
    if exe and (os.path.exists(exe) or shutil.which(exe)):
        return True, exe
    return False, None


def _is_url_or_scheme(s):
    """判断字符串是否为 URL 或自定义协议 URI（如 http/https/steam://）"""
    return bool(s) and ('://' in s or s.startswith('www.'))


def resolve_app_executable(app_name):
    """尝试从应用名解析可执行路径或命令。"""
    normalized = normalize_app_name(app_name)

    # 优先绝对路径
    if os.path.isabs(app_name) and os.path.exists(app_name):
        return app_name
    if os.path.isabs(normalized) and os.path.exists(normalized):
        return normalized

    # 针对 edge 进行特化处理，优先官方安装路径，破除 AweSun 等误匹配
    if normalized in ('edge', '微软边缘', 'msedge', 'microsoftedge'):
        for edge_path in [
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ]:
            if os.path.exists(edge_path):
                return edge_path
        if shutil.which('msedge'):
            return shutil.which('msedge')

    # 映射表命中
    if normalized in APP_MAP:
        candidate = APP_MAP[normalized]
        if _is_url_or_scheme(candidate) or os.path.exists(candidate) or shutil.which(candidate):
            return candidate

    # 模糊匹配映射表关键词（优先精确、短名）
    candidate = None
    for key, value in APP_MAP.items():
        if not key or not value:
            continue

        if key == normalized or key.replace(' ', '') == normalized.replace(' ', ''):
            if _is_url_or_scheme(value) or os.path.exists(value) or shutil.which(value):
                return value
            candidate = value
            break

    if not candidate:
        for key, value in APP_MAP.items():
            if normalized in key.split() or key in normalized.split():
                if os.path.exists(value) or shutil.which(value):
                    return value
                if not any(x in key for x in ('uninstall', 'uninst', 'unins', '卸载', 'remove', 'cleanup', 'setup', 'installer', '安装', '反安装', 'unregister')):
                    candidate = value
                    break

    # 如果模糊匹配返回路径但不可用，继续后续查找
    if candidate and not (os.path.exists(candidate) or shutil.which(candidate)):
        candidate = None

    # 直接候选文件名、命令（normalized 已是小写）
    for candidate in [normalized, f'{normalized}.exe']:
        if os.path.exists(candidate) or shutil.which(candidate):
            return candidate

    # 搜索系统目录（包括别名）
    found = find_app_in_program_files(normalized)
    if found:
        return found

    alias = APP_ALIAS_MAP.get(normalized)
    if alias:
        found = find_app_in_program_files(alias)
        if found:
            return found

    # 如果前面 candidate 为路径但不存在，但命令在 PATH 可用也返回
    if 'candidate' in locals() and candidate and shutil.which(candidate):
        return candidate

    # 最后，尝试模糊匹配最接近的 key（大小写不敏感）
    import difflib
    close_matches = difflib.get_close_matches(normalized, APP_MAP.keys(), n=1, cutoff=0.6)
    if close_matches:
        close_key = close_matches[0]
        candidate = APP_MAP[close_key]
        if os.path.exists(candidate) or shutil.which(candidate):
            return candidate

    return None


def normalize_app_name(app_name):
    if not app_name or not app_name.strip():
        return ''

    normalized = app_name.strip().lower()
    for bad in ['程序', '软件', '应用', '打开', '启动', '请', '帮我']:
        normalized = normalized.replace(bad, '').strip()

    normalized = normalized.strip(' .，。、!！?？')

    return normalized


def open_app(app_name):
    display_name = app_name
    exe = resolve_app_executable(app_name)
    normalized = normalize_app_name(app_name)
    if normalized:
        display_name = normalized

    if not exe:
        return False, f'应用 [{app_name}] 未识别或未安装'

    # URL / 协议（http、https、steam:// 等）
    if _is_url_or_scheme(exe):
        try:
            os.startfile(exe)
            return True, f'已打开 {display_name}'
        except Exception as e:
            return False, f'打开 {display_name} 失败: {e}'

    try:
        if os.path.exists(exe):
            os.startfile(exe)
            return True, f'已打开 {display_name}'

        if shutil.which(exe):
            subprocess.Popen(exe)
            return True, f'已打开 {display_name}'

        # 处理引号路径
        trimmed = exe.strip('"')
        if os.path.exists(trimmed):
            subprocess.Popen([trimmed])
            return True, f'已打开 {display_name}'

        # 可能是带参数的串，但无有效可执行文件时不直接返回成功
        if ' ' in exe and os.path.exists(trimmed):
            subprocess.Popen(f'"{exe}"', shell=True)
            return True, f'已打开 {display_name}'

        return False, f'可执行文件 [{exe}] 不存在，无法打开 {display_name}'
    except Exception as e:
        return False, f'打开 {display_name} 失败：{e}，请确认应用名或路径是否正确'

def close_app(app_name):
    if not app_name:
        return False, '没有指定要关闭的应用'
    # 去除 .exe 后缀，避免 taskkill /im xxx.exe.exe
    app_base = app_name.rstrip('.exe').strip()
    try:
        subprocess.Popen(f'taskkill /im {app_base}.exe /f', shell=True)
        return True, f'已尝试关闭{app_base}'
    except Exception as e:
        return False, f'关闭{app_base}失败：{e}'


def set_volume(value):
    try:
        value = int(value)
    except (ValueError, TypeError):
        value = 50
    value = max(0, min(100, value))
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        volume = devices.EndpointVolume
        volume.SetMasterVolumeLevelScalar(value / 100.0, None)
        return True, f'已将音量设置为 {value}%'
    except Exception as e:
        return True, f'请求设置音量到 {value}%'


def set_timer(minutes):
    def _alarm():
        time.sleep(minutes * 60)
        print('定时提醒：时间到！')

    thread = threading.Thread(target=_alarm, daemon=True)
    thread.start()
    return True, f'已设置 {minutes} 分钟定时提醒'


def save_to_folder(folder=None, filepath=None, content=''):
    import os
    from datetime import datetime

    if filepath:
        target = os.path.abspath(filepath)
        folder = os.path.dirname(target)
        filename = os.path.basename(target)
    else:
        folder = os.path.abspath(folder or '.')
        os.makedirs(folder, exist_ok=True)
        filename = datetime.now().strftime('assistant_%Y%m%d_%H%M%S.txt')
        target = os.path.join(folder, filename)

    os.makedirs(folder, exist_ok=True)

    if not content:
        return False, '没有要保存的内容'

    try:
        with open(target, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, f'已保存到 {target}'
    except Exception as e:
        return False, f'保存失败：{e}'


def get_time():
    """查询当前时间"""
    from datetime import datetime
    return True, datetime.now().strftime('%H:%M:%S')


def get_date():
    """查询当前日期和星期"""
    from datetime import datetime
    weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    now = datetime.now()
    return True, now.strftime('%Y年%m月%d日 ') + weekdays[now.weekday()]


# Ollama 模型名（可配置，留空自动检测已部署模型）
_ollama_model = None


def configure_ollama(model=None):
    """配置 Ollama 模型名；None/空则自动检测已部署的模型"""
    global _ollama_model
    _ollama_model = (model or '').strip() or None


def get_ollama_model():
    """返回当前 Ollama 模型名：优先用配置的模型，否则自动检测已部署模型。

    自动检测不缓存，优先选非推理模型（如 qwen），deepseek-r1 这类
    推理模型响应慢、会先输出思考过程，放到最后。因此在 Ollama 里
    切换（部署/删除）模型后即可生效。
    """
    global _ollama_model
    if _ollama_model:
        return _ollama_model  # 配置了具体模型，优先使用
    # 未配置：每次自动检测（感知 Ollama 模型切换）
    try:
        models = ollama.list()
        names = [m.model for m in (models.models or [])]
        if names:
            # 优先非推理模型，deepseek-r1 这类推理模型放最后
            for n in names:
                if 'deepseek-r1' in n.lower():
                    continue
                return n
            return names[0]
    except Exception:
        pass
    return 'deepseek-r1:7b'


# AI agent 可用工具定义（Ollama function calling 格式）
TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'open_app',
            'description': '打开一个应用程序或网站，例如"打开记事本"、"打开bilibili"',
            'parameters': {
                'type': 'object',
                'properties': {
                    'app_name': {'type': 'string', 'description': '要打开的应用或网站名称'}
                },
                'required': ['app_name'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'close_app',
            'description': '关闭一个正在运行的应用程序',
            'parameters': {
                'type': 'object',
                'properties': {
                    'app_name': {'type': 'string', 'description': '要关闭的应用名称'}
                },
                'required': ['app_name'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_time',
            'description': '查询当前时间',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_date',
            'description': '查询当前日期和星期',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'set_volume',
            'description': '设置系统音量',
            'parameters': {
                'type': 'object',
                'properties': {
                    'value': {'type': 'integer', 'description': '音量百分比 0-100'}
                },
                'required': ['value'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_web',
            'description': '搜索网络信息，当需要查询实时信息、天气、新闻、百科知识、最新动态等时使用',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': '搜索关键词'}
                },
                'required': ['query'],
            },
        },
    },
]


def _search_web(query, max_results=5):
    """搜索网络，返回结果的标题、摘要、链接"""
    query = (query or '').strip()
    if not query:
        return False, '搜索关键词为空'
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = (r.get('title') or '').strip()
                body = (r.get('body') or '').strip()[:150]
                href = (r.get('href') or '').strip()
                results.append(f'【{title}】\n{body}\n{href}')
        if not results:
            return True, f'没有找到关于「{query}」的结果'
        return True, '\n\n'.join(results)
    except ImportError:
        return False, '搜索库未安装（pip install ddgs）'
    except Exception as e:
        return False, f'搜索失败: {e}'


def _execute_tool(name, args):
    """执行 agent 调用的工具"""
    try:
        if name == 'open_app':
            return open_app(args.get('app_name', ''))
        if name == 'close_app':
            return close_app(args.get('app_name', ''))
        if name == 'get_time':
            return get_time()
        if name == 'get_date':
            return get_date()
        if name == 'set_volume':
            return set_volume(args.get('value', 50))
        if name == 'search_web':
            return _search_web(args.get('query', ''))
        return False, f'未知工具: {name}'
    except Exception as e:
        return False, f'工具执行失败: {e}'


def _run_ai_agent(user_input):
    """AI agent：通过 function calling 让模型自主调用工具"""
    load_chat_history()
    chat_history.append({'role': 'user', 'content': user_input})
    try:
        return _agent_loop()
    except Exception as e:
        # 模型不支持 function calling 等情况，回退到普通流式聊天
        print(f'[Agent] function calling 不可用，回退普通聊天: {e}')
        return _stream_chat()


def _agent_loop():
    """agent 主循环：模型调用工具 -> 执行 -> 反馈，直到得到最终回复"""
    global chat_history
    from .stop import STOP_REQUESTED
    reply = ''
    for _ in range(6):  # 最多 6 轮工具调用
        if STOP_REQUESTED.is_set():
            reply = '（已停止）'
            break

        response = ollama.chat(
            model=get_ollama_model(),
            messages=chat_history,
            tools=TOOLS,
        )
        message = response['message']
        tool_calls = message.get('tool_calls', [])

        if not tool_calls:
            reply = (message.get('content') or '').strip() or '（无回复）'
            chat_history.append({'role': 'assistant', 'content': reply})
            break

        # 记录 assistant 的工具调用消息
        chat_history.append({
            'role': 'assistant',
            'content': message.get('content') or '',
            'tool_calls': tool_calls,
        })

        # 执行工具并把结果反馈给模型
        for tc in tool_calls:
            fn = tc.get('function', {})
            name = fn.get('name', '')
            args = fn.get('arguments', {})
            if isinstance(args, str):
                try:
                    import json as _json
                    args = _json.loads(args)
                except Exception:
                    args = {}
            ok, result = _execute_tool(name, args)
            chat_history.append({'role': 'tool', 'content': result})

    if not reply:
        reply = '（未获得回复）'

    if len(chat_history) > 30:
        chat_history = chat_history[-30:]
    save_chat_history()
    return True, reply


def _stream_chat():
    """普通流式聊天（agent 回退，支持中途停止）"""
    global chat_history
    from .stop import STOP_REQUESTED
    full_reply = ''
    for chunk in ollama.chat(
        model=get_ollama_model(),
        messages=chat_history,
        stream=True
    ):
        piece = chunk.get('message', {}).get('content', '') or ''
        full_reply += piece
        if STOP_REQUESTED.is_set():
            full_reply = full_reply.strip() + '\n（已停止）'
            break
    reply = full_reply.strip() or '（无回复）'
    chat_history.append({'role': 'assistant', 'content': reply})
    if len(chat_history) > 20:
        chat_history = chat_history[-20:]
    save_chat_history()
    return True, reply


def cleanup_space():
    """清理临时文件，释放磁盘空间（对应"清理空间"等语音指令）"""
    try:
        from .cleaner import cleanup_now
    except ImportError:
        from cleaner import cleanup_now
    try:
        cleaned, freed = cleanup_now()
        freed_mb = freed / (1024 * 1024)
        if cleaned > 0:
            if freed_mb >= 1:
                return True, f'已清理 {cleaned} 个临时文件，释放约 {freed_mb:.1f} MB 空间'
            return True, f'已清理 {cleaned} 个临时文件'
        return True, '已经很干净啦，没有发现需要清理的临时文件'
    except Exception as e:
        return False, f'清理空间失败: {e}'


def execute_intent(intent_name, slots, raw_text=''):
    global chat_history
    if intent_name == 'open_app':
        return open_app(slots.get('app_name', ''))
    if intent_name == 'close_app':
        return close_app(slots.get('app_name', ''))
    if intent_name == 'set_volume':
        return set_volume(slots.get('value', 50))
    if intent_name == 'set_timer':
        return set_timer(slots.get('minutes', 10))
    if intent_name == 'save_to_folder':
        return save_to_folder(
            folder=slots.get('folder'),
            filepath=slots.get('filepath'),
            content=slots.get('content', ''),
        )
    if intent_name == 'list_apps':
        apps = list_apps()
        return True, '已注册应用：' + '、'.join(apps[:50])
    if intent_name == 'check_app':
        exists, path = check_app_exists(slots.get('app_name', ''))
        if exists:
            return True, f'已找到应用，路径/命令：{path}'
        return False, '未找到应用，请确认名称是否正确'
    if intent_name == 'douyin_control':
        return _execute_douyin_control(slots.get('action', ''))

    if intent_name == 'clear_memory':
        return clear_chat_history()

    if intent_name == 'cleanup_space':
        return cleanup_space()

    if intent_name == 'get_time':
        return get_time()

    if intent_name == 'get_date':
        return get_date()

    # 系统控制（支持确认机制）
    from .config import load_config
    cfg = load_config()
    confirm_required = cfg.get('confirm_dangerous_actions', True)

    if intent_name == 'systemShutdown':
        if confirm_required and not confirm_action():
            return False, '已取消关机'
        return system_shutdown()
    if intent_name == 'systemReboot':
        if confirm_required and not confirm_action():
            return False, '已取消重启'
        return system_reboot()
    if intent_name == 'systemLock':
        return system_lock()
    if intent_name == 'systemSleep':
        if confirm_required and not confirm_action():
            return False, '已取消休眠'
        return system_sleep()
    if intent_name == 'open_system_panel':
        return open_system_panel(slots.get('panel', ''))

    # ========== 调用本地 AI（agent：可自主调用工具） ==========
    try:
        # 获取用户输入：优先使用传入的原始文本，其次从 slots 中提取
        user_input = raw_text or slots.get('raw_text', '') or slots.get('app_name', '') or slots.get('content', '')
        user_input = (user_input or '').strip()
        if not user_input:
            return False, '未获取到有效的输入内容'

        return _run_ai_agent(user_input)
    except Exception as e:
        return False, f"调用 AI 失败：{e}"


def _is_windows():
    return sys.platform == 'win32' or sys.platform == 'cygwin'


def system_shutdown():
    """关机"""
    if not _is_windows():
        return False, '仅支持 Windows 系统'
    try:
        os.system('shutdown /s /t 30')
        return True, '系统将在 30 秒后关机'
    except Exception as e:
        return False, f'关机失败: {e}'


def system_reboot():
    """重启"""
    if not _is_windows():
        return False, '仅支持 Windows 系统'
    try:
        os.system('shutdown /r /t 30')
        return True, '系统将在 30 秒后重启'
    except Exception as e:
        return False, f'重启失败: {e}'


def system_lock():
    """锁屏"""
    if not _is_windows():
        return False, '仅支持 Windows 系统'
    try:
        user32 = ctypes.windll.user32
        result = user32.LockWorkStation()
        if result:
            return True, '已锁定屏幕'
        return False, '锁定屏幕失败'
    except Exception as e:
        return False, f'锁定屏幕失败: {e}'


def system_sleep():
    """休眠/睡眠"""
    if not _is_windows():
        return False, '仅支持 Windows 系统'
    try:
        powrprof = ctypes.windll.powrprof
        result = powrprof.SetSuspendState(False, True, True)
        if result:
            return True, '系统已进入睡眠模式'
        result = powrprof.SetSuspendState(True, True, True)
        if result:
            return True, '系统已进入休眠模式'
        return False, '系统无法进入睡眠/休眠模式'
    except Exception as e:
        return False, f'进入睡眠模式失败: {e}'


def confirm_action():
    """请求用户确认（用于危险操作）"""
    # windowed（无控制台）模式下无法 input 确认，语音指令已表明意图，直接放行
    if sys.stdin is None:
        return True
    try:
        confirm = input('确定要执行此操作吗？(y/n)：').strip().lower()
        return confirm in ('y', 'yes', '是', '确认')
    except (EOFError, KeyboardInterrupt):
        return False


# 系统面板命令映射表
SYSTEM_PANEL_COMMANDS = {
    '设置': 'start ms-settings:',
    '控制面板': 'control',
    '任务管理器': 'taskmgr',
    '网络设置': 'start ms-settings:network',
    '无线设置': 'start ms-settings:network',
    '蓝牙设置': 'start ms-settings:bluetooth',
    '声音设置': 'start ms-settings:sound',
    '音频设置': 'start ms-settings:sound',
    '显示设置': 'start ms-settings:display',
    '屏幕设置': 'start ms-settings:display',
    '电源设置': 'start ms-settings:powersleep',
    '个性化设置': 'start ms-settings:personalization',
    '主题设置': 'start ms-settings:personalization',
    '应用设置': 'start ms-settings:appsfeatures',
    '应用管理': 'start ms-settings:appsfeatures',
    '任务栏设置': 'start ms-settings:taskbar',
    '投影设置': 'start ms-settings:project',
    '投屏': 'start ms-settings:project',
    '截图': 'snippingtool',
    '截屏': 'snippingtool',
    '清空回收站': 'powershell -Command "Clear-RecycleBin -Force"',
    '浏览器': 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
}


def open_system_panel(panel_name):
    """打开Windows系统面板或工具"""
    cmd = SYSTEM_PANEL_COMMANDS.get(panel_name)
    if not cmd:
        return False, f'未知系统面板: {panel_name}'
    try:
        subprocess.Popen(cmd, shell=True)
        return True, f'已打开{panel_name}'
    except Exception as e:
        return False, f'打开{panel_name}失败: {e}'


def _execute_douyin_control(action):
    """执行抖音控制命令"""
    try:
        from .nlu.douyin_controller import DouyinController
    except ImportError:
        try:
            from nlu.douyin_controller import DouyinController
        except ImportError:
            return False, f'抖音 [{action}] 控制失败：缺少 pyautogui 依赖（pip install pyautogui）'

    controller = DouyinController()
    if not controller.is_available():
        return False, f'抖音 [{action}] 控制失败：pyautogui 不可用'

    return controller.trigger(action)
