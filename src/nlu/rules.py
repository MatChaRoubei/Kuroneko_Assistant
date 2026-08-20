"""
共享的意图回退规则
所有 NLU 解析器共享同一套回退规则，避免重复维护
"""
from difflib import SequenceMatcher

# 抖音动作关键词（统一维护）
DOUYIN_ACTION_KEYWORDS = [
    '继续播放', '继续', '下一个', '上一个',
    '点赞', '收藏', '关注', '取消关注', '评论', '分享',
    '暂停', '播放', '全屏', '网页全屏', '小窗', '自动连播',
    '清屏', '弹幕', '不感兴趣', '相关推荐', '作者主页', '复制口令',
    '上滑', '下滑', '快进', '快退', '音量加', '音量减', '稍后再看',
    '静音', '刷新', '首页'
]

# 回退规则列表（供 FuzzyRegexMatcher 和 IntentParser 共用）
FALLBACK_RULES = [
    {
        'keywords': ['打开', '启动'],
        'intent': 'open_app',
        'slot_name': 'app_name',
        'slot_pattern': r'.*(?:打开|启动)\s*(.*)'
    },
    {
        'keywords': ['关闭', '退出'],
        'intent': 'close_app',
        'slot_name': 'app_name',
        'slot_pattern': r'.*(?:关闭|退出)\s*(.*)'
    },
    {
        'keywords': ['设置', '音量'],
        'intent': 'set_volume',
        'slot_name': 'value',
        'slot_pattern': r'音量.*?(\d+)'
    },
    {
        'keywords': ['定时', '闹钟'],
        'intent': 'set_timer',
        'slot_name': 'minutes',
        'slot_pattern': r'(\d+)\s*分钟'
    },
    {
        'keywords': ['列出应用', '显示应用', '应用列表'],
        'intent': 'list_apps',
        'slot_name': None,
        'slot_pattern': None
    },
    {
        'keywords': ['检查应用', '查询应用', '是否安装'],
        'intent': 'check_app',
        'slot_name': 'app_name',
        'slot_pattern': r'(?:检查应用|查询应用|是否安装)\s*(.*)'
    },
    {
        'keywords': ['存入文件夹', '保存到文件夹', '存入文件'],
        'intent': 'save_to_folder',
        'slot_name': None,
        'slot_pattern': None
    },
    # 抖音控制回退规则（无前缀）
    {
        'keywords': DOUYIN_ACTION_KEYWORDS,
        'intent': 'douyin_control',
        'slot_name': 'action',
        'slot_pattern': None
    },
    # 系统控制回退规则
    {
        'keywords': ['关机', '关闭系统', '关电脑'],
        'intent': 'systemShutdown',
        'slot_name': None,
        'slot_pattern': None
    },
    {
        'keywords': ['重启', '重新启动', '重启电脑', '重新启动电脑'],
        'intent': 'systemReboot',
        'slot_name': None,
        'slot_pattern': None
    },
    {
        'keywords': ['息屏', '锁屏', '锁定电脑', '锁电脑'],
        'intent': 'systemLock',
        'slot_name': None,
        'slot_pattern': None
    },
    {
        'keywords': ['休眠', '睡眠', '待机'],
        'intent': 'systemSleep',
        'slot_name': None,
        'slot_pattern': None
    },
]


def extract_app_name(raw_name):
    """
    模糊提取应用名称
    移除常见前缀/后缀词
    """
    if not raw_name:
        return raw_name

    stop_words = ['程序', '软件', '应用', '打开', '启动', '关闭', '退出',
                  '请', '帮我', '我想', '能不能', '可以', '那个', '这个']
    result = raw_name
    for word in stop_words:
        result = result.replace(word, '')

    result = result.strip(' .，。、!！?？')
    return result if result else raw_name


def calculate_similarity(s1, s2):
    """计算两个字符串的相似度"""
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def extract_douyin_action(text, keywords=None):
    """从文本中提取匹配的抖音动作词"""
    if keywords is None:
        keywords = DOUYIN_ACTION_KEYWORDS
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return kw
    return None
