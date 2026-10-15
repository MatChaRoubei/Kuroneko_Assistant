"""Settings arranged around user tasks, with optional services kept separate."""
import threading
import tkinter as tk
from tkinter import ttk, messagebox


ENGINE_OPTIONS = {
    '本地自然语音（离线）': 'vits',
    '自动选择可用声音': 'auto',
    '微软在线语音（需要网络）': 'edge',
    'Windows 系统声音（离线）': 'pyttsx3',
    'Azure 订阅语音': 'azure',
    '角色声音（GPT-SoVITS 本地服务）': 'gptsovits',
}
ENGINE_HINTS = {
    'vits': '使用随程序提供的本地模型，无需联网。',
    'auto': '优先本地自然语音，失败时自动尝试其他可用声音。',
    'edge': '无需订阅密钥；网络不可用时会尝试本地声音。',
    'pyttsx3': '使用电脑已安装的系统声音，不需要语音模型或网络。',
    'azure': '需要语音服务订阅。请在“高级”页填写密钥和区域。',
    'gptsovits': '需要单独运行本机 GPT-SoVITS 服务，并在“角色声音”页配置参考音频。音色取决于模型与素材。',
}


def model_choices(current, detected):
    """Refreshing a disconnected server must never silently clear a choice."""
    choices = ['自动选择'] + list(dict.fromkeys(detected))
    if current and current != '自动选择' and current not in choices:
        choices.append(current)
    return choices


class SettingsWindow:
    def __init__(self, ui):
        from src import gui
        from src.feedback import EDGE_STYLE_OPTIONS
        self.ui = ui
        self.gui = gui
        self.window = tk.Toplevel(ui.root)
        root = self.window
        root.title('黑猫 · 设置')
        root.geometry('580x570')
        root.minsize(480, 490)
        root.transient(ui.root)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Escape>', lambda event: self.close())
        values = gui._runtime.copy()
        from src.character_tts import DEFAULT_PROFILE
        character = {**DEFAULT_PROFILE, **values.get('character_voice', {})}
        engine = next((label for label, value in ENGINE_OPTIONS.items() if value == values['tts_engine']), '自动选择可用声音')
        voice = next((label for label, value in gui.VOICE_OPTIONS.items() if value == values['tts_voice']), next(iter(gui.VOICE_OPTIONS)))
        style = next((label for label, value in EDGE_STYLE_OPTIONS.items() if value == values['tts_style']), next(iter(EDGE_STYLE_OPTIONS)))
        defaults = {
            'wake': '，'.join(values['wake_words']),
            'stop': '，'.join(values['stop_words']),
            'engine': engine, 'voice': voice, 'style': style,
            'rate': values['tts_rate'], 'pitch': values['tts_pitch'],
            'model': gui.load_selected_model() or '自动选择',
            'key': values['tts_azure_key'], 'region': values['tts_azure_region'],
            'azure_voice': values['tts_azure_voice'],
            'microphone': (f'{values["microphone"]["name"]} · {values["microphone"]["hostapi"]}' if values.get('microphone') else 'Windows 默认麦克风'),
            'energy': str(values.get('energy_threshold', 0.02)),
            'role_url': character['url'], 'role_audio': character['ref_audio_path'],
            'role_text': character['prompt_text'], 'role_language': character['prompt_lang'],
            'role_speed': str(character['speed_factor']),
        }
        self.fields = {key: tk.StringVar(root, value=value) for key, value in defaults.items()}
        self.sensitivity = tk.DoubleVar(root, value=values['sensitivity'])
        self.continuous = tk.BooleanVar(root, value=values.get('continuous_dialogue', False))
        self.catchphrases = tk.BooleanVar(root, value=values.get('japanese_catchphrases', True))
        self.microphones = {}
        self._voice_probe_running = False
        self._initial = self.snapshot()
        ttk.Label(root, text='让黑猫更适合你的使用习惯', font=('Microsoft YaHei', 14, 'bold')).pack(anchor='w', padx=22, pady=(18, 12))
        footer = ttk.Frame(root, padding=(18, 12))
        footer.pack(side='bottom', fill='x')
        self.message = tk.StringVar(root)
        ttk.Label(footer, textvariable=self.message, wraplength=340).pack(side='left', fill='x', expand=True)
        ttk.Button(footer, text='保存并应用', command=self.apply).pack(side='right')
        ttk.Button(footer, text='关闭', command=self.close).pack(side='right', padx=8)
        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True, padx=18, pady=(0, 6))
        self.tabs = {}
        self.tab_canvases = {}
        for name in ('常用', '语音输入', '声音', '角色声音', 'AI 对话', '高级'):
            holder = ttk.Frame(notebook)
            canvas = tk.Canvas(holder, highlightthickness=0)
            scrollbar = ttk.Scrollbar(holder, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            canvas.pack(side='left', fill='both', expand=True)
            tab = ttk.Frame(canvas, padding=18)
            item = canvas.create_window((0, 0), window=tab, anchor='nw')
            tab.bind('<Configure>', lambda event, c=canvas: c.configure(scrollregion=c.bbox('all')))
            canvas.bind('<Configure>', lambda event, c=canvas, i=item: c.itemconfigure(i, width=event.width))
            tab.columnconfigure(0, weight=1)
            notebook.add(holder, text=name)
            self.tabs[name] = tab
            self.tab_canvases[str(holder)] = canvas
        def scroll(event):
            canvas = self.tab_canvases.get(notebook.select())
            if canvas and canvas.bbox('all') and canvas.bbox('all')[3] > canvas.winfo_height():
                canvas.yview_scroll(-int(event.delta / 120), 'units')
        root.bind('<MouseWheel>', scroll)

        self.field('常用', '唤醒词', 'wake', hint='多个词用逗号分隔。说“黑猫现在几点”即可下达指令。')
        self.field('常用', '停止词', 'stop', hint='回答时说这些词可以停止，也可以随时按 Esc。')
        tab = self.tabs['常用']
        ttk.Label(tab, text='唤醒匹配严格程度').pack(anchor='w', pady=(8, 4))
        ttk.Scale(tab, from_=0.1, to=1, variable=self.sensitivity).pack(fill='x')
        ttk.Label(tab, text='向左更容易唤醒，向右更严格；容易误唤醒时向右调。', wraplength=440).pack(anchor='w', pady=8)
        ttk.Label(tab, text='无需修改这些设置也能使用。\n主窗口可暂停监听，文字输入仍然可用。', wraplength=440).pack(anchor='w', pady=14)

        self.microphone_combo = self.field('语音输入', '使用的麦克风', 'microphone', [defaults['microphone'], 'Windows 默认麦克风'])
        self.microphone_status = tk.StringVar(root, value='正在检测麦克风…')
        ttk.Label(self.tabs['语音输入'], textvariable=self.microphone_status, wraplength=440).pack(anchor='w', pady=6)
        self.microphone_refresh = ttk.Button(self.tabs['语音输入'], text='刷新设备', command=self.refresh_microphones)
        self.microphone_refresh.pack(anchor='w', pady=(0, 10))
        self.field('语音输入', '录音能量阈值', 'energy', hint='默认 0.02。轻声检测不到时调低，背景噪音频繁触发时调高；范围 0.001～0.2。')
        ttk.Checkbutton(self.tabs['语音输入'], text='回复后允许连续说话，无需重复唤醒', variable=self.continuous).pack(anchor='w', pady=8)
        ttk.Label(self.tabs['语音输入'], text='连续对话等待 8 秒，静音后回到唤醒模式。\n也可说“结束对话”。默认关闭，适合安静环境。\n主窗口“说一句”或 F8 只录制一条指令。', wraplength=440).pack(anchor='w', pady=8)

        self.engine_combo = self.field('声音', '播报方式', 'engine', list(ENGINE_OPTIONS))
        self.engine_hint = tk.StringVar(root)
        ttk.Label(self.tabs['声音'], textvariable=self.engine_hint, wraplength=440).pack(anchor='w', pady=(0, 10))
        self.voice_combo = self.field('声音', '在线声音角色', 'voice', list(gui.VOICE_OPTIONS))
        self.rate_combo = self.field('声音', '在线语速', 'rate', ['-20%', '-15%', '-10%', '-5%', '+0%', '+5%', '+10%', '+15%', '+20%'])
        self.pitch_combo = self.field('声音', '在线音调', 'pitch', ['-10Hz', '-6Hz', '-3Hz', '+0Hz', '+3Hz', '+6Hz', '+10Hz'])
        self.voice_status = tk.StringVar(root, value='检测会实际生成音频，不播放、不切换播报方式。')
        self.probe_button = ttk.Button(self.tabs['声音'], text='检测当前声音是否可用', command=self.probe_voice)
        self.probe_button.pack(anchor='w', pady=4)
        ttk.Button(self.tabs['声音'], text='保存并试听当前声音', command=self.preview_voice).pack(anchor='w', pady=4)
        ttk.Label(self.tabs['声音'], textvariable=self.voice_status, wraplength=440).pack(anchor='w', pady=6)

        self.field('角色声音', 'GPT-SoVITS 服务地址', 'role_url')
        self.field('角色声音', '参考音频路径（服务所在电脑的文件）', 'role_audio')
        from tkinter import filedialog
        def choose_audio():
            path = filedialog.askopenfilename(parent=root, title='选择清晰参考音频', filetypes=[('音频', '*.wav *.mp3 *.flac'), ('所有文件', '*.*')])
            if path:
                self.fields['role_audio'].set(path)
        ttk.Button(self.tabs['角色声音'], text='选择参考音频', command=choose_audio).pack(anchor='w', pady=(0, 6))
        self.field('角色声音', '参考音频对应台词', 'role_text')
        row = ttk.Frame(self.tabs['角色声音'])
        row.pack(fill='x', pady=4)
        ttk.Label(row, text='参考语言').pack(side='left')
        ttk.Combobox(row, textvariable=self.fields['role_language'], values=['ja', 'zh', 'en'], state='readonly', width=6).pack(side='left', padx=8)
        ttk.Label(row, text='语速 0.7～1.3').pack(side='left')
        ttk.Entry(row, textvariable=self.fields['role_speed'], width=6).pack(side='left', padx=8)
        ttk.Checkbutton(self.tabs['角色声音'], text='中文为主，偶尔使用简短日语口头禅', variable=self.catchphrases).pack(anchor='w', pady=8)
        ttk.Label(self.tabs['角色声音'], text='此安装包未包含 GPT-SoVITS 服务、角色模型或参考音频。\n请先运行本机 api_v2.py；只填写地址不会自动安装或启动服务。', wraplength=440).pack(anchor='w', pady=6)
        ttk.Button(self.tabs['角色声音'], text='检查角色服务与合成', command=lambda: self.probe_voice('gptsovits')).pack(anchor='w', pady=4)
        ttk.Label(self.tabs['角色声音'], textvariable=self.voice_status, wraplength=440).pack(anchor='w', pady=4)
        ttk.Button(self.tabs['角色声音'], text='保存并试听角色声音', command=self.preview_character).pack(anchor='w', pady=6)

        ttk.Label(self.tabs['AI 对话'], text='AI 聊天是可选功能', font=('Microsoft YaHei', 12, 'bold')).pack(anchor='w', pady=(0, 8))
        ttk.Label(self.tabs['AI 对话'], text='系统指令和本地语音无需 Ollama。\n如需自由聊天，请在这台电脑安装、启动 Ollama，并下载聊天模型。', wraplength=440).pack(anchor='w', pady=(0, 16))
        self.model_combo = self.field('AI 对话', '使用的模型', 'model', model_choices(defaults['model'], []))
        self.model_status = tk.StringVar(root, value='准备检测本机模型…')
        ttk.Label(self.tabs['AI 对话'], textvariable=self.model_status, wraplength=440).pack(anchor='w', pady=8)
        self.refresh_button = ttk.Button(self.tabs['AI 对话'], text='重新检测', command=self.refresh_models)
        self.refresh_button.pack(anchor='w', pady=8)

        ttk.Label(self.tabs['高级'], text='仅使用 Azure 订阅语音时需要填写', wraplength=440).pack(anchor='w', pady=(0, 10))
        self.azure_fields = [
            self.field('高级', '订阅密钥', 'key', secret=True),
            self.field('高级', '服务区域', 'region', hint='与订阅资源一致，例如 japaneast、eastus 或 chinaeast2；程序按区域选择云端地址。'),
            self.field('高级', 'Azure 音色', 'azure_voice'),
            self.field('高级', '表达风格', 'style', list(EDGE_STYLE_OPTIONS)),
        ]
        self.engine_combo.bind('<<ComboboxSelected>>', lambda event: self.update_applicability())
        self.update_applicability()
        self.refresh_models()
        self.refresh_microphones()

    def field(self, tab, label, key, choices=None, hint='', secret=False):
        frame = ttk.Frame(self.tabs[tab])
        frame.pack(fill='x', pady=(0, 10))
        ttk.Label(frame, text=label).pack(anchor='w', pady=(0, 4))
        if choices is None:
            widget = ttk.Entry(frame, textvariable=self.fields[key], show='*' if secret else '')
        else:
            widget = ttk.Combobox(frame, textvariable=self.fields[key], values=choices, state='readonly')
        widget.pack(fill='x', ipady=3)
        if hint:
            ttk.Label(frame, text=hint, wraplength=440).pack(anchor='w', pady=(4, 0))
        return widget

    def snapshot(self):
        return {**{key: variable.get() for key, variable in self.fields.items()}, 'sensitivity': round(self.sensitivity.get(), 2),
                'continuous': self.continuous.get(), 'catchphrases': self.catchphrases.get()}

    def preview_character(self):
        self.preview_voice(character_only=True)

    def probe_voice(self, engine=None):
        from src.lifecycle import busy
        from src.feedback import EDGE_STYLE_OPTIONS
        if self._voice_probe_running:
            return
        if busy.is_set():
            self.voice_status.set('请先停止当前回答，再检测声音。')
            return
        data = self.snapshot()
        engine = engine or ENGINE_OPTIONS[data['engine']]
        values = {'voice': self.gui.VOICE_OPTIONS[data['voice']], 'rate': data['rate'], 'pitch': data['pitch'],
                  'key': data['key'].strip(), 'region': data['region'], 'azure_voice': data['azure_voice'],
                  'style': EDGE_STYLE_OPTIONS[data['style']],
                  'character': {'url': data['role_url'].strip(), 'ref_audio_path': data['role_audio'].strip(),
                                'prompt_text': data['role_text'], 'prompt_lang': data['role_language'],
                                'text_lang': 'auto', 'speed_factor': data['role_speed']}}
        self.probe_button.state(['disabled'])
        self._voice_probe_running = True
        self.voice_status.set('正在检测所选服务并尝试合成…')
        def check():
            from src.voice_health import probe
            ok, result = probe(engine, values)
            def finish():
                self._voice_probe_running = False
                if self.window.winfo_exists():
                    self.voice_status.set(('可用：' if ok else '不可用：') + result)
                    self.probe_button.state(['!disabled'])
            self.ui._post(finish)
        threading.Thread(target=check, daemon=True, name='voice-check').start()

    def preview_voice(self, character_only=False):
        from src.lifecycle import busy
        if busy.is_set():
            self.message.set('请先停止当前回答，再试听声音。')
            return
        if character_only and ENGINE_OPTIONS[self.fields['engine'].get()] != 'gptsovits':
            self.message.set('请先在“声音”页选择角色声音播报方式。')
            return
        if not self.apply():
            return
        busy.set()
        self.ui.set_busy(True)
        from src.lifecycle import cancel_capture
        cancel_capture()
        from src.stop import begin_output, end_output
        from src.feedback import say, wait_speaking_done
        def preview():
            try:
                begin_output()
                say('我已经听见了。接下来的事交给我就好。')
                wait_speaking_done()
            finally:
                end_output()
                busy.clear()
                self.ui.set_busy(False)
        threading.Thread(target=preview, daemon=True, name='voice-preview').start()

    def refresh_microphones(self):
        self.microphone_refresh.state(['disabled'])
        def detect():
            try:
                from src.recognize import microphone_choices
                choices = microphone_choices()
            except Exception:
                choices = {}
            def update():
                if not self.window.winfo_exists():
                    return
                self.microphones = choices
                current = self.fields['microphone'].get()
                names = list(dict.fromkeys(['Windows 默认麦克风', current] + list(choices)))
                self.microphone_combo.configure(values=names)
                self.microphone_status.set('设备已更新；同名设备后的文字表示音频接口。' if choices else '未检测到麦克风，文字输入仍可使用。')
                self.microphone_refresh.state(['!disabled'])
            self.ui._post(update)
        threading.Thread(target=detect, daemon=True, name='microphone-detect').start()

    def update_applicability(self):
        engine = ENGINE_OPTIONS[self.fields['engine'].get()]
        self.engine_hint.set(ENGINE_HINTS[engine])
        self.voice_combo.configure(state='readonly' if engine == 'edge' else 'disabled')
        for combo in (self.rate_combo, self.pitch_combo):
            combo.configure(state='readonly' if engine in ('edge', 'azure') else 'disabled')
        for widget in self.azure_fields:
            widget.configure(state=('readonly' if isinstance(widget, ttk.Combobox) else 'normal') if engine == 'azure' else 'disabled')

    def refresh_models(self):
        self.refresh_button.state(['disabled'])
        self.model_status.set('正在检测… 窗口仍可正常操作。')
        def detect():
            models = self.gui.list_ollama_models()
            def update():
                if not self.window.winfo_exists():
                    return
                current = self.fields['model'].get()
                self.model_combo.configure(values=model_choices(current, models))
                if current != '自动选择' and current not in models:
                    self.model_status.set('所选模型暂不可用，已保留选择。请检查 Ollama 是否启动。')
                elif models:
                    self.model_status.set(f'检测到 {len(models)} 个模型。刷新不会更改你的选择。')
                else:
                    self.model_status.set('未检测到模型。可以先使用系统指令，准备好 Ollama 后再刷新。')
                self.refresh_button.state(['!disabled'])
            self.ui._post(update)
        threading.Thread(target=detect, daemon=True, name='model-detect').start()

    def apply(self):
        from src.feedback import configure_tts, EDGE_STYLE_OPTIONS
        from src.executor import configure_ollama
        from src.lifecycle import cancel_capture
        data = self.snapshot()
        try:
            energy = float(data['energy'])
            if not 0.001 <= energy <= 0.2:
                raise ValueError
        except ValueError:
            self.message.set('录音能量阈值需要在 0.001～0.2 之间。')
            return False
        if data['microphone'] == 'Windows 默认麦克风':
            microphone = None
        elif data['microphone'] in self.microphones:
            microphone = self.microphones[data['microphone']]
        elif data['microphone'] == self._initial['microphone']:
            microphone = self.gui._runtime.get('microphone')
        else:
            self.message.set('所选设备不可用，请刷新麦克风列表后重试。')
            return False
        wake = [word.strip() for word in data['wake'].replace('，', ',').split(',') if word.strip()]
        stop = [word.strip() for word in data['stop'].replace('，', ',').split(',') if word.strip()]
        if not wake or not stop:
            self.message.set('唤醒词和停止词都至少需要填写一个。')
            return False
        if not 0.1 <= data['sensitivity'] <= 1:
            self.message.set('严格程度需要在 0.1～1 之间。')
            return False
        engine = ENGINE_OPTIONS[data['engine']]
        from src.character_tts import validate_profile
        try:
            character = {'url': data['role_url'].strip(), 'ref_audio_path': data['role_audio'].strip(),
                         'prompt_text': data['role_text'].strip(), 'prompt_lang': data['role_language'],
                         'text_lang': 'auto', 'speed_factor': float(data['role_speed'])}
            if engine == 'gptsovits':
                character = validate_profile(character)
                import os
                if not os.path.isfile(character['ref_audio_path']):
                    raise ValueError('角色参考音频文件不存在，请先选择有效文件。')
        except (ValueError, TypeError) as exc:
            self.message.set(str(exc))
            return False
        voice = self.gui.VOICE_OPTIONS[data['voice']]
        style = EDGE_STYLE_OPTIONS[data['style']]
        if engine == 'azure' and not data['key'].strip():
            self.message.set('请在高级页填写 Azure 密钥，或选择其他播报方式。')
            return False
        if engine == 'azure':
            from src.voice_health import azure_endpoint
            try:
                azure_endpoint(data['region'])
            except ValueError as exc:
                self.message.set(str(exc))
                return False
        model = '' if data['model'] == '自动选择' else data['model']
        if not self.gui.save_settings(wake, data['sensitivity'], engine, voice, style, data['rate'], data['pitch'],
                                      tts_azure_key=data['key'].strip(), tts_azure_region=data['region'].strip(),
                                      tts_azure_voice=data['azure_voice'].strip(), ollama_model=model, stop_words=stop,
                                      microphone=microphone, energy_threshold=energy, continuous_dialogue=data['continuous'],
                                      character_voice=character, japanese_catchphrases=data['catchphrases']):
            self.message.set('保存失败，请检查用户数据目录是否可写。原设置已保留。')
            return False
        configure_tts(engine, voice, style, data['rate'], data['pitch'], azure_key=data['key'].strip(),
                      azure_region=data['region'].strip(), azure_voice=data['azure_voice'].strip(), character_profile=character)
        configure_ollama(model or None)
        cancel_capture()
        if self.ui._on_retry and (data['microphone'] != self._initial['microphone'] or data['energy'] != self._initial['energy']):
            self.ui._on_retry()
        self._initial = self.snapshot()
        self.message.set('已保存并应用，下次启动也会保留。')
        return True

    def close(self):
        if self.snapshot() != self._initial:
            answer = messagebox.askyesnocancel('尚未保存', '要保存刚才修改的设置吗？', parent=self.window)
            if answer is None or (answer and not self.apply()):
                return 'break'
        self.window.destroy()
        return 'break'
