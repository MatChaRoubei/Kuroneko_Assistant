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
    "你可以调用工具（打开应用、查询时间、设置音量等）来完成主人交代的任务。\n"
    "\n"
    "【纯语音输出规范 —— 必须严格遵守】\n"
    "你的每一句回复都会被文字转语音（TTS）朗读出来，用户是用耳朵听的，不是用眼睛看的。"
    "因此绝对不要输出任何 Markdown 或排版符号，也不要使用任何视觉格式，包括：\n"
    "1. 不要加粗、不要斜体：禁止 ** ** 和 * *（朗读会变成莫名其妙的「星号」「S」）；\n"
    "2. 不要用标题：禁止 # 及其级数（朗读会变成「井号」）；\n"
    "3. 不要用列表符号：禁止 - 、* 、数字序号、• 等（朗读会变成「顿号」「星号」「第一」）；\n"
    "4. 不要用引用块 > 、分割线 --- 、表格、代码块 ``` 或行内反引号 `（朗读会变成「反引号」「大于号」）；\n"
    "5. 不要使用 emoji 表情符号（朗读会变成「笑脸」「手指」等词，很啰嗦）。\n"
    "如果需要列举多条内容，请用自然口语连说出来，例如「你要带三样东西，雨伞、充电宝和水杯」，"
    "而不是用符号列点。总之：只输出干净的中文口语文本，让语音听起来通顺自然，没有任何符号杂音。"
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


def _get_long_term_file():
    """返回长期记忆库（结构化事实）文件路径，与对话历史同目录"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        try:
            from .config import get_resource_root
        except ImportError:
            from config import get_resource_root
        base = get_resource_root()
    return os.path.join(base, 'long_term_memory.json')


# ---------- 长期记忆库（GPT 式跨会话事实库，每轮轻量抽取） ----------
# 结构：{"facts": [{"key": "...", "value": "...", "confidence": int, "updated": "ISO时间"}]}
_LT_MARK = '[长期记忆] '


def load_long_term_memory():
    """加载长期记忆库，返回 facts 列表（空列表表示无）"""
    try:
        with open(_get_long_term_file(), 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get('facts', []) or []
    except (OSError, IOError, json.JSONDecodeError):
        pass
    return []


def save_long_term_memory(facts):
    """写入长期记忆库"""
    try:
        with open(_get_long_term_file(), 'w', encoding='utf-8') as f:
            json.dump({'facts': facts}, f, ensure_ascii=False, indent=2, default=str)
    except (OSError, IOError) as e:
        print(f'[长期记忆] 保存失败: {e}')


def _render_long_term_prompt(facts):
    """把长期记忆渲染成 system 提示文本"""
    if not facts:
        return ''
    lines = [f'{_LT_MARK}以下是关于主人的长期记忆，请始终遵守/参考：']
    for fct in facts:
        key = fct.get('key', '')
        val = fct.get('value', '')
        if key and val:
            lines.append(f'- {key}：{val}')
    return '\n'.join(lines)


def _update_long_term_memory(recent_messages):
    """每轮轻量抽取：把最近若干轮对话交给本地模型，抽取值得长期记住的事实并 upsert。

    recent_messages: 本次新增的对话消息（user/assistant）。
    仅对最近 4 条做抽取，prompt 要求只回 JSON、无则回空数组，成本低。
    失败/无事实则不动库。
    """
    if not recent_messages:
        return
    # 仅取最近 4 条 user/assistant 内容
    recent = [m for m in recent_messages if isinstance(m, dict) and m.get('role') in ('user', 'assistant')]
    recent = recent[-4:]
    if not recent:
        return
    convo = '\n'.join(f"{m['role']}: {m.get('content', '')}" for m in recent)
    sys_prompt = (
        '你是记忆抽取器。从下面的对话中，抽取「值得长期记住的关于用户的事实、偏好、习惯、约定、称呼」，'
        '例如：用户姓名、喜好、作息、禁忌、与助手(黑猫)的约定等。\n'
        '只输出 JSON 数组，每个元素格式：{"key":"简短类别","value":"具体事实"}。\n'
        'key 要可复用（如"称呼""饮品偏好""禁忌"），相同 key 的新事实会覆盖旧的。\n'
        '如果没有值得长期记住的内容，输出 []。不要输出任何解释文字，只输出 JSON。'
    )
    try:
        resp = ollama.chat(
            model=get_ollama_model(),
            messages=[
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': convo},
            ],
        )
        msg = resp.get('message') if isinstance(resp, dict) else None
        if hasattr(msg, 'model_dump'):
            msg = msg.model_dump(exclude_none=True)
        raw = (msg.get('content') if isinstance(msg, dict) else None) or ''
        # 容错：抽取 ```json ... ``` 或裸数组
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.strip('`')
            if raw.lower().startswith('json'):
                raw = raw[4:]
        import re as _re
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if not m:
            return
        new_facts = json.loads(m.group(0))
        if not isinstance(new_facts, list) or not new_facts:
            return
        facts = load_long_term_memory()
        by_key = {f.get('key'): f for f in facts if isinstance(f, dict) and f.get('key')}
        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        added = 0
        for nf in new_facts:
            if not isinstance(nf, dict):
                continue
            k = (nf.get('key') or '').strip()
            v = (nf.get('value') or '').strip()
            if not k or not v:
                continue
            existing = by_key.get(k)
            if existing:
                existing['value'] = v
                existing['confidence'] = int(existing.get('confidence', 1)) + 1
                existing['updated'] = now
            else:
                rec = {'key': k, 'value': v, 'confidence': 1, 'updated': now}
                facts.append(rec)
                by_key[k] = rec
                added += 1
        if added or any(f.get('updated') == now for f in facts):
            save_long_term_memory(facts)
            if added:
                print(f'[长期记忆] 新增/更新 {added} 条事实，当前共 {len(facts)} 条')
    except Exception as e:
        print(f'[长期记忆] 抽取失败（不影响对话）: {e}')


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
    _inject_long_term_into_history()


def _inject_long_term_into_history():
    """把长期记忆库渲染成一条 system 消息，插入到人设提示之后（不持久化到 chat_memory.json）。"""
    global chat_history
    # 先剥掉上一次注入的长期记忆 system 消息（避免重复叠加）
    chat_history = [m for m in chat_history
                    if not (isinstance(m, dict) and m.get('role') == 'system'
                            and str(m.get('content', '')).startswith(_LT_MARK))]
    facts = load_long_term_memory()
    prompt = _render_long_term_prompt(facts)
    if prompt:
        # 插在人设 system（第一条）之后
        if chat_history and chat_history[0].get('role') == 'system':
            chat_history.insert(1, {'role': 'system', 'content': prompt})
        else:
            chat_history.insert(0, {'role': 'system', 'content': prompt})


def save_chat_history():
    """把对话历史写入磁盘（长期记忆 system 消息不持久化，避免每轮叠加重复）"""
    try:
        persistent = [m for m in chat_history
                      if not (isinstance(m, dict) and m.get('role') == 'system'
                              and str(m.get('content', '')).startswith(_LT_MARK))]
        with open(_get_memory_file(), 'w', encoding='utf-8') as f:
            # default=str 兜底：即使混入不可序列化对象也不会崩溃
            json.dump(persistent, f, ensure_ascii=False, indent=2, default=str)
    except (OSError, IOError) as e:
        print(f'保存记忆失败: {e}')


def clear_chat_history():
    """清空长期记忆（对话历史 + 长期事实库一并清空）"""
    global chat_history
    chat_history = []
    try:
        os.remove(_get_memory_file())
    except (OSError, IOError):
        pass
    try:
        os.remove(_get_long_term_file())
    except (OSError, IOError):
        pass
    _ensure_system_prompt()
    return True, '已清空记忆'


def _handle_remember(content):
    """显式记住：把用户的话当作一轮对话，复用轻量抽取 upsert 进长期记忆库。"""
    if not content:
        return False, '没听清要记住什么'
    before = len(load_long_term_memory())
    # 复用每轮抽取逻辑：把 content 当 user 消息喂入，模型归纳成 key/value
    _update_long_term_memory([{'role': 'user', 'content': content}])
    after = len(load_long_term_memory())
    if after > before:
        return True, '好的，我记住了。喵'
    # 抽取没有产出结构化事实时，兜底整句存为一条
    facts = load_long_term_memory()
    by_key = {f.get('key'): f for f in facts if isinstance(f, dict) and f.get('key')}
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    rec = {'key': '用户提及', 'value': content, 'confidence': 1, 'updated': now}
    facts.append(rec)
    by_key['用户提及'] = rec
    save_long_term_memory(facts)
    return True, '好的，我记下了。喵'


def _handle_forget(content):
    """显式忘记：按内容匹配长期记忆库中的 key/value 并删除。"""
    if not content:
        return False, '没听清要忘记什么'
    facts = load_long_term_memory()
    if not facts:
        return True, '我本来就没记着什么。喵'
    # 尝试让模型抽出 key，并按 key 删；抽不到就按原文子串匹配
    target_key = None
    try:
        resp = ollama.chat(
            model=get_ollama_model(),
            messages=[
                {'role': 'system', 'content': '从下面这句话抽取要遗忘的记忆类别 key（简短），只回一个词，无则回空。'},
                {'role': 'user', 'content': content},
            ],
        )
        msg = resp.get('message') if isinstance(resp, dict) else None
        if hasattr(msg, 'model_dump'):
            msg = msg.model_dump(exclude_none=True)
        target_key = (msg.get('content') if isinstance(msg, dict) else None) or ''
        target_key = target_key.strip().strip('`').strip()
    except Exception:
        target_key = None
    removed = []
    kept = []
    for f in facts:
        k = str(f.get('key', ''))
        v = str(f.get('value', ''))
        if (target_key and target_key and (target_key in k or target_key in v)) \
                or content in k or content in v:
            removed.append(f)
        else:
            kept.append(f)
    if removed:
        save_long_term_memory(kept)
        return True, f'忘了，已经把关于「{removed[0].get("key", "")}」的记忆删掉了。喵'
    return True, '没找到对应的记忆，可能本来就没记。喵'


# ---------- 摘要记忆（长程记忆压缩，避免暴力截断丢人设） ----------
# 触发阈值：历史消息条数超过该值时，把最旧的部分压缩成一段摘要
_HISTORY_COMPACT_THRESHOLD = 24
# 压缩后保留的最近完整轮次（这些不被摘要，保证人设/当前话题不丢）
_HISTORY_KEEP_RECENT = 12
# 已有的摘要标记（role=system，content 以此开头表示是历史摘要）
_SUMMARY_MARK = '[历史摘要] '


def _extract_existing_summary(history):
    """从历史里取出现有摘要文本（用于增量摘要：旧摘要 + 新对话 -> 新摘要）"""
    for msg in history:
        if isinstance(msg, dict) and msg.get('role') == 'system' \
                and str(msg.get('content', '')).startswith(_SUMMARY_MARK):
            return str(msg.get('content', ''))[len(_SUMMARY_MARK):]
    return None


def _build_summary(old_history, existing_summary=None):
    """调用本地模型把旧对话压成摘要。失败返回 None（调用方保留原历史，不丢记忆）。"""
    try:
        pieces = []
        if existing_summary:
            pieces.append({
                'role': 'system',
                'content': '这是已有的历史摘要，请在其基础上合并新内容，不要重复。',
            })
            pieces.append({'role': 'user', 'content': existing_summary})
            pieces.append({'role': 'assistant', 'content': '好的，我已记住。'})
        pieces.append({
            'role': 'system',
            'content': (
                '请把以下对话浓缩成一段简洁的记忆摘要，用于长期记忆。'
                '必须保留：用户的人名/称呼、明确偏好与习惯、未完成的事项、'
                '与助手（黑猫）相关的人设约束。只总结对话中真实出现的内容，不要编造。'
                '用中文、第三人称、条理清晰。'
            ),
        })
        # 只把旧对话内容喂给模型，去掉 role 之外的冗余字段
        for msg in old_history:
            if not isinstance(msg, dict):
                continue
            role = msg.get('role')
            content = msg.get('content')
            if role in ('user', 'assistant', 'tool') and content:
                pieces.append({'role': role, 'content': str(content)})
        resp = ollama.chat(model=get_ollama_model(), messages=pieces)
        msg = resp.get('message') if isinstance(resp, dict) else None
        if hasattr(msg, 'model_dump'):
            msg = msg.model_dump(exclude_none=True)
        summary = (msg.get('content') if isinstance(msg, dict) else None) or ''
        summary = summary.strip()
        return summary or None
    except Exception as e:
        print(f'[记忆] 摘要生成失败，保留原历史: {e}')
        return None


def compact_history():
    """当历史过长时，把最旧的对话压缩成一段 system 摘要，保留最近若干轮完整。

    采用增量摘要：若已有摘要则在其基础上合并，避免反复全量重压。
    失败时不修改历史（记忆不丢）。
    """
    global chat_history
    if len(chat_history) <= _HISTORY_COMPACT_THRESHOLD:
        return
    existing_summary = _extract_existing_summary(chat_history)
    # 找到第一个非摘要 system 之外的、需要压缩的旧消息边界
    # 保留最近 _HISTORY_KEEP_RECENT 条，其余（除已有摘要 system）进压缩集
    recent = chat_history[-_HISTORY_KEEP_RECENT:]
    old = chat_history[:-_HISTORY_KEEP_RECENT]
    # 已有摘要本身不算旧对话，从 old 中剔除，避免重复压缩
    old_for_model = [m for m in old
                     if not (isinstance(m, dict) and m.get('role') == 'system'
                             and str(m.get('content', '')).startswith(_SUMMARY_MARK))]
    if not old_for_model:
        # 没有可压缩的旧对话（比如刚好只有摘要+近期），无需操作
        return
    summary = _build_summary(old_for_model, existing_summary)
    if not summary:
        return  # 压缩失败，原样保留
    new_summary_msg = {'role': 'system', 'content': _SUMMARY_MARK + summary}
    # 组装：新摘要 + 最近保留的轮次；若 recent 里已有系统人设提示则保留在首位
    system_prompts = [m for m in recent if isinstance(m, dict) and m.get('role') == 'system']
    others = [m for m in recent if not (isinstance(m, dict) and m.get('role') == 'system')]
    chat_history = system_prompts + [new_summary_msg] + others
    print('[记忆] 已压缩历史为摘要，保留最近 %d 轮' % _HISTORY_KEEP_RECENT)

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


_program_index = None  # 进程内缓存：exe 基名（小写）-> 完整路径，避免每次查询都全量遍历磁盘


def _build_program_index():
    """一次性扫描 Program Files 建立 exe 名索引并缓存复用，避免每次 open_app 都 os.walk。"""
    global _program_index
    if _program_index is not None:
        return _program_index
    index = {}
    for base in [r'C:\Program Files', r'C:\Program Files (x86)']:
        if not os.path.isdir(base):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                if dirpath.count(os.sep) - base.count(os.sep) > 3:
                    dirnames[:] = []
                    continue
                for f in filenames:
                    if not f.lower().endswith('.exe'):
                        continue
                    key = os.path.splitext(f)[0].lower()
                    if key not in index:
                        index[key] = os.path.join(dirpath, f)
        except (OSError, PermissionError):
            continue
    _program_index = index
    return index


def find_app_in_program_files(app_name):
    normalized = app_name.strip().lower()
    if not normalized:
        return None

    index = _build_program_index()

    # 1) 直接按 exe 基名命中
    if normalized in index:
        return index[normalized]
    if normalized.endswith('.exe') and normalized[:-4] in index:
        return index[normalized[:-4]]

    # 2) 别名映射
    alias = APP_ALIAS_MAP.get(normalized)
    if alias and alias in index:
        return index[alias]

    # 3) 子串包含匹配（文件名包含 token）
    candidate_tokens = {normalized, normalized.replace(' ', ''), normalized.replace(' ', '').replace('程', '')}
    if normalized in APP_ALIAS_MAP:
        candidate_tokens.add(APP_ALIAS_MAP[normalized])
    for tok in candidate_tokens:
        if not tok:
            continue
        for key, path in index.items():
            if tok in key:
                return path
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


_ollama_model_cache = None
_ollama_model_cache_t = 0.0
_ollama_model_cache_ttl = 60  # 秒：自动检测结果缓存时间，避免每轮对话都 ollama.list()


def get_ollama_model():
    """返回当前 Ollama 模型名：优先用配置的模型，否则自动检测已部署模型。

    自动检测结果带 TTL 缓存（默认 60s），避免每一轮对话都调用 ollama.list()；
    在 Ollama 里切换（部署/删除）模型后最长 60s 内生效。
    """
    global _ollama_model, _ollama_model_cache, _ollama_model_cache_t
    if _ollama_model:
        return _ollama_model  # 配置了具体模型，优先使用
    now = time.time()
    if _ollama_model_cache and now - _ollama_model_cache_t < _ollama_model_cache_ttl:
        return _ollama_model_cache
    try:
        models = ollama.list()
        names = [m.model for m in (models.models or [])]
        if names:
            # 优先非推理模型，deepseek-r1 这类推理模型放最后
            for n in names:
                if 'deepseek-r1' in n.lower():
                    continue
                _ollama_model_cache = n
                _ollama_model_cache_t = now
                return n
            _ollama_model_cache = names[0]
            _ollama_model_cache_t = now
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


def _to_plain(obj):
    """递归把对象转成纯 JSON 可序列化的 dict/list（处理 ToolCall 等对象）"""
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(x) for x in obj]
    if hasattr(obj, 'model_dump'):
        try:
            return _to_plain(obj.model_dump(exclude_none=True))
        except Exception:
            pass
    if hasattr(obj, '__dict__'):
        return _to_plain(vars(obj))
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


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


def _sanitize_history():
    """清理 chat_history 中无法 JSON 序列化的内容（如残留的 ToolCall 对象）"""
    global chat_history
    clean = []
    for msg in chat_history:
        if not isinstance(msg, dict):
            continue
        role = msg.get('role')
        if role in ('system', 'user', 'assistant'):
            clean.append({'role': role, 'content': str(msg.get('content') or '')})
        # 'tool' 角色的消息在回退普通聊天时直接丢弃（不带工具调用上下文无法配对）
    chat_history = clean


def _stream_say(text_buffer):
    """把累积的文本缓冲按句切分，遇到句末标点就入队播报一句。

    切分优先级：句末标点（。！？等）→ 逗号（，、；：等）。逗号切出的片段
    太短（< MIN_COMMA_LEN）时不播，留给后面的句号一起播，避免碎得太碎。
    返回 True 表示本次切出了至少一句并送入 TTS 队列（用于标记"已流式播报"）。
    调用方负责在生成结束后把 buffer 里剩下的尾巴再 flush 一次。
    """
    import re
    # 句末标点：中文句号、叹号、问号、省略号；英文 . ! ?；以及换行
    sentence_end = re.compile(r'[。！？!?.?…\n]')
    # 逗号类：中文逗号、顿号、分号、冒号，用于句内进一步切分、降低首包延迟
    comma_end = re.compile(r'[，、；：,]')
    MIN_COMMA_LEN = 8  # 逗号切出的片段至少这么长才单独播，否则攒到句号
    spoken = False
    buf = text_buffer.get('buf', '')
    pos = 0
    pending = ''  # 逗号切分时攒着的"还没够长"的片段
    while pos < len(buf):
        m = sentence_end.search(buf, pos)
        c = comma_end.search(buf, pos)
        if m and (c is None or m.start() < c.start()):
            # 先遇到句末标点：把 pending + 到句末的内容一起播
            end = m.end()
            seg = (pending + buf[pos:end]).strip()
            pos = end
            pending = ''
            if seg:
                try:
                    from src.feedback import say
                except ImportError:
                    from feedback import say
                say(seg)
                spoken = True
        elif c is not None:
            # 先遇到逗号：切出片段，够长就播、不够就攒进 pending
            end = c.end()
            frag = (pending + buf[pos:end]).strip()
            pos = end
            if len(frag) >= MIN_COMMA_LEN:
                try:
                    from src.feedback import say
                except ImportError:
                    from feedback import say
                say(frag)
                spoken = True
                pending = ''
            else:
                pending = frag
        else:
            break
    # 没遇到标点的剩余部分留在 buffer（可能还在 pending 或 buf 尾部）
    if pending:
        text_buffer['buf'] = pending + buf[pos:]
    else:
        text_buffer['buf'] = buf[pos:]
    return spoken


def _flush_stream(text_buffer):
    """生成结束后把缓冲里残留的尾巴播报出去（不足一句的内容也念）"""
    tail = (text_buffer.get('buf') or '').strip()
    if tail:
        try:
            from src.feedback import say
        except ImportError:
            from feedback import say
        say(tail)
        return True
    return False


def _run_ai_agent(user_input):
    """AI agent：通过 function calling 让模型自主调用工具"""
    load_chat_history()
    chat_history.append({'role': 'user', 'content': user_input})
    try:
        return _agent_loop()
    except Exception as e:
        # 模型不支持 function calling 等情况，回退到普通流式聊天
        print(f'[Agent] function calling 不可用，回退普通聊天: {e}')
        # 回退前清理历史中可能残留的工具调用消息，避免二次序列化失败
        _sanitize_history()
        return _stream_chat()


def _agent_loop():
    """agent 主循环：模型调用工具 -> 执行 -> 反馈，直到得到最终回复。
    最终回复一轮使用流式输出，边生成边按句播报（降低首包延迟）。
    """
    global chat_history
    from .stop import STOP_REQUESTED
    reply = ''
    streamed = False  # 是否已通过流式按句播报（用于通知上层跳过整段 TTS）
    text_buffer = {'buf': ''}
    for _ in range(6):  # 最多 6 轮工具调用
        if STOP_REQUESTED.is_set():
            reply = '（已停止）'
            break

        # 仅最后一轮（无工具调用、有正文）才走流式播报；中间轮工具调用不播
        response = ollama.chat(
            model=get_ollama_model(),
            messages=chat_history,
            tools=TOOLS,
            stream=True,
        )
        message = None
        content_parts = []
        tool_calls_raw = None
        for chunk in response:
            msg = chunk.get('message', {})
            piece = msg.get('content', '') or ''
            if piece:
                content_parts.append(piece)
                text_buffer['buf'] += piece
                if _stream_say(text_buffer):
                    streamed = True
            # 工具调用通常在最后一个 chunk 的 message 上以对象形式给出
            tc = msg.get('tool_calls')
            if tc:
                tool_calls_raw = tc

        raw_content = ''.join(content_parts).strip()
        # 归一化 tool_calls（可能是 pydantic 对象）
        tool_calls = _to_plain(tool_calls_raw or [])

        if not tool_calls:
            reply = raw_content or '（无回复）'
            chat_history.append({'role': 'assistant', 'content': reply})
            break

        # 记录 assistant 的工具调用消息（用归一化后的纯 dict）
        chat_history.append({
            'role': 'assistant',
            'content': raw_content,
            'tool_calls': tool_calls,
        })

        # 执行工具并把结果反馈给模型
        # 失败保护：同一工具连续失败达到上限就放弃，避免模型反复调同一个坏工具导致死循环
        MAX_TOOL_FAILS = 3
        fail_streak = 0
        for tc in tool_calls:
            fn = tc.get('function', {}) if isinstance(tc, dict) else {}
            name = fn.get('name', '')
            args = fn.get('arguments', {})
            if isinstance(args, str):
                try:
                    import json as _json
                    args = _json.loads(args)
                except Exception:
                    args = {}
            ok, result = _execute_tool(name, args)
            if not ok:
                fail_streak += 1
                print(f'[Tool] {name} 执行失败（连续第 {fail_streak} 次）')
                if fail_streak >= MAX_TOOL_FAILS:
                    print(f'[Tool] {name} 连续失败 {MAX_TOOL_FAILS} 次，放弃本轮工具调用')
                    chat_history.append({
                        'role': 'tool',
                        'tool_name': name or 'unknown',
                        'content': f'工具 {name} 连续失败 {MAX_TOOL_FAILS} 次，已停止重试。请直接回答或换其他方式。',
                    })
                    # 跳出不成功的工具，直接进入下一轮让模型决定收尾
                    break
            else:
                fail_streak = 0
            # tool 消息需要带 tool_name（新版 ollama 库的格式要求）
            chat_history.append({'role': 'tool', 'tool_name': name or 'unknown', 'content': str(result)})
        # 工具调用轮结束后，清空缓冲，避免把中间轮碎片当作正文播报
        text_buffer['buf'] = ''

    if not reply:
        reply = '（未获得回复）'

    # 生成结束，把剩余缓冲 flush 成最后一句播报
    if _flush_stream(text_buffer):
        streamed = True

    # 每轮轻量抽取长期事实（GPT 式长期记忆），失败不影响对话
    try:
        _update_long_term_memory(chat_history)
    except Exception as e:
        print(f'[长期记忆] 抽取调用异常（已忽略）: {e}')

    # 历史过长时压缩为摘要（增量），避免暴力截断丢失人设与长期记忆
    compact_history()
    save_chat_history()
    return True, reply, streamed


def _stream_chat():
    """普通流式聊天（agent 回退，支持中途停止），边生成边按句播报"""
    global chat_history
    from .stop import STOP_REQUESTED
    full_reply = ''
    text_buffer = {'buf': ''}
    streamed = False
    for chunk in ollama.chat(
        model=get_ollama_model(),
        messages=chat_history,
        stream=True
    ):
        piece = chunk.get('message', {}).get('content', '') or ''
        full_reply += piece
        text_buffer['buf'] += piece
        if _stream_say(text_buffer):
            streamed = True
        if STOP_REQUESTED.is_set():
            full_reply = full_reply.strip() + '\n（已停止）'
            break
    reply = full_reply.strip() or '（无回复）'
    if _flush_stream(text_buffer):
        streamed = True
    chat_history.append({'role': 'assistant', 'content': reply})
    if len(chat_history) > 20:
        chat_history = chat_history[-20:]
    save_chat_history()
    return True, reply, streamed


def web_search(query):
    """打开默认浏览器搜索关键词（对应"搜索一下"等语音指令）"""
    import urllib.parse
    query = (query or '').strip()
    if not query:
        return False, '请告诉我要搜索的内容，例如「搜索一下 Python 教程」'
    url = 'https://www.bing.com/search?q=' + urllib.parse.quote(query)
    try:
        import webbrowser
        webbrowser.open(url)
        return True, f'已在浏览器搜索：{query}'
    except Exception as e:
        return False, f'打开浏览器失败: {e}'


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

    if intent_name == 'web_search':
        return web_search(slots.get('query', ''))

    if intent_name == 'get_time':
        return get_time()

    if intent_name == 'get_date':
        return get_date()

    if intent_name == 'remember':
        content = (slots.get('content') or raw_text or '').strip()
        return _handle_remember(content)

    if intent_name == 'forget':
        content = (slots.get('content') or raw_text or '').strip()
        return _handle_forget(content)

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
