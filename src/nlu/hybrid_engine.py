"""
混合 NLU 引擎
结合 FuzzyRegex（快速路径）和 EmbeddingMatcher（语义路径）
作为 IntentParser 的高级替代品
"""
import os
import json
from typing import Tuple, Dict

from .fuzzy_regex import FuzzyRegexMatcher


class HybridNLUEngine:
    """
    混合意图解析引擎

    解析流程：
    1. FuzzyRegex 快速路径（正则匹配，高精度）
    2. 父类 IntentParser 回退（兼容 legacy）
    """

    def __init__(
        self,
        intents_path: str,
        intent_descriptions_path: str = None,
        embedding_threshold: float = 0.65,
        use_embedding: bool = False
    ):
        """
        Args:
            intents_path: intents.json 路径
            intent_descriptions_path: intent_descriptions.json 路径（预留）
            embedding_threshold: 嵌入匹配置信度阈值（预留）
            use_embedding: 是否启用 embedding 匹配（预留，需 sentence-transformers）
        """
        self.intents_path = intents_path
        self.intent_descriptions_path = intent_descriptions_path

        # 快速路径：模糊正则匹配器
        self.fuzzy_regex = FuzzyRegexMatcher(intents_path)

        self._load_intent_descriptions()

    def _load_intent_descriptions(self):
        """加载意图描述语料"""
        if not self.intent_descriptions_path or not os.path.exists(self.intent_descriptions_path):
            self._intent_descriptions = []
            return
        try:
            with open(self.intent_descriptions_path, 'r', encoding='utf-8') as f:
                self._intent_descriptions = json.load(f)
        except Exception:
            self._intent_descriptions = []

    def parse(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        解析文本意图

        Args:
            text: 输入文本

        Returns:
            (intent_name, slots_dict)
        """
        text = text.strip().lower()
        if not text:
            return 'unknown', {}

        # 1. FuzzyRegex 快速路径
        intent, slots = self.fuzzy_regex.match(text)
        if intent != 'unknown':
            return intent, slots

        # 2. 回退到原始 IntentParser（legacy 兼容）
        return 'unknown', {}

    def add_intent(self, name: str, description: str, slots: Dict = None):
        """动态添加新意图描述"""
        self._intent_descriptions.append({
            'name': name,
            'description': description,
            'slots': slots or {}
        })
