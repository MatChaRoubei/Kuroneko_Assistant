"""模型选择设置窗口（tkinter，零额外依赖）。

提供可视化界面：检测 Ollama 已部署的模型，让用户手动选择，
选择结果持久化到 exe 旁的 model_selection.json，下次启动自动生效。
"""
import os
import sys
import json
import threading


from src.storage import data_directory, user_file, write_json
import queue


def get_app_root():
    return str(data_directory())


def get_selection_file():
    return user_file('model_selection.json')


def load_selected_model(default=''):
    """读取用户手动选择的模型名，未选择返回空字符串"""
    try:
        # New settings are one atomic document; keep reading legacy selections.
        from src.storage import read_json
        settings = read_json(get_settings_file(), {})
        if isinstance(settings, dict) and 'ollama_model' in settings:
            return str(settings['ollama_model'] or '').strip()
        path = get_selection_file()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return (data.get('ollama_model') or '').strip()
    except Exception:
        pass
    return default or ''


def save_selected_model(model):
    """保存用户选择的模型名"""
    try:
        path = get_selection_file()
        write_json(path, {'ollama_model': model})
        return True
    except Exception:
        return False


def list_ollama_models():
    """检测 Ollama 已部署的模型，返回模型名列表"""
    try:
        import ollama
        models = ollama.Client(timeout=3).list()
        return [m.model for m in (models.models or [])]
    except Exception:
        return []


def show_model_selector():
    show_settings()


class MainWindow:
    """All Tk calls stay on the UI thread; workers only enqueue updates."""
    def __init__(self, on_submit=None, on_pause=None, on_retry=None, on_speak=None):
        self.root = None
        self.text = None
        self._events = queue.Queue()
        self._on_submit = on_submit
        self._on_pause = on_pause
        self._on_retry = on_retry
        self._on_speak = on_speak
        self._ready = False
        self._busy = False
        self._tray_available = False
        self._settings = None
        self._stream_start = None
        self._history = []
        self._history_index = 0
        self._draft = ''
        self._capturing = False

    def run(self):
        import tkinter as tk
        from tkinter import ttk
        from src.lifecycle import shutdown, request_exit
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.title('黑猫语音助手 · 0.0.9')
        width = min(900, self.root.winfo_screenwidth() - 80)
        height = min(640, self.root.winfo_screenheight() - 100)
        self.root.geometry(f'{width}x{height}+30+30')
        self.root.minsize(660, 480)
        self.root.configure(bg='#f2f5fa')
        try:
            from src.config import get_resource_root
            self.root.iconbitmap(os.path.join(get_resource_root(), 'icon.ico'))
        except Exception:
            pass
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TButton', font=('Microsoft YaHei', 10), padding=(12, 7))
        style.configure('Accent.TButton', background='#345ce6', foreground='white')
        header = tk.Frame(self.root, bg='#f2f5fa')
        header.pack(fill='x', padx=24, pady=(20, 12))
        tk.Label(header, text='黑猫语音助手', font=('Microsoft YaHei', 19, 'bold'),
                 fg='#18253b', bg='#f2f5fa').pack(side='left')
        ttk.Button(header, text='设置', command=show_settings).pack(side='right')
        self.status_label = tk.Label(self.root, text='正在准备，请稍候…', anchor='w',
                                     bg='#f2f5fa', fg='#345ce6', font=('Microsoft YaHei', 10))
        self.status_label.pack(fill='x', padx=26, pady=(0, 10))
        frame = tk.Frame(self.root, bg='white', highlightbackground='#dce3ef', highlightthickness=1)
        frame.pack(fill='both', expand=True, padx=24)
        self.text = tk.Text(frame, bg='white', fg='#263348', font=('Microsoft YaHei', 11),
                            wrap='word', relief='flat', padx=18, pady=16, spacing3=8, state='disabled', height=8, width=1)
        scroll = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        for tag, color in [('asr', '#345ce6'), ('out', '#263348'), ('sys', '#65748c')]:
            self.text.tag_configure(tag, foreground=color)
        entry_row = tk.Frame(self.root, bg='#f2f5fa')
        entry_row.pack(fill='x', padx=24, pady=(14, 8))
        self.entry = ttk.Entry(entry_row, font=('Microsoft YaHei', 11))
        self.entry.pack(side='left', fill='x', expand=True, ipady=8)
        self.entry.bind('<Return>', self._submit)
        self.entry.bind('<Up>', lambda event: self._recall(-1))
        self.entry.bind('<Down>', lambda event: self._recall(1))
        self.send_button = ttk.Button(entry_row, text='发送', style='Accent.TButton', command=self._submit)
        self.send_button.pack(side='left', padx=(10, 0))
        self.speak_button = ttk.Button(entry_row, text='说一句 (F8)', command=self._speak_once)
        self.speak_button.pack(side='left', padx=(8, 0))
        self.speak_button.state(['disabled'])
        self.send_button.state(['disabled'])
        controls = tk.Frame(self.root, bg='#f2f5fa')
        controls.pack(fill='x', padx=24, pady=(0, 12))
        from src.lifecycle import voice_paused
        self.pause_button = ttk.Button(controls, text='恢复监听' if voice_paused.is_set() else '暂停监听', command=self._pause)
        self.pause_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='停止回答 (Esc)', command=self._stop)
        self.stop_button.pack(side='left', padx=6)
        self.stop_button.state(['disabled'])
        ttk.Button(controls, text='重试麦克风', command=self._retry_microphone).pack(side='left')
        ttk.Button(controls, text='清空对话显示', command=self._on_clear).pack(side='right')
        ttk.Button(controls, text='退出', command=request_exit).pack(side='right', padx=6)
        self.input_hint = tk.Label(self.root, text='正在准备输入…', anchor='w',
                                   bg='#f2f5fa', fg='#65748c', font=('Microsoft YaHei', 9))
        self.input_hint.pack(fill='x', padx=26, pady=(0, 14))
        self.root.bind('<Escape>', lambda event: self._stop())
        self.root.bind('<F8>', lambda event: self._speak_once())
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.append_system('欢迎使用。可以说“唤醒词 + 指令”，也可以直接在下方输入。\n试试：现在几点、打开记事本、搜索一下天气。')
        self.root.after(30, self._drain)
        self.root.mainloop()
        request_exit()

    def _drain(self):
        from src.lifecycle import shutdown
        if shutdown.is_set():
            self.root.destroy()
            return
        # Bound each frame's work so bursts cannot starve input/paint events.
        for _ in range(80):
            try:
                callback = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                print(f'[GUI] 更新失败: {exc}')
        self.root.after(30, self._drain)

    def _post(self, callback):
        self._events.put(callback)

    def _submit(self, event=None):
        query = self.entry.get().strip()
        if query and self._ready and not self._busy and self._on_submit:
            if self._on_submit(query):
                if not self._history or self._history[-1] != query:
                    self._history.append(query)
                    self._history = self._history[-50:]
                self._history_index = len(self._history)
                self._draft = ''
                self.entry.delete(0, 'end')
                self.entry.focus_set()
        elif query and self._busy:
            self.input_hint.configure(text='当前回答结束后可发送；也可以先按 Esc 停止。输入内容会保留。')
        return 'break'

    def _recall(self, direction):
        if not self._history:
            return 'break'
        if self._history_index == len(self._history):
            self._draft = self.entry.get()
        self._history_index = max(0, min(len(self._history), self._history_index + direction))
        text = self._draft if self._history_index == len(self._history) else self._history[self._history_index]
        self.entry.delete(0, 'end')
        self.entry.insert(0, text)
        self.entry.icursor('end')
        return 'break'

    def _speak_once(self):
        if self._ready and not self._busy and self._on_speak:
            self._on_speak()
        return 'break'

    def _retry_microphone(self):
        from src.lifecycle import voice_paused
        voice_paused.clear()
        self.pause_button.configure(text='暂停监听')
        if self._on_retry:
            self._on_retry()

    def _pause(self):
        from src.lifecycle import voice_paused, cancel_capture
        if voice_paused.is_set():
            voice_paused.clear()
        else:
            voice_paused.set()
            cancel_capture()
        self.pause_button.configure(text='恢复监听' if voice_paused.is_set() else '暂停监听')
        if self._on_pause:
            self._on_pause()

    def _stop(self):
        if self._capturing:
            from src.lifecycle import cancel_capture
            cancel_capture()
            self.set_status('已取消录音。')
        if not self._busy:
            return
        from src.stop import request_stop
        request_stop()
        self.stop_button.state(['disabled'])
        self.set_status('正在停止回答…')

    def _on_clear(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self._stream_start = None
        self.text.configure(state='disabled')

    def _on_close(self):
        if self._tray_available:
            self.root.withdraw()
        else:
            from src.lifecycle import request_exit
            request_exit()

    def show(self):
        def restore():
            self.root.deiconify()
            self.root.lift()
            self.entry.focus_set()
        self._post(restore)

    def set_ready(self, ready=True):
        self._post(lambda: self._set_controls(ready, self._busy))

    def set_busy(self, busy):
        self._post(lambda: self._set_controls(self._ready, busy))

    def set_capturing(self, capturing):
        def update():
            self._capturing = capturing
            self._set_controls(self._ready, self._busy)
            self.speak_button.configure(text='正在听…' if capturing else '说一句 (F8)')
        self._post(update)

    def _set_controls(self, ready, busy):
        just_ready = ready and not self._ready
        self._ready, self._busy = ready, busy
        self.send_button.state(['!disabled'] if ready and not busy else ['disabled'])
        self.speak_button.state(['!disabled'] if ready and not busy and not self._capturing else ['disabled'])
        self.send_button.configure(text='处理中…' if busy else '发送')
        self.stop_button.state(['!disabled'] if busy or self._capturing else ['disabled'])
        self.input_hint.configure(text='可先输入下一条，按 Esc 停止当前回答后发送。' if busy else
                                  '回车发送 · ↑↓ 找回最近输入 · AI 聊天需在设置中选择模型' if ready else '正在准备输入…')
        if just_ready:
            self.entry.focus_set()

    def set_status(self, text):
        self._post(lambda: self.status_label.configure(text=text))

    def append_asr(self, text):
        self._post(lambda: self._insert(f'你：{text}\n', 'asr'))

    def append_output(self, text):
        def finish():
            if self._stream_start is not None:
                self.text.configure(state='normal')
                self.text.delete(self._stream_start, 'end-1c')
                self.text.configure(state='disabled')
                self._stream_start = None
            self._insert(f'黑猫：{text}\n', 'out')
        self._post(finish)

    def stream_output(self, piece):
        def update():
            if self._stream_start is None:
                self._stream_start = 'live_start'
                self.text.mark_set('live_start', 'end-1c')
                self.text.mark_gravity('live_start', 'left')
                self._insert('黑猫：', 'out')
            self._insert(piece, 'out')
        self._post(update)

    def append_system(self, text):
        self._post(lambda: self._insert(f'{text}\n', 'sys'))

    def _insert(self, line, tag):
        at_bottom = self.text.yview()[1] >= 0.98
        self.text.configure(state='normal')
        self.text.insert('end', line, tag)
        lines = int(self.text.index('end-1c').split('.')[0])
        if lines > 1800:
            self.text.delete('1.0', f'{lines - 1500}.0')
        self.text.configure(state='disabled')
        if at_bottom:
            self.text.see('end')


def ask_confirmation(message):
    """Run an action confirmation on Tk's owner thread with a bounded wait."""
    if _ui is None:
        return False
    completed = threading.Event()
    result = [False]
    def ask():
        from tkinter import messagebox
        _ui.root.deiconify()
        result[0] = messagebox.askyesno('确认系统操作', message, parent=_ui.root)
        completed.set()
    _ui._post(ask)
    completed.wait(120)
    return result[0]


# 运行时设置（唤醒词、停止词、灵敏度、TTS 引擎）
_runtime = {
    'wake_words': ['你好黑猫', '助手', '黑猫', '你好助手'],
    'sensitivity': 0.8,
    'tts_engine': 'vits',                     # 默认 vits：本地离线神经语音，零网络延迟、无需注册/卡号
    'tts_voice': 'zh-CN-XiaoxiaoNeural',
    'tts_style': 'calm',
    'tts_rate': '-5%',                        # 语速：轻微放慢更从容；过慢(-12%)会显人机
    'tts_pitch': '+0Hz',                      # 音调：edge_tts 用 Hz(+0Hz 中性)；非负百分比如 0% 会被拒
    'tts_azure_key': '',                     # Azure(微软)语音合成订阅密钥
    'tts_azure_region': 'chinaeast2',        # Azure 区域(需与订阅资源一致)
    'tts_azure_voice': 'zh-CN-XiaoxiaoNeural',  # Azure 神经语音音色
    'stop_words': ['停止', '停下', '闭嘴', '别说了', '打住'],
    'microphone': None,
    'energy_threshold': 0.02,
    'continuous_dialogue': False,
    'character_voice': {'url': 'http://127.0.0.1:9880', 'ref_audio_path': '', 'prompt_text': '',
                        'prompt_lang': 'ja', 'text_lang': 'auto', 'speed_factor': 1.0},
    'japanese_catchphrases': True,
}

# edge-tts 声音角色（显示名 -> voice id）
VOICE_OPTIONS = {
    '晓晓（女，温柔）': 'zh-CN-XiaoxiaoNeural',
    '晓伊（女，活泼）': 'zh-CN-XiaoyiNeural',
    '云希（男，阳光）': 'zh-CN-YunxiNeural',
    '云扬（男，沉稳）': 'zh-CN-YunyangNeural',
}


def get_settings_file():
    return os.path.join(get_app_root(), 'settings.json')


def load_settings(config=None):
    """启动时读取设置，覆盖默认值"""
    global _runtime
    if config:
        for key in _runtime:
            if key in config:
                _runtime[key] = config[key]
        if 'wake_word_sensitivity' in config:
            _runtime['sensitivity'] = config['wake_word_sensitivity']
    try:
        path = get_settings_file()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'wake_words' in data:
                ww = data['wake_words']
                if isinstance(ww, str):
                    ww = [w.strip() for w in ww.replace('，', ',').split(',') if w.strip()]
                if isinstance(ww, list) and ww and all(isinstance(word, str) and word.strip() for word in ww):
                    _runtime['wake_words'] = [word.strip() for word in ww]
            if 'sensitivity' in data:
                try:
                    value = float(data['sensitivity'])
                    if 0.1 <= value <= 1:
                        _runtime['sensitivity'] = value
                except Exception:
                    pass
            if data.get('tts_engine') in ('vits', 'edge', 'pyttsx3', 'azure', 'auto', 'gptsovits'):
                _runtime['tts_engine'] = data['tts_engine']
            if isinstance(data.get('ollama_model'), str):
                _runtime['ollama_model'] = data['ollama_model']
            if data.get('microphone') is None or (isinstance(data.get('microphone'), dict) and
                    all(isinstance(data['microphone'].get(key), str) for key in ('name', 'hostapi'))):
                _runtime['microphone'] = data.get('microphone')
            if isinstance(data.get('energy_threshold'), (float, int)) and 0.001 <= data['energy_threshold'] <= 0.2:
                _runtime['energy_threshold'] = data['energy_threshold']
            if isinstance(data.get('continuous_dialogue'), bool):
                _runtime['continuous_dialogue'] = data['continuous_dialogue']
            if isinstance(data.get('character_voice'), dict):
                from src.character_tts import DEFAULT_PROFILE
                _runtime['character_voice'] = {**DEFAULT_PROFILE, **data['character_voice']}
            if isinstance(data.get('japanese_catchphrases'), bool):
                _runtime['japanese_catchphrases'] = data['japanese_catchphrases']
            if 'tts_voice' in data and data['tts_voice']:
                _runtime['tts_voice'] = data['tts_voice']
            if 'tts_style' in data and data['tts_style']:
                _runtime['tts_style'] = data['tts_style']
            if 'tts_rate' in data and data['tts_rate']:
                _runtime['tts_rate'] = data['tts_rate']
            if 'tts_pitch' in data and data['tts_pitch']:
                _runtime['tts_pitch'] = data['tts_pitch']
            if 'tts_azure_key' in data:
                _runtime['tts_azure_key'] = data['tts_azure_key']
            if 'tts_azure_region' in data and data['tts_azure_region']:
                _runtime['tts_azure_region'] = data['tts_azure_region']
            if 'tts_azure_voice' in data and data['tts_azure_voice']:
                _runtime['tts_azure_voice'] = data['tts_azure_voice']
            if 'stop_words' in data:
                sw = data['stop_words']
                if isinstance(sw, str):
                    sw = [w.strip() for w in sw.replace('，', ',').split(',') if w.strip()]
                if isinstance(sw, list) and sw and all(isinstance(word, str) and word.strip() for word in sw):
                    _runtime['stop_words'] = sw
    except Exception:
        pass


def save_settings(wake_words, sensitivity, tts_engine, tts_voice=None,
                  tts_style='calm', tts_rate='-5%', tts_pitch='+0Hz',
                  tts_azure_key='', tts_azure_region='chinaeast2',
                  tts_azure_voice='zh-CN-XiaoxiaoNeural', ollama_model=None, stop_words=None,
                  microphone='unchanged', energy_threshold=None, continuous_dialogue=None,
                  character_voice=None, japanese_catchphrases=None):
    """保存设置并更新运行时"""
    global _runtime
    candidate = _runtime.copy()
    candidate['wake_words'] = wake_words
    candidate['sensitivity'] = sensitivity
    candidate['tts_engine'] = tts_engine
    if tts_voice:
        candidate['tts_voice'] = tts_voice
    candidate['tts_style'] = tts_style
    candidate['tts_rate'] = tts_rate
    candidate['tts_pitch'] = tts_pitch
    candidate['tts_azure_key'] = tts_azure_key
    candidate['tts_azure_region'] = tts_azure_region
    candidate['tts_azure_voice'] = tts_azure_voice
    if ollama_model is not None:
        candidate['ollama_model'] = ollama_model
    if stop_words is not None:
        candidate['stop_words'] = stop_words
    if microphone != 'unchanged':
        candidate['microphone'] = microphone
    if energy_threshold is not None:
        candidate['energy_threshold'] = energy_threshold
    if continuous_dialogue is not None:
        candidate['continuous_dialogue'] = continuous_dialogue
    if character_voice is not None:
        candidate['character_voice'] = character_voice
    if japanese_catchphrases is not None:
        candidate['japanese_catchphrases'] = japanese_catchphrases
    try:
        write_json(get_settings_file(), candidate)
        _runtime = candidate
        return True
    except Exception:
        return False


def get_wake_words():
    return _runtime.get('wake_words', ['你好黑猫'])


def get_sensitivity():
    return _runtime.get('sensitivity', 0.8)


def get_stop_words():
    return _runtime.get('stop_words', ['停止', '停下', '闭嘴', '别说了', '打住'])


def get_tts_engine():
    return _runtime.get('tts_engine', 'vits')


def get_character_voice():
    return dict(_runtime.get('character_voice', {}))


def get_tts_voice():
    return _runtime.get('tts_voice', 'zh-CN-XiaoxiaoNeural')


def get_tts_style():
    return _runtime.get('tts_style', 'calm')


def get_tts_rate():
    return _runtime.get('tts_rate', '-5%')


def get_tts_pitch():
    return _runtime.get('tts_pitch', '+0Hz')


def get_tts_azure_key():
    return _runtime.get('tts_azure_key', '')


def get_tts_azure_region():
    return _runtime.get('tts_azure_region', 'chinaeast2')


def get_tts_azure_voice():
    return _runtime.get('tts_azure_voice', 'zh-CN-XiaoxiaoNeural')


def show_settings():
    if _ui is None:
        return
    def open_once():
        _ui.root.deiconify()
        if _ui._settings is not None and _ui._settings.winfo_exists():
            _ui._settings.lift()
        else:
            from src.settings_ui import SettingsWindow
            dialog = SettingsWindow(_ui)
            _ui._settings = dialog.window
    _ui._post(open_once)


_ui = None


def start_main_window(on_submit=None, on_pause=None, on_retry=None, on_speak=None):
    """Create the window on the process main thread."""
    global _ui
    _ui = MainWindow(on_submit, on_pause, on_retry, on_speak)
    return _ui


def show_window():
    """显示主窗口（托盘点击/菜单调用）"""
    global _ui
    if _ui is not None:
        _ui.show()


def set_status(text):
    if _ui:
        _ui.set_status(text)


def append_asr(text):
    if _ui:
        _ui.append_asr(text)


def append_output(text):
    if _ui:
        _ui.append_output(text)


def append_system(text):
    if _ui:
        _ui.append_system(text)


def stream_output(piece):
    if _ui:
        _ui.stream_output(piece)
