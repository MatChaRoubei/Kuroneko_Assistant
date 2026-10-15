"""Persisted, cancellable reminders with one scheduler instead of per-alarm threads."""
import math
import threading
import time

from src.storage import read_json, user_file, write_json


class ReminderManager:
    def __init__(self, path, deliver, clock=time.time, stop_event=None):
        self.path, self.deliver, self.clock = path, deliver, clock
        self.stop_event = stop_event or threading.Event()
        self.lock = threading.RLock()
        state = read_json(path, {})
        state = state if isinstance(state, dict) else {}
        records = state.get('items', [])
        records = records if isinstance(records, list) else []
        self.items = [item for item in records if isinstance(item, dict)
                      and isinstance(item.get('id'), int) and isinstance(item.get('due'), (int, float))
                      and math.isfinite(item['due']) and isinstance(item.get('title'), str)]
        self.next_id = max([item['id'] for item in self.items] + [0]) + 1
        if isinstance(state.get('next_id'), int):
            self.next_id = max(self.next_id, state['next_id'])
        self.worker = None

    def _persist(self, items, next_id):
        write_json(self.path, {'items': items, 'next_id': next_id})

    def add(self, seconds, title='时间到'):
        try:
            seconds = float(seconds)
        except (ValueError, TypeError):
            return False, '请说明多久后提醒，例如“五分钟后提醒我喝水”。'
        if not math.isfinite(seconds) or not 1 <= seconds <= 30 * 86400:
            return False, '提醒时长需要在 1 秒到 30 天之间。'
        with self.lock:
            if len(self.items) >= 100:
                return False, '最多保留 100 条提醒，请先取消不需要的提醒。'
            item = {'id': self.next_id, 'due': self.clock() + seconds, 'title': str(title or '时间到')[:300]}
            updated = self.items + [item]
            try:
                self._persist(updated, self.next_id + 1)
            except OSError:
                return False, '提醒保存失败，未创建提醒，请检查用户目录权限。'
            self.items, self.next_id = updated, self.next_id + 1
        return True, f'已创建第 {item["id"]} 号提醒，{format_duration(seconds)}后提醒你：{item["title"]}'

    def list(self):
        with self.lock:
            return [dict(item) for item in sorted(self.items, key=lambda item: item['due'])]

    def describe(self):
        items = self.list()
        if not items:
            return True, '目前没有待办提醒。'
        lines = [f'第 {item["id"]} 号：{item["title"]}，' +
                 (f'还剩{format_duration(max(0, item["due"] - self.clock()))}' if item['due'] > self.clock() else '已到时间，正在通知')
                 for item in items[:10]]
        return True, '；'.join(lines) + (f'。共有 {len(items)} 条提醒。' if len(items) > 10 else '')

    def cancel(self, target='latest'):
        with self.lock:
            if not self.items:
                return False, '目前没有可以取消的提醒。'
            if target == 'all':
                updated = []
            else:
                try:
                    identifier = max(item['id'] for item in self.items) if target == 'latest' else int(target)
                except (ValueError, TypeError):
                    return False, '请说“取消最后一个提醒”或“取消第 2 号提醒”。'
                updated = [item for item in self.items if item['id'] != identifier]
                if len(updated) == len(self.items):
                    return False, f'没有找到第 {identifier} 号提醒。'
            try:
                self._persist(updated, self.next_id)
            except OSError:
                return False, '取消失败，原提醒已保留。'
            count = len(self.items) - len(updated)
            self.items = updated
        return True, f'已取消 {count} 条提醒。'

    def tick(self):
        with self.lock:
            due = [item for item in self.items if item['due'] <= self.clock()]
            if not due:
                return
            remaining = [item for item in self.items if item['due'] > self.clock()]
            self._persist(remaining, self.next_id)
            self.items = remaining
        for item in due:
            try:
                self.deliver(dict(item))
            except Exception as exc:
                print(f'[提醒] 通知失败: {exc}')

    def start(self):
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return
            def work():
                while not self.stop_event.wait(0.25):
                    try:
                        self.tick()
                    except OSError as exc:
                        print(f'[提醒] 暂时无法保存状态: {exc}')
                        self.stop_event.wait(2)
            self.worker = threading.Thread(target=work, daemon=True, name='reminders')
            self.worker.start()


def format_duration(seconds):
    seconds = max(0, math.ceil(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return ''.join(f'{value}{unit}' for value, unit in ((hours, '小时'), (minutes, '分钟'), (seconds, '秒')) if value) or '0秒'


def deliver_reminder(item):
    from src.gui import append_system
    from src.feedback import notify, say
    from src.lifecycle import busy, cancel_capture
    from src.stop import OUTPUT_DONE, STOP_REQUESTED
    message = f'第 {item["id"]} 号提醒：{item["title"]}'
    append_system(message)
    notify('黑猫提醒', message)
    try:
        import winsound
        winsound.MessageBeep()
    except (ImportError, RuntimeError):
        pass
    if not busy.is_set() and OUTPUT_DONE.is_set():
        STOP_REQUESTED.clear()
        cancel_capture()
        say(message)


_manager = None
_manager_lock = threading.Lock()


def get_reminders():
    global _manager
    with _manager_lock:
        if _manager is None:
            from src.lifecycle import shutdown
            _manager = ReminderManager(user_file('reminders.json'), deliver_reminder, stop_event=shutdown)
            _manager.start()
        return _manager
