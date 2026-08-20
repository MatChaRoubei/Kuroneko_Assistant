"""
增强的正则意图匹配器
在原有 IntentParser 基础上增加模糊匹配能力
"""
import re
import os
import json
from difflib import SequenceMatcher

from .rules import FALLBACK_RULES, DOUYIN_ACTION_KEYWORDS, extract_app_name, extract_douyin_action


class FuzzyRegexMatcher:
    """支持模糊匹配的意图解析器"""

    def __init__(self, intents_path=None):
        """
        初始化模糊匹配器

        Args:
            intents_path: intents.json 文件路径
        """
        self.intents_path = intents_path
        self.intents = []
        if intents_path and os.path.exists(intents_path):
            self._load_intents(intents_path)

        # 回退规则（从共享模块导入）
        self._fallback_rules = FALLBACK_RULES

    def _load_intents(self, intents_path):
        """从 JSON 文件加载意图定义"""
        try:
            with open(intents_path, 'r', encoding='utf-8') as f:
                self.intents = json.load(f)
        except Exception:
            self.intents = []

    def match(self, text):
        """
        尝试匹配意图

        Args:
            text: 输入文本

        Returns:
            (intent_name, slots_dict) 或 ('unknown', {})
        """
        text = text.strip().lower()

        # 1. 尝试 JSON 中的正则 patterns
        for intent in self.intents:
            for pattern in intent.get('patterns', []):
                if re.search(pattern, text):
                    slots = {}
                    for slot_name, regex in intent.get('slots', {}).items():
                        m = re.search(regex, text)
                        if m:
                            slots[slot_name] = m.group(1)
                    return intent['name'], slots

        # 2. 尝试共享回退规则
        has_douyin_action = any(kw in text for kw in DOUYIN_ACTION_KEYWORDS)

        for rule in self._fallback_rules:
            if any(kw in text for kw in rule['keywords']):
                slots = {}
                # 抖音控制特殊处理：从关键词直接提取动作
                if rule['intent'] == 'douyin_control':
                    action = extract_douyin_action(text, rule['keywords'])
                    if action:
                        slots['action'] = action
                        return 'douyin_control', slots
                    continue  # 没匹配到关键词，继续下一个规则
                # 如果当前是 open_app/close_app，且文本中也包含抖音动作词
                # 优先使用抖音控制（如"打开评论"、"关闭弹幕"）
                if rule['intent'] in ('open_app', 'close_app') and has_douyin_action:
                    action = extract_douyin_action(text, DOUYIN_ACTION_KEYWORDS)
                    if action:
                        return 'douyin_control', {'action': action}
                if rule['slot_pattern']:
                    m = re.search(rule['slot_pattern'], text)
                    if m:
                        slots[rule['slot_name']] = m.group(1).strip()
                        # 尝试模糊提取应用名
                        if rule['intent'] in ('open_app', 'close_app'):
                            slots['app_name'] = extract_app_name(m.group(1).strip())
                return rule['intent'], slots

        return 'unknown', {}

    def _calculate_similarity(self, s1, s2):
        """计算两个字符串的相似度"""
        return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    def add_intent(self, intent_dict):
        """动态添加新意图"""
        self.intents.append(intent_dict)
