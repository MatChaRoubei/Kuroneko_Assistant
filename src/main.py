import sys
import os
from pathlib import Path


def _setup_import_paths():
    """配置 sys.path，兼容源码运行与 PyInstaller 打包运行"""
    if getattr(sys, 'frozen', False):
        # 打包运行：资源解压在 _MEIPASS，项目根即 _MEIPASS
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    else:
        # 源码运行：项目根为 src 的上一级目录
        base = str(Path(__file__).resolve().parent.parent)
    if base not in sys.path:
        sys.path.insert(0, base)


_setup_import_paths()


# 打包运行（frozen）时，无论 PyInstaller 是否把 stdout/stderr 置空，
# 都无条件把标准输出重定向到 console.log。
# 原因：speech_recognition 等第三方库在 import 时会 print 版本 banner，
# 若依赖 `sys.stdout is None` 判断则会漏掉「带控制台窗口的打包」场景，
# 导致用户一进 exe 就看到一堆打印。统一重定向可彻底消除控制台噪音。
IS_WINDOWED = getattr(sys, 'frozen', False)

if IS_WINDOWED:
    from src.storage import user_file
    _log_path = user_file('diagnostics_console.log' if '--debug' in sys.argv else 'console.log')
    try:
        # 用 'w' 截断而非 'a' 追加：避免反复启动无限累积（旧版曾涨到数百 MB）
        _log_f = open(_log_path, 'w', encoding='utf-8', buffering=1)
        # 用 tee 包装：既吞掉控制台输出，又写入日志文件，方便排障
        _orig_stdout = sys.stdout
        _orig_stderr = sys.stderr

        class _Tee:
            encoding = 'utf-8'

            def __init__(self, orig, logf):
                self._orig = orig
                self._logf = logf

            def write(self, s):
                try:
                    if self._logf.tell() > 2 * 1024 * 1024:
                        self._logf.seek(0)
                        self._logf.truncate()
                    self._logf.write(s)
                except Exception:
                    pass
                return len(s)

            def flush(self):
                try:
                    self._logf.flush()
                except Exception:
                    pass

            def isatty(self):
                return False

        sys.stdout = _Tee(_orig_stdout, _log_f)
        sys.stderr = _Tee(_orig_stderr, _log_f)
    except Exception:
        pass

import queue
import threading
import time
from src.lifecycle import shutdown, voice_paused, capture_cancelled, busy, request_exit, cancel_capture
from src.gui import (start_main_window, load_settings, load_selected_model, get_wake_words,
                     get_sensitivity, get_stop_words, append_asr, append_output, append_system, set_status)


def create_recognizer(cfg):
    from src.recognize import SpeechRecognizer, SherpaONNXRecognizer, resolve_microphone
    from src.gui import _runtime
    device = resolve_microphone(_runtime.get('microphone'))
    threshold = _runtime.get('energy_threshold', cfg.get('energy_threshold', 0.02))
    model_path = cfg['speech_model_path']
    if cfg.get('speech_engine') != 'google' and os.path.isfile(os.path.join(model_path, 'tokens.txt')):
        try:
            return SherpaONNXRecognizer(model_path=model_path, energy_threshold=threshold, device=device)
        except Exception as exc:
            append_system(f'本地语音识别暂不可用，尝试在线识别：{exc}')
    append_system('正在使用在线语音识别，需要网络；离线识别请安装本地语音模型。')
    return SpeechRecognizer(language=cfg.get('language', 'zh-CN'), device=device, energy_threshold=threshold)


class Assistant:
    def __init__(self):
        self.commands = queue.Queue(maxsize=1)
        self.ready = threading.Event()
        self.retry_microphone = threading.Event()
        self.recognizer = None
        self.voice_error = ''
        self.single_utterance = threading.Event()
        self.followup_until = 0
        self.last_input_source = 'text'
        self.ui = start_main_window(self.submit, self.update_status, self.retry, self.speak_once)

    def submit(self, query, source='text'):
        if not self.ready.is_set() or busy.is_set() or shutdown.is_set():
            return False
        # Reserve execution immediately; rapid Enter presses cannot duplicate work.
        busy.set()
        self.last_input_source = source
        # Start the cancellation lifetime at acceptance. A Stop click before
        # the worker dequeues the command must not be cleared by that worker.
        from src.stop import begin_output
        begin_output()
        cancel_capture()
        self.ui.set_busy(True)
        self.commands.put_nowait(query)
        return True

    def speak_once(self):
        if not self.ready.is_set() or busy.is_set() or self.single_utterance.is_set():
            return False
        self.single_utterance.set()
        cancel_capture()
        if self.recognizer is None:
            self.retry_microphone.set()
        set_status('正在准备单次说话…')
        return True

    def retry(self):
        self.retry_microphone.set()
        cancel_capture()
        set_status('正在重新检测麦克风… · 文字输入仍可使用')

    def update_status(self):
        if busy.is_set():
            return
        if voice_paused.is_set():
            set_status('监听已暂停 · 可继续输入文字')
        elif self.voice_error:
            set_status('文字模式 · 点击“重试麦克风”可恢复语音')
        else:
            set_status(f'监听中 · 说“{get_wake_words()[0]} + 指令”或直接输入')

    def initialize(self):
        try:
            set_status('正在加载指令和设置…')
            from src.config import load_config
            from src.logger import configure_logging, get_logger
            from src.executor import load_app_map, configure_ollama
            from src.feedback import configure_tts, warmup_tts
            from src.gui import (get_tts_engine, get_tts_voice, get_tts_style, get_tts_rate,
                                 get_tts_pitch, get_tts_azure_key, get_tts_azure_region, get_tts_azure_voice)
            from src.gui import get_character_voice
            from src.intents import IntentParser
            from src.plugins import PluginManager
            self.cfg = load_config()
            from src.storage import migrate_legacy_settings
            migrate_legacy_settings()
            load_settings(self.cfg)
            configure_logging(self.cfg['log_file'])
            self.logger = get_logger()
            configure_tts(get_tts_engine(), get_tts_voice(), get_tts_style(), get_tts_rate(), get_tts_pitch(),
                          azure_key=get_tts_azure_key(), azure_region=get_tts_azure_region(), azure_voice=get_tts_azure_voice(),
                          character_profile=get_character_voice())
            configure_ollama(load_selected_model(self.cfg.get('ollama_model')))
            self.parser = IntentParser(self.cfg['intents_path'])
            self.plugins = PluginManager(self.cfg['plugin_path'])
            load_app_map(self.cfg['app_map_path'])
            self.corrector = None
            self.fuzzy = None
            if self.cfg.get('enable_asr_correction', True):
                try:
                    from src.nlu.phonetic_corrector import PhoneticCorrector
                    from src.executor import APP_MAP
                    self.corrector = PhoneticCorrector(self.cfg['intents_path'], app_names=list(APP_MAP))
                except Exception as exc:
                    self.logger.warning('纠错模块不可用: %s', exc)
            if self.cfg.get('nlu_engine') == 'fuzzy_regex':
                from src.nlu.fuzzy_regex import FuzzyRegexMatcher
                self.fuzzy = FuzzyRegexMatcher(self.cfg['intents_path'])
            self.ready.set()
            self.ui.set_ready()
            set_status('文字输入已就绪 · 正在准备语音识别…')
            threading.Thread(target=self.execute_loop, daemon=True, name='commands').start()
            threading.Thread(target=self.voice_loop, daemon=True, name='voice').start()
            if voice_paused.is_set():
                self.update_status()
            # Model warmup and application discovery never delay a usable window.
            warmup_tts()
            from src.executor import scan_all_program_folders
            threading.Thread(target=scan_all_program_folders, daemon=True, name='app-index').start()
            from src.cleaner import start_periodic_cleanup
            start_periodic_cleanup()
            from src.reminders import get_reminders
            get_reminders()
        except Exception as exc:
            append_system(f'初始化失败：{exc}。可从托盘运行诊断。')
            set_status('启动遇到问题 · 请运行诊断查看详情')
            import traceback
            traceback.print_exc()

    def voice_loop(self):
        from src.feedback import wait_speaking_done, say_wake
        from src.recognize import capture_context
        from src.lifecycle import capture_should_stop
        while not shutdown.is_set():
            manual = self.single_utterance.is_set()
            if busy.is_set() or (voice_paused.is_set() and not manual):
                shutdown.wait(0.1)
                continue
            if self.recognizer is None or self.retry_microphone.is_set():
                self.retry_microphone.clear()
                try:
                    self.recognizer = create_recognizer(self.cfg)
                    self.voice_error = ''
                except Exception as exc:
                    self.recognizer = None
                    self.voice_error = str(exc)
                    self.single_utterance.clear()
                    append_system(f'语音暂不可用：{exc}。您可以继续输入文字，连接麦克风后点击重试。')
                    self.update_status()
                    while not shutdown.is_set() and not self.retry_microphone.wait(0.2):
                        pass
                    continue
            capture_cancelled.clear()
            if busy.is_set() or (voice_paused.is_set() and not manual):
                continue
            wait_speaking_done()
            if shutdown.is_set():
                return
            self.update_status()
            try:
                capture_context.manual = manual
                def on_wake():
                    set_status('已唤醒 · 请说指令')
                    say_wake()
                followup = time.monotonic() < self.followup_until
                if manual or followup:
                    self.single_utterance.clear()
                    self.ui.set_capturing(True)
                    set_status('请直接说指令，说完会自动结束。' if manual else '连续对话中 · 可直接说下一句，8 秒静音后结束')
                    ok, query = self.recognizer.listen_once(timeout=5 if manual else 8, phrase_time_limit=15)
                    self.followup_until = 0
                else:
                    ok, query = self.recognizer.listen_with_kws(get_wake_words(), on_wake=on_wake, sensitivity=get_sensitivity())
                if capture_cancelled.is_set() or shutdown.is_set():
                    continue
                if ok and query:
                    self.submit(query, source='voice')
                elif query and '未检测' not in query and '取消' not in query:
                    self.logger.warning('语音监听: %s', query)
                    if '录音' in query or 'PortAudio' in query:
                        self.recognizer = None
                    shutdown.wait(0.5)
            except Exception as exc:
                self.logger.warning('语音监听异常: %s', exc)
                self.recognizer = None
                shutdown.wait(1)
            finally:
                capture_context.manual = False
                self.ui.set_capturing(False)
                if not busy.is_set():
                    self.update_status()

    def execute_loop(self):
        from src.executor import execute_intent, SYSTEM_PANEL_COMMANDS
        from src.recognize import is_stop_command
        from src.feedback import say, wait_speaking_done
        from src.stop import begin_output, end_output, start_stop_listener, is_stop_requested
        while not shutdown.is_set():
            try:
                query = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            allow_followup = True
            try:
                if is_stop_requested():
                    append_system('已取消这条指令。')
                    continue
                append_asr(query)
                if query in ('退出', '关闭', '拜拜'):
                    request_exit()
                    continue
                if is_stop_command(query, get_stop_words()):
                    append_system('已停止，等待下一条指令。')
                    continue
                set_status('正在处理… · 按 Esc 可停止回答')
                if self.corrector:
                    query = self.corrector.correct(query)
                name, slots = self.fuzzy.match(query) if self.fuzzy else self.parser.parse(query)
                if name == 'unknown':
                    name, slots = self.parser.parse(query)
                if name == 'open_app' and slots.get('app_name') in SYSTEM_PANEL_COMMANDS:
                    name, slots = 'open_system_panel', {'panel': slots['app_name']}
                if name == 'dialogue_mode':
                    from src.gui import _runtime
                    enabled = slots['enabled']
                    _runtime['continuous_dialogue'] = enabled
                    allow_followup = enabled
                    self.followup_until = time.monotonic() + 8 if enabled else 0
                    append_output('已开启连续对话。每次回复后可直接说下一句。' if enabled else '已结束连续对话，下一条语音指令需要唤醒词。')
                    continue
                # One input owner at a time. Energy-only barge-in mistakes speaker
                # echo for speech and sd.rec can stop sd.play, so use stop words.
                if name == 'unknown' and self.recognizer and not voice_paused.is_set():
                    # Let the normal capture loop release its stream first.
                    from src.recognize import capture_lock
                    with capture_lock:
                        capture_cancelled.clear()
                        start_stop_listener(True, self.recognizer, get_stop_words())
                plugin_ok, plugin_result = self.plugins.try_execute(name, slots)
                result = (True, str(plugin_result)) if plugin_ok else execute_intent(name, slots, query)
                success, message = result[:2]
                streamed = len(result) > 2 and result[2]
                append_output(message if success else '执行失败：' + message)
                if not streamed and not is_stop_requested():
                    say(message)
                set_status('正在播报… · 按 Esc 可停止' if not is_stop_requested() else '已停止回答')
                wait_speaking_done()
            except Exception as exc:
                self.logger.exception('指令处理失败')
                append_output(f'处理失败：{exc}')
            finally:
                end_output()
                self.commands.task_done()
                busy.clear()
                self.ui.set_busy(False)
                from src.gui import _runtime
                if allow_followup and not is_stop_requested() and getattr(self, 'last_input_source', 'text') == 'voice' and _runtime.get('continuous_dialogue') and not voice_paused.is_set():
                    self.followup_until = time.monotonic() + 8
                self.update_status()


def main():
    # Import lightweight UI first; dependency/model loading happens after paint.
    from src.instance import SingleInstance
    instance = SingleInstance()
    if not instance.acquire():
        return
    if '--text-only' in sys.argv:
        voice_paused.set()
    app = Assistant()
    instance.watch(app.ui.show)
    from src.tray import start_tray, stop_tray
    app.ui._tray_available = start_tray() is not None
    threading.Thread(target=app.initialize, daemon=True, name='initialize').start()
    try:
        app.ui.run()
    finally:
        request_exit()
        stop_tray()
        instance.close()


if __name__ == '__main__':
    if '--debug' in sys.argv:
        from src.debug_tool import run_diagnostics
        run_diagnostics(cli=True, write_file='--no-file' not in sys.argv)
    else:
        main()
