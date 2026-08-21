"""模型选择设置窗口（tkinter，零额外依赖）。

提供可视化界面：检测 Ollama 已部署的模型，让用户手动选择，
选择结果持久化到 exe 旁的 model_selection.json，下次启动自动生效。
"""
import os
import sys
import json
import threading


def get_app_root():
    """可写目录：打包运行 -> exe 所在目录；源码运行 -> 项目根"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_selection_file():
    return os.path.join(get_app_root(), 'model_selection.json')


def load_selected_model():
    """读取用户手动选择的模型名，未选择返回空字符串"""
    try:
        path = get_selection_file()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return (data.get('ollama_model') or '').strip()
    except Exception:
        pass
    return ''


def save_selected_model(model):
    """保存用户选择的模型名"""
    try:
        path = get_selection_file()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'ollama_model': model}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def list_ollama_models():
    """检测 Ollama 已部署的模型，返回模型名列表"""
    try:
        import ollama
        models = ollama.list()
        return [m.model for m in (models.models or [])]
    except Exception:
        return []


def show_model_selector():
    """弹窗：检测并选择 Ollama 模型"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    def _build():
        try:
            root = tk.Tk()
            root.title('黑猫语音助手 - 模型设置')
            root.geometry('440x320')
            root.resizable(False, False)
            root.attributes('-topmost', True)

            tk.Label(root, text='选择 Ollama 大模型', font=('Microsoft YaHei', 13, 'bold')).pack(pady=(16, 2))
            tk.Label(root, text='AI 对话将使用所选模型', fg='gray').pack()

            # 模型下拉框
            frame = tk.Frame(root)
            frame.pack(pady=14)
            tk.Label(frame, text='模型：', font=('Microsoft YaHei', 10)).pack(side='left')
            combo = ttk.Combobox(frame, width=32, state='readonly', font=('Microsoft YaHei', 10))
            combo.pack(side='left', padx=6)

            status = tk.Label(root, text='', fg='gray', font=('Microsoft YaHei', 9))
            status.pack()

            def refresh():
                models = list_ollama_models()
                combo['values'] = models
                if models:
                    combo.current(0)
                    status.config(text=f'检测到 {len(models)} 个模型', fg='green')
                else:
                    status.config(text='未检测到模型，请确认 Ollama 正在运行', fg='red')

            tk.Button(root, text='刷新检测', command=refresh, font=('Microsoft YaHei', 10)).pack(pady=6)

            current = load_selected_model()
            models = list_ollama_models()
            combo['values'] = models
            if current and current in models:
                combo.set(current)
                status.config(text=f'当前：{current}', fg='green')
            elif models:
                combo.current(0)
                status.config(text=f'检测到 {len(models)} 个模型', fg='gray')

            def save():
                model = combo.get()
                if not model:
                    messagebox.showwarning('提示', '请先选择一个模型', parent=root)
                    return
                if save_selected_model(model):
                    try:
                        from src.executor import configure_ollama
                        configure_ollama(model)
                    except Exception:
                        pass
                    messagebox.showinfo('成功', f'已切换到模型：\n{model}\n\n下次 AI 对话即生效', parent=root)
                    root.destroy()
                else:
                    messagebox.showerror('失败', '保存失败，请检查目录权限', parent=root)

            tk.Button(root, text='保存并应用', command=save, bg='#4CAF50', fg='white',
                      font=('Microsoft YaHei', 11, 'bold'), width=16).pack(pady=12)

            root.mainloop()
        except Exception as e:
            print(f'[GUI] 窗口启动失败: {e}')

    threading.Thread(target=_build, daemon=True, name='model-selector').start()


def get_resource_root():
    """资源根目录：打包运行 -> _MEIPASS；源码运行 -> 项目根"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MainWindow:
    """主窗口：以 firstUI.png 为背景，实时展示识别内容和输出内容"""

    def __init__(self):
        self.root = None
        self.text = None
        self.status_label = None
        self._win_w = 1000
        self._win_h = 560

    def run(self):
        """阻塞运行窗口（在调用线程）"""
        try:
            import tkinter as tk
            from PIL import Image, ImageTk

            W, H = self._win_w, self._win_h
            self.root = tk.Tk()
            self.root.title('黑猫语音助手')
            self.root.geometry(f'{W}x{H}')
            self.root.resizable(False, False)

            # 背景图
            bg_path = os.path.join(get_resource_root(), 'firstUI.png')
            self._bg = None
            if os.path.exists(bg_path):
                try:
                    img = Image.open(bg_path).resize((W, H), Image.LANCZOS)
                    self._bg = ImageTk.PhotoImage(img)
                except Exception as e:
                    print(f'[GUI] 背景图加载失败: {e}')

            canvas = tk.Canvas(self.root, width=W, height=H, highlightthickness=0)
            canvas.pack(fill='both', expand=True)
            if self._bg:
                canvas.create_image(0, 0, image=self._bg, anchor='nw')

            # 顶部状态
            self.status_label = tk.Label(self.root, text='● 监听中', fg='#ffffff',
                                         bg='#0e1621', font=('Microsoft YaHei', 12, 'bold'),
                                         padx=18, pady=4)
            canvas.create_window(W // 2, 32, window=self.status_label)

            # 中部对话区（白色卡片，简约清爽）
            frame = tk.Frame(self.root, bg='#ffffff', highlightthickness=1, highlightbackground='#ccd5e0')
            self.text = tk.Text(frame, bg='#ffffff', fg='#2b3440',
                                font=('Microsoft YaHei', 11), wrap='word',
                                insertbackground='#2b3440', relief='flat',
                                padx=16, pady=14, spacing1=3, spacing3=3)
            scroll = tk.Scrollbar(frame, command=self.text.yview)
            self.text.configure(yscrollcommand=scroll.set)
            self.text.pack(side='left', fill='both', expand=True)
            scroll.pack(side='right', fill='y')
            canvas.create_window(W // 2, H // 2 - 5, window=frame, width=W - 120, height=H - 140)

            # 文字配色
            self.text.tag_configure('asr', foreground='#1976d2')   # 你（识别）蓝色
            self.text.tag_configure('out', foreground='#2b3440')   # 助手深灰
            self.text.tag_configure('sys', foreground='#9aa4b2')   # 系统浅灰

            # 底部按钮
            btn = tk.Frame(self.root, bg='#0e1621')
            b = dict(font=('Microsoft YaHei', 10), width=10, relief='flat', cursor='hand2')
            tk.Button(btn, text='⚙ 设置', bg='#ffffff', fg='#2b3440', command=self._on_settings, **b).pack(side='left', padx=6)
            tk.Button(btn, text='清空', bg='#ffffff', fg='#2b3440', command=self._on_clear, **b).pack(side='left', padx=6)
            tk.Button(btn, text='退出', bg='#e57373', fg='white', command=self._on_exit, **b).pack(side='left', padx=6)
            canvas.create_window(W // 2, H - 28, window=btn)

            self.append_system('黑猫语音助手已启动，说「你好黑猫」唤醒')
            # 关闭窗口 = 隐藏到托盘，而不是退出进程
            self.root.protocol('WM_DELETE_WINDOW', self._on_close)
            self.root.mainloop()
        except Exception as e:
            print(f'[GUI] 主窗口启动失败: {e}')

    def _on_clear(self):
        if self.text:
            self.text.delete('1.0', 'end')

    def _on_settings(self):
        show_settings()

    def _on_close(self):
        """关闭窗口 = 隐藏到托盘，程序继续后台运行"""
        if self.root:
            try:
                self.root.withdraw()
            except Exception:
                pass

    def show(self):
        """显示窗口（从托盘唤起），并恢复正确的位置和大小"""
        if self.root:
            try:
                self.root.deiconify()
                # withdraw 后 geometry 可能丢失，重新设置并居中
                w, h = self._win_w, self._win_h
                x = (self.root.winfo_screenwidth() - w) // 2
                y = (self.root.winfo_screenheight() - h) // 2
                self.root.geometry(f'{w}x{h}+{x}+{y}')
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass

    def _on_exit(self):
        os._exit(0)

    def _post(self, fn):
        if self.root:
            try:
                self.root.after(0, fn)
            except Exception:
                pass

    def set_status(self, text):
        self._post(lambda: self.status_label.config(text=text) if self.status_label else None)

    def append_asr(self, text):
        self._post(lambda: self._insert(f'你：{text}\n', 'asr'))

    def append_output(self, text):
        self._post(lambda: self._insert(f'助手：{text}\n', 'out'))

    def append_system(self, text):
        self._post(lambda: self._insert(f'{text}\n', 'sys'))

    def _insert(self, line, tag):
        if self.text:
            self.text.insert('end', line, tag)
            self.text.see('end')


# 运行时设置（唤醒词、灵敏度、TTS 引擎）
_runtime = {
    'wake_words': ['你好黑猫', '助手', '黑猫', '你好助手'],
    'sensitivity': 0.8,
    'tts_engine': 'vits',
    'tts_voice': 'zh-CN-XiaoxiaoNeural',
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


def load_settings():
    """启动时读取设置，覆盖默认值"""
    global _runtime
    try:
        path = get_settings_file()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'wake_words' in data:
                ww = data['wake_words']
                if isinstance(ww, str):
                    ww = [w.strip() for w in ww.replace('，', ',').split(',') if w.strip()]
                if ww:
                    _runtime['wake_words'] = ww
            if 'sensitivity' in data:
                try:
                    _runtime['sensitivity'] = float(data['sensitivity'])
                except Exception:
                    pass
            if 'tts_engine' in data and data['tts_engine']:
                _runtime['tts_engine'] = data['tts_engine']
            if 'tts_voice' in data and data['tts_voice']:
                _runtime['tts_voice'] = data['tts_voice']
    except Exception:
        pass


def save_settings(wake_words, sensitivity, tts_engine, tts_voice=None):
    """保存设置并更新运行时"""
    global _runtime
    _runtime['wake_words'] = wake_words
    _runtime['sensitivity'] = sensitivity
    _runtime['tts_engine'] = tts_engine
    if tts_voice:
        _runtime['tts_voice'] = tts_voice
    try:
        with open(get_settings_file(), 'w', encoding='utf-8') as f:
            json.dump(_runtime, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_wake_words():
    return _runtime.get('wake_words', ['你好黑猫'])


def get_sensitivity():
    return _runtime.get('sensitivity', 0.8)


def get_tts_engine():
    return _runtime.get('tts_engine', 'vits')


def get_tts_voice():
    return _runtime.get('tts_voice', 'zh-CN-XiaoxiaoNeural')


def show_settings():
    """设置窗口：唤醒词、灵敏度、TTS 引擎、模型"""
    def _build():
        try:
            import tkinter as tk
            from tkinter import ttk, messagebox

            root = tk.Tk()
            root.title('设置')
            root.geometry('460x460')
            root.resizable(False, False)
            root.attributes('-topmost', True)
            root.configure(bg='#f5f7fa')

            tk.Label(root, text='设置', font=('Microsoft YaHei', 14, 'bold'), bg='#f5f7fa').pack(pady=(16, 8))

            f1 = tk.Frame(root, bg='#f5f7fa')
            f1.pack(fill='x', padx=32, pady=6)
            tk.Label(f1, text='唤醒词（逗号分隔）', bg='#f5f7fa').pack(anchor='w')
            wake_entry = tk.Entry(f1, font=('Microsoft YaHei', 10))
            wake_entry.pack(fill='x', ipady=3)
            wake_entry.insert(0, '，'.join(_runtime['wake_words']))

            f2 = tk.Frame(root, bg='#f5f7fa')
            f2.pack(fill='x', padx=32, pady=6)
            tk.Label(f2, text='唤醒灵敏度 (0.1~1.0，越低越宽松)', bg='#f5f7fa').pack(anchor='w')
            sens_entry = tk.Entry(f2, font=('Microsoft YaHei', 10))
            sens_entry.pack(fill='x', ipady=3)
            sens_entry.insert(0, str(_runtime['sensitivity']))

            f3 = tk.Frame(root, bg='#f5f7fa')
            f3.pack(fill='x', padx=32, pady=6)
            tk.Label(f3, text='语音引擎（TTS）', bg='#f5f7fa').pack(anchor='w')
            tts_combo = ttk.Combobox(f3, values=['vits', 'edge', 'pyttsx3', 'auto'], state='readonly')
            tts_combo.pack(fill='x')
            tts_combo.set(_runtime.get('tts_engine', 'vits'))

            f3b = tk.Frame(root, bg='#f5f7fa')
            f3b.pack(fill='x', padx=32, pady=6)
            tk.Label(f3b, text='声音角色（edge 引擎时生效）', bg='#f5f7fa').pack(anchor='w')
            voice_combo = ttk.Combobox(f3b, values=list(VOICE_OPTIONS.keys()), state='readonly')
            voice_combo.pack(fill='x')
            cur_voice_id = _runtime.get('tts_voice', 'zh-CN-XiaoxiaoNeural')
            cur_voice_name = '晓晓（女，温柔）'
            for name, vid in VOICE_OPTIONS.items():
                if vid == cur_voice_id:
                    cur_voice_name = name
                    break
            voice_combo.set(cur_voice_name)

            f4 = tk.Frame(root, bg='#f5f7fa')
            f4.pack(fill='x', padx=32, pady=6)
            tk.Label(f4, text='大模型（Ollama）', bg='#f5f7fa').pack(anchor='w')
            model_row = tk.Frame(f4, bg='#f5f7fa')
            model_row.pack(fill='x')
            model_combo = ttk.Combobox(model_row, state='readonly')
            model_combo.pack(side='left', fill='x', expand=True)

            def _refresh_models():
                model_combo['values'] = list_ollama_models()
                if model_combo['values']:
                    model_combo.current(0)
            tk.Button(model_row, text='刷新', command=_refresh_models).pack(side='left', padx=6)
            _refresh_models()
            cur_model = load_selected_model()
            if cur_model and cur_model in model_combo['values']:
                model_combo.set(cur_model)

            def apply():
                """应用设置并保存，返回是否成功"""
                ww = [w.strip() for w in wake_entry.get().replace('，', ',').split(',') if w.strip()]
                if not ww:
                    messagebox.showwarning('提示', '唤醒词不能为空', parent=root)
                    return False
                try:
                    sens = float(sens_entry.get())
                    if not (0.0 < sens <= 1.0):
                        raise ValueError
                except Exception:
                    messagebox.showwarning('提示', '灵敏度需为 0~1 之间的数字', parent=root)
                    return False
                tts = tts_combo.get() or 'vits'
                voice_name = voice_combo.get()
                voice = VOICE_OPTIONS.get(voice_name, 'zh-CN-XiaoxiaoNeural')
                model = model_combo.get()
                save_settings(ww, sens, tts, voice)
                if model:
                    save_selected_model(model)
                try:
                    from src.feedback import configure_tts
                    configure_tts(tts, voice)
                except Exception:
                    pass
                try:
                    from src.executor import configure_ollama
                    configure_ollama(model if model else None)
                except Exception:
                    pass
                return True

            def save_and_close():
                if apply():
                    messagebox.showinfo('成功', '设置已保存并生效', parent=root)
                    root.destroy()

            def on_close():
                # 点窗口 X 关闭时提示保存，避免设置被"重置"
                if messagebox.askyesno('提示', '要保存设置吗？', parent=root):
                    if apply():
                        root.destroy()
                else:
                    root.destroy()

            tk.Button(root, text='保存并应用', command=save_and_close, bg='#4CAF50', fg='white',
                      font=('Microsoft YaHei', 11, 'bold'), width=16).pack(pady=14)

            root.protocol('WM_DELETE_WINDOW', on_close)
            root.mainloop()
        except Exception as e:
            print(f'[GUI] 设置窗口失败: {e}')

    threading.Thread(target=_build, daemon=True, name='settings-ui').start()


_ui = None


def start_main_window():
    """启动主窗口（后台线程，不阻塞主循环）"""
    global _ui
    if _ui is not None:
        return
    _ui = MainWindow()
    threading.Thread(target=_ui.run, daemon=True, name='main-ui').start()


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
