import json
import os
import re

try:
    from .nlu.rules import FALLBACK_RULES, extract_app_name
    SHARED_RULES_AVAILABLE = True
except ImportError:
    SHARED_RULES_AVAILABLE = False


class IntentParser:
    def __init__(self, intents_path):
        self.intents_path = intents_path
        self.intents = self._load_intents()

    def _load_intents(self):
        if not os.path.exists(self.intents_path):
            return []
        with open(self.intents_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def parse(self, text):
        text = text.strip().lower()

        for intent in self.intents:
            for pattern in intent.get('patterns', []):
                if re.search(pattern, text):
                    slots = {}
                    for slot_name, regex in intent.get('slots', {}).items():
                        m = re.search(regex, text)
                        if m:
                            slots[slot_name] = m.group(1)
                    return intent['name'], slots

        # 使用共享回退规则（如果可用）
        if SHARED_RULES_AVAILABLE:
            for rule in FALLBACK_RULES:
                if rule['intent'] == 'save_to_folder':
                    # save_to_folder 有特殊语法，单独处理
                    continue
                if any(kw in text for kw in rule['keywords']):
                    slots = {}
                    if rule['slot_pattern']:
                        m = re.search(rule['slot_pattern'], text)
                        if m:
                            slots[rule['slot_name']] = m.group(1).strip()
                            if rule['intent'] in ('open_app', 'close_app') and SHARED_RULES_AVAILABLE:
                                slots['app_name'] = extract_app_name(m.group(1).strip())
                    return rule['intent'], slots

        # save_to_folder 特殊语法处理
        if '存入文件夹' in text or '保存到文件夹' in text or '存入文件' in text:
            folder_match = re.search(r'(?:存入文件夹|保存到文件夹)\s*([^\s:：]+)\s*(?:内容|文本)?\s*[:：]?\s*(.*)', text)
            file_match = re.search(r'(?:存入文件|保存)\s*([^\s:：]+\/[^\s:：]+)\s*(.*)', text)
            if folder_match:
                folder = folder_match.group(1).strip()
                content = folder_match.group(2).strip() or ''
                return 'save_to_folder', {'folder': folder, 'content': content}
            if file_match:
                filepath = file_match.group(1).strip()
                content = file_match.group(2).strip() or ''
                return 'save_to_folder', {'filepath': filepath, 'content': content}

        return 'unknown', {}
