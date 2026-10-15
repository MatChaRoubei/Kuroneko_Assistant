"""Precise, shared parsing for everyday spoken commands.

Numbers are converted only in numeric slots; search and note content stay intact.
"""
import re

NUMBER = r'(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)'
DURATION = rf'(?:(?:{NUMBER}个?半?|半)\s*(?:小时|分钟|分|秒钟|秒)\s*)+'


def spoken_number(value):
    value = str(value).strip()
    try:
        return float(value)
    except ValueError:
        pass
    digits = dict(zip('零〇一二两三四五六七八九', [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    if value and all(character in digits for character in value):
        return int(''.join(str(digits[character]) for character in value))
    total = current = 0
    for character in value:
        if character in digits:
            current = digits[character]
        elif character in '十百千':
            total += (current or 1) * {'十': 10, '百': 100, '千': 1000}[character]
            current = 0
        else:
            raise ValueError('不是有效数字')
    return total + current


def duration_seconds(value):
    total = 0
    parts = re.findall(rf'({NUMBER}|半)(个?半?)\s*(小时|分钟|分|秒钟|秒)', value)
    if not parts:
        raise ValueError('缺少时长')
    for number, suffix, unit in parts:
        amount = 0.5 if number == '半' else spoken_number(number)
        if '半' in suffix:
            amount += 0.5
        total += amount * {'小时': 3600, '分钟': 60, '分': 60, '秒钟': 1, '秒': 1}[unit]
    return total


def parse_command(text):
    """Return None for custom/NLU matching, or an explicit intent and slots."""
    original = str(text).strip().strip('。！？!?')
    command = re.sub(r'^(?:(?:麻烦你|请你|帮我|麻烦|请)\s*)+', '', original).strip()
    # Questions and negations are conversation, never actionable substring hits.
    if re.match(r'^(?:不要|别|不许|不用|不想|怎么|如何|为什么|如果|假如|能不能介绍|告诉我怎么)', command):
        return 'unknown', {}
    for prefix, name, slot in [('搜索一下', 'web_search', 'query'), ('搜索', 'web_search', 'query'),
                                ('搜一下', 'web_search', 'query'), ('百度一下', 'web_search', 'query'),
                                ('记住', 'remember', 'content'), ('记下来', 'remember', 'content')]:
        if command.startswith(prefix):
            return name, {slot: command[len(prefix):].strip()}
    if re.fullmatch(r'(?:有哪些功能|你会什么|使用帮助|帮助|语音帮助)', command):
        return 'show_help', {}
    if command in ('开启连续对话', '进入连续对话'):
        return 'dialogue_mode', {'enabled': True}
    if command in ('结束对话', '退出对话', '关闭连续对话', '结束连续对话'):
        return 'dialogue_mode', {'enabled': False}
    if re.fullmatch(r'(?:查看|列出|显示|查询)(?:所有)?(?:提醒|定时器|闹钟)(?:列表)?|提醒列表|有哪些提醒', command):
        return 'list_reminders', {}
    if re.fullmatch(r'取消(?:全部|所有)(?:提醒|定时器|闹钟)', command):
        return 'cancel_reminder', {'target': 'all'}
    if re.fullmatch(r'取消(?:最后|上|这)(?:一)?个(?:提醒|定时器|闹钟)|取消(?:提醒|定时器|闹钟)', command):
        return 'cancel_reminder', {'target': 'latest'}
    match = re.fullmatch(rf'取消第?\s*({NUMBER})\s*(?:号|个)?(?:提醒|定时器|闹钟)', command)
    if match:
        return 'cancel_reminder', {'target': int(spoken_number(match[1]))}
    match = re.fullmatch(rf'({DURATION})(?:以?后)(?:提醒我|叫我|提醒)(.*)', command)
    if not match:
        match = re.fullmatch(rf'(?:设置|设个|设一个|定时|倒计时|闹钟)\s*({DURATION})(?:的)?(?:定时器|定时|提醒|闹钟)?', command)
    if match:
        return 'set_timer', {'seconds': duration_seconds(match[1]), 'content': match[2].strip() if match.lastindex > 1 else '时间到'}
    if re.fullmatch(r'(?:取消关机|取消重启|取消关机计划|取消重启计划)', command):
        return 'cancel_shutdown', {}
    if re.fullmatch(r'(?:静音|关闭声音|关闭音量)', command):
        return 'mute_volume', {'muted': True}
    if re.fullmatch(r'(?:取消静音|恢复声音|恢复音量)', command):
        return 'mute_volume', {'muted': False}
    if re.fullmatch(r'(?:当前音量|音量多少|现在音量多少)', command):
        return 'get_volume', {}
    if re.fullmatch(r'(?:音量(?:大|高|加)(?:一)?点|调大(?:一点)?音量|增大音量|提高音量)', command):
        return 'adjust_volume', {'delta': 10}
    if re.fullmatch(r'(?:音量(?:小|低|减)(?:一)?点|调小(?:一点)?音量|减小音量|降低音量)', command):
        return 'adjust_volume', {'delta': -10}
    match = re.fullmatch(rf'(?:设置|调节)?\s*音量\s*(?:设置|调|调整)?\s*(?:为|到)?\s*(?:百分之)?({NUMBER})\s*%?', command)
    if match:
        return 'set_volume', {'value': spoken_number(match[1])}
    # These names express toggles honestly: Windows media keys cannot verify
    # whether an arbitrary player was already paused before the key press.
    media = {'播放暂停': 'play_pause', '播放或暂停': 'play_pause', '暂停音乐': 'play_pause',
             '播放音乐': 'play_pause', '下一首': 'next_track', '上一首': 'previous_track'}
    if command in media:
        return 'media_control', {'action': media[command]}
    windows = {'最小化当前窗口': 'minimize', '最大化当前窗口': 'maximize',
               '还原当前窗口': 'restore', '关闭当前窗口': 'close'}
    if command in windows:
        return 'window_control', {'action': windows[command]}
    dangerous = {'关机': 'systemShutdown', '关闭系统': 'systemShutdown', '关电脑': 'systemShutdown',
                 '重启': 'systemReboot', '重启电脑': 'systemReboot', '重新启动电脑': 'systemReboot',
                 '锁屏': 'systemLock', '锁定电脑': 'systemLock', '息屏': 'systemLock',
                 '睡眠': 'systemSleep', '休眠': 'systemSleep', '待机': 'systemSleep'}
    if command in dangerous:
        return dangerous[command], {}
    if any(word in command for word in ('关机', '重启', '睡眠', '休眠')):
        return 'unknown', {}
    if re.search(r'(?:打开|关闭|启动|退出)', command) and not re.match(r'^(?:打开|关闭|启动|退出)', command):
        return 'unknown', {}
    return None
