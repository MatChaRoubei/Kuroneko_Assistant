"""
ASR 语音识别纠错模块
基于拼音相似度和 Levenshtein 距离修正常见的语音识别错误
例如："打一微信" -> "打开微信"
"""
import os
import re
from difflib import SequenceMatcher

try:
    from pypinyin import lazy_pinyin, Style
    PYPINYIN_AVAILABLE = True
except ImportError:
    PYPINYIN_AVAILABLE = False

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


class PhoneticCorrector:
    """基于拼音的语音识别纠错器"""

    # 中文数字映射
    CN_NUM = {
        '零': '0', '〇': '0',
        '一': '1', '么': '1',
        '二': '2', '两': '2',
        '三': '3',
        '四': '4',
        '五': '5',
        '六': '6',
        '七': '7', '期': '7',
        '八': '8',
        '九': '9',
        '十': '10',
        '百': '100',
        '千': '1000',
    }

    def __init__(self, intents_path=None, app_names=None):
        """
        初始化纠错器

        Args:
            intents_path: intents.json 文件路径
            app_names: 应用名称列表（从 APP_MAP 提取）
        """
        self.commands = []  # 原始命令文本列表
        self.command_pinyin = []  # 对应拼音列表

        # 基础命令语料（常见ASR错误模式）
        self._common_asr_errors = {
            '打一': '打开',
            '打币': '打开',
            '打歪': '打开',
            '打一微': '打开微信',
            '关掉微': '关闭微信',
            '关掉一': '关闭',
            '启一': '启动',
            '启币': '启动',
            '暂停一': '暂停',
            '播放一': '播放',
        }

        # 从 intents.json 加载命令
        if intents_path and os.path.exists(intents_path):
            self._load_intents(intents_path)

        # 添加应用名称到语料
        if app_names:
            for name in app_names:
                self._add_command(name)

    def _load_intents(self, intents_path):
        """从 intents.json 加载所有 pattern 作为命令语料"""
        import json
        try:
            with open(intents_path, 'r', encoding='utf-8') as f:
                intents = json.load(f)
            for intent in intents:
                for pattern in intent.get('patterns', []):
                    # 提取 pattern 中的实际文本（去掉正则符号）
                    cmd = self._extract_text_from_pattern(pattern)
                    if cmd:
                        self._add_command(cmd)
        except Exception:
            pass

    def _extract_text_from_pattern(self, pattern):
        """从正则 pattern 中提取可读文本"""
        # 简单处理：去掉常见正则符号
        text = pattern
        text = text.replace('.*', '').replace('(.+)', '').replace('(', '').replace(')', '')
        text = text.replace('|', ' ').replace('[', '').replace(']', '')
        text = text.strip()
        return text if text else None

    def _add_command(self, cmd):
        """添加命令到语料库"""
        if not cmd or cmd in self.commands:
            return
        self.commands.append(cmd)
        self.command_pinyin.append(self._to_pinyin(cmd))

    def _to_pinyin(self, text):
        """将中文文本转换为拼音（无声调）"""
        if not PYPINYIN_AVAILABLE:
            return text.lower()
        try:
            # 提取不带声调的拼音
            pinyin = lazy_pinyin(text, style=Style.NORMAL)
            return ''.join(pinyin)
        except Exception:
            return text.lower()

    def _calc_similarity(self, s1, s2):
        """计算两个字符串的相似度"""
        if RAPIDFUZZ_AVAILABLE:
            return fuzz.ratio(s1, s2) / 100.0
        else:
            return SequenceMatcher(None, s1, s2).ratio()

    def _convert_chinese_number(self, text):
        """
        将中文数字表达式转换为阿拉伯数字
        例如: "百分之五十" -> "50", "调音量到八十" -> "调音量到80", "二十三" -> "23"
        """
        # 1. 处理 "百分之X" 模式 -> "X"
        text = re.sub(r'百分之(\d+)', lambda m: m.group(1), text)
        text = re.sub(r'百分之([零一二三四五六七八九十〇]+)', self._cn_to_digit, text)

        # 2. 处理 "一百五十" 等（X百Y十Z = X*100+Y*10+Z），需先于单独的一百处理
        text = re.sub(r'([零一二三四五六七八九〇])百([零一二三四五六七八九〇]?)十([零一二三四五六七八九〇]?)',
                      lambda m: str(self._hundred_cn_to_int(m.group(1), m.group(2), m.group(3))), text)

        # 3. 处理 "一百", "五百" 等（X百 = X*100）
        text = re.sub(r'([零一二三四五六七八九〇])百(?![零一二三四五六七八九〇十])',
                      lambda m: str(int(self._single_cn_to_digit(m.group(1))) * 100), text)

        # 4. 处理 "一千三百" 等（X千Y百 = X*1000+Y*100）
        text = re.sub(r'([零一二三四五六七八九〇])千([零一二三四五六七八九〇]?)百',
                      lambda m: str(self._thousand_cn_to_int(m.group(1), m.group(2))), text)

        # 5. 处理 "十X" 模式（如"十三"=13，"十"=10），但排除"八十"等"X十"模式
        text = re.sub(r'(?<![\d零一二三四五六七八九〇])十([零一二三四五六七八九〇]?)',
                      lambda m: '10' if m.group(1) == '' else '1' + self._single_cn_to_digit(m.group(1)), text)

        # 6. 处理 "二十", "八十" 等（X十 = X*10，但不处理"十三"等十X模式）
        text = re.sub(r'([零一二三四五六七八九〇])十(?![零一二三四五六七八九〇])',
                      lambda m: str(int(self._single_cn_to_digit(m.group(1))) * 10), text)

        # 7. 处理 "二十三", "八十五" 等（X十Y = X*10+Y）
        text = re.sub(r'([零一二三四五六七八九〇])十([零一二三四五六七八九〇])',
                      lambda m: str(int(self._single_cn_to_digit(m.group(1))) * 10 + int(self._single_cn_to_digit(m.group(2)))), text)

        # 8. 处理剩余的个位数字
        text = re.sub(r'[零一二三四五六七八九〇]', self._single_cn_to_digit, text)

        return text

    def _hundred_cn_to_int(self, hundreds, tens, ones):
        """将百位数字转为整数: X百Y十Z = X*100 + Y*10 + Z"""
        h = int(self._single_cn_to_digit(hundreds)) if hundreds else 0
        t = int(self._single_cn_to_digit(tens)) if tens and tens != '零' else 0
        o = int(self._single_cn_to_digit(ones)) if ones and ones != '零' else 0
        return h * 100 + t * 10 + o

    def _thousand_cn_to_int(self, thousands, hundreds):
        """将千位数字转为整数: X千Y百 = X*1000 + Y*100"""
        t = int(self._single_cn_to_digit(thousands)) if thousands else 0
        h = int(self._single_cn_to_digit(hundreds)) if hundreds and hundreds != '零' else 0
        return t * 1000 + h * 100

    def _cn_to_digit(self, m):
        """将中文数字字符串（如"五十"、"三十"）转换为数字字符串"""
        s = m.group(1) if hasattr(m, 'group') else str(m)
        return str(self._parse_cn_number(s))

    def _parse_cn_number(self, s):
        """将中文数字字符串解析为整数（如"五十"->50, "十三"->13, "九"->9）"""
        # 数字位权：一二三四五六七八九 = 1-9, 十 = 10
        units = {'零': 0, '〇': 0, '一': 1, '么': 1, '二': 2, '两': 2, '三': 3,
                 '四': 4, '五': 5, '六': 6, '七': 7, '期': 7, '八': 8, '九': 9, '十': 10}
        result = 0
        temp = 0
        for c in s:
            if c in units:
                v = units[c]
                if v == 10:  # '十'
                    temp = temp * 10 if temp else 10
                    result = result + temp
                    temp = 0
                else:
                    temp = temp * 10 + v if temp else v
            # 忽略其他字符
        return result + temp

    def _single_cn_to_digit(self, m):
        """将单个中文数字字符转换为数字字符"""
        if isinstance(m, str):
            c = m
        else:
            c = m.group(0) if hasattr(m, 'group') else str(m)
        return self.CN_NUM.get(c, c)

    def correct(self, text):
        """
        对输入文本进行纠错

        Args:
            text: 原始识别文本

        Returns:
            纠错后的文本
        """
        if not text:
            return text

        original = text
        text = text.strip().lower()

        # 0. 如果输入文本本身就是一个已知命令，直接返回（避免误纠正）
        if text in self.commands:
            return text

        # 1. 检查常见ASR错误映射（快速路径）
        for error, correct in self._common_asr_errors.items():
            if error in text:
                text = text.replace(error, correct)

        # 2. 中文数字转换（如"百分之五十" -> "50"，"八十" -> "80"）
        text = self._convert_chinese_number(text)

        # 2. 尝试拼音相似度匹配
        text_pinyin = self._to_pinyin(text)

        best_match = None
        best_score = 0
        threshold = 0.75  # 相似度阈值

        for i, cmd_pinyin in enumerate(self.command_pinyin):
            # 使用快速 fuzz 过滤
            score = self._calc_similarity(text_pinyin, cmd_pinyin)
            if score > best_score and score >= threshold:
                best_score = score
                best_match = self.commands[i]

        if best_match and best_match != text:
            return best_match

        return text  # 返回已转换（包括中文数字转换）后的文本

    def add_commands(self, commands):
        """动态添加命令到语料库"""
        for cmd in commands:
            self._add_command(cmd)


def build_corrector_from_app_map(app_map):
    """从 APP_MAP 构建纠错器"""
    app_names = list(app_map.keys()) if app_map else []
    corrector = PhoneticCorrector(app_names=app_names)
    return corrector
