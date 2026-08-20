"""
抖音键盘控制器
通过 pyautogui 模拟键盘快捷键控制抖音桌面版
"""
import time
from typing import Tuple, List, Optional

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False


# 抖音桌面版键盘快捷键映射
DOUYIN_SHORTCUTS = {
    # 播放控制
    'play': 'space',           # 暂停/播放
    'pause': 'space',          # 暂停

    # 视频互动（主界面）
    'like': 'z',               # 点赞/取消点赞
    'collect': 'c',            # 收藏/取消收藏
    'follow': 'g',             # 关注/取消关注
    'author_home': 'f',        # 进入作者主页
    'comment': 'x',            # 评论
    'share_link': 'v',         # 复制分享口令
    'share': 'p',              # 推荐给朋友
    'not_interested': 'r',     # 不感兴趣
    'related': 'n',            # 相关推荐

    # 弹幕与画面
    'danmaku': 'b',            # 开启/关闭弹幕
    'clear_screen': 'j',      # 清屏
    'auto_play': 'k',         # 自动连播
    'web_fullscreen': 'y',    # 网页内全屏
    'fullscreen': 'h',        # 全屏
    'watch_later': 'l',       # 稍后再看
    'pip': 'u',               # 小窗模式

    # 导航
    'next_video': 'down',     # 下一个视频
    'prev_video': 'up',       # 上一个视频
    'scroll_up': 'pageup',    # 向上滚动
    'scroll_down': 'pagedown', # 向下滚动
    'home': 'h',              # 回首页
    'refresh': 'r',           # 刷新

    # 音量（组合键）
    'volume_up': ['shift', '/'],   # 音量增加
    'volume_down': ['shift', '-'], # 音量减少

    # 播放进度
    'seek_forward': 'right',  # 快进
    'seek_backward': 'left',  # 快退

    # 别名（中文指令映射）
    '点赞': 'like',
    '收藏': 'collect',
    '关注': 'follow',
    '评论': 'comment',
    '分享': 'share',
    '暂停': 'pause',
    '播放': 'play',
    '继续播放': 'play',
    '继续': 'play',
    '下一个': 'next_video',
    '上一个': 'prev_video',
    '全屏': 'fullscreen',
    '网页全屏': 'web_fullscreen',
    '小窗': 'pip',
    '稍后再看': 'watch_later',
    '自动连播': 'auto_play',
    '清屏': 'clear_screen',
    '弹幕': 'danmaku',
    '不感兴趣': 'not_interested',
    '相关推荐': 'related',
    '作者主页': 'author_home',
    '复制口令': 'share_link',
    '上滑': 'prev_video',
    '下滑': 'next_video',
    '快进': 'seek_forward',
    '快退': 'seek_backward',
    '音量加': 'volume_up',
    '音量减': 'volume_down',
}


class DouyinController:
    """抖音键盘控制器"""

    def __init__(self, pyautogui_mode=True):
        """
        初始化抖音控制器

        Args:
            pyautogui_mode: 是否使用 pyautogui 发送按键
                           设置为 False 可用于测试/dry-run
        """
        self.pyautogui_mode = pyautogui_mode and PYAUTOGUI_AVAILABLE
        self.shortcuts = DOUYIN_SHORTCUTS

        if self.pyautogui_mode:
            pyautogui.PAUSE = 0.1  # 按键间隔 100ms
            pyautogui.FAILSAFE = True  # 鼠标移到角落终止

    def trigger(self, action: str) -> Tuple[bool, str]:
        """
        触发一个抖音操作

        Args:
            action: 操作名称（如 'like', 'comment', 'pause'）

        Returns:
            (success, message)
        """
        if not action:
            return False, '未指定抖音操作'

        action = action.strip().lower()

        # 解析别名（可能需要多次查找直到找到实际按键）
        key = None
        if action in self.shortcuts:
            key = self.shortcuts[action]
            # 如果结果仍然是别名，继续查找直到找到实际按键
            while key in self.shortcuts and isinstance(key, str):
                next_key = self.shortcuts[key]
                if next_key == key:  # 防止无限循环（自引用）
                    break
                key = next_key
        else:
            # 尝试模糊匹配
            key = self._find_similar_action(action)
            if not key:
                return False, f'未知的抖音操作: {action}'

        if not self.pyautogui_mode:
            return True, f'[DRY-RUN] 抖音操作 [{action}] -> {key}'

        try:
            # 处理组合键
            if isinstance(key, list):
                pyautogui.hotkey(*key)
            else:
                pyautogui.press(key)
            return True, f'抖音 [{action}] 已执行'
        except Exception as e:
            return False, f'执行抖音操作 [{action}] 失败: {e}'

    def trigger_sequence(self, actions: List[str], delay: float = 0.3) -> Tuple[bool, str]:
        """
        执行一系列抖音操作

        Args:
            actions: 操作列表
            delay: 每次操作间隔（秒）

        Returns:
            (success, message)
        """
        results = []
        for action in actions:
            success, msg = self.trigger(action)
            results.append(msg)
            if success:
                time.sleep(delay)

        all_success = all(success for success, _ in results)
        return all_success, '; '.join(results)

    def _find_similar_action(self, action: str) -> Optional[str]:
        """通过模糊匹配找到对应的快捷键"""
        from difflib import get_close_matches

        # 优先精确匹配中文别名
        for alias, key in self.shortcuts.items():
            if action == alias:
                return key

        # 尝试模糊匹配
        matches = get_close_matches(action, self.shortcuts.keys(), n=1, cutoff=0.6)
        if matches:
            return self.shortcuts[matches[0]]

        return None

    def is_available(self) -> bool:
        """检查 pyautogui 是否可用"""
        return self.pyautogui_mode

    @staticmethod
    def get_supported_actions() -> List[str]:
        """获取所有支持的操作列表"""
        # 去重返回
        seen = set()
        actions = []
        for key in DOUYIN_SHORTCUTS.keys():
            if key not in seen and not key.startswith('_'):
                seen.add(key)
                actions.append(key)
        return sorted(actions)
