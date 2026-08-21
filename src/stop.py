"""停止机制：允许在模型生成 / TTS 播报期间请求停止"""
import threading
import time

STOP_REQUESTED = threading.Event()
OUTPUT_DONE = threading.Event()
_listener_thread = None


def begin_output():
    """开始一次输出前调用：清除停止标志"""
    STOP_REQUESTED.clear()
    OUTPUT_DONE.clear()


def end_output():
    """输出结束后调用：通知停止监听线程退出"""
    OUTPUT_DONE.set()


def request_stop():
    """请求停止当前输出"""
    STOP_REQUESTED.set()


def is_stop_requested():
    return STOP_REQUESTED.is_set()


def start_stop_listener(use_voice=False, recognizer=None, stop_words=None):
    """启动停止监听线程：语音模式识别停止词，文本模式按 Esc 键。

    stop_words: 停止词列表（如 ["停止", "闭嘴", "别说了"]）。为 None 时使用内置默认。
    命中后调用 request_stop()，由执行侧（AI 生成循环 / TTS 队列）进行中断。
    """
    global _listener_thread
    if _listener_thread is not None and _listener_thread.is_alive():
        return

    if use_voice:
        if recognizer is None:
            return
        _listener_thread = threading.Thread(
            target=_listen_voice, args=(recognizer, stop_words), daemon=True
        )
        _listener_thread.start()
        return

    _listener_thread = threading.Thread(target=_listen_keyboard, daemon=True)
    _listener_thread.start()


def _listen_keyboard():
    try:
        import msvcrt
    except ImportError:
        return
    while not OUTPUT_DONE.is_set() and not STOP_REQUESTED.is_set():
        try:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                # Esc 键停止
                if ch == b'\x1b':
                    request_stop()
                    break
                # 输入 'q' 或 's' 停止（英文快捷）
                if ch.lower() in (b'q', b's'):
                    request_stop()
                    break
        except Exception:
            pass
        time.sleep(0.05)


def _listen_voice(recognizer, stop_words=None):
    if stop_words is None:
        stop_words = ['停止', '停下', '闭嘴', '别说了', '打住']
    try:
        from src.recognize import match_phrase
    except Exception:
        match_phrase = None
    while not OUTPUT_DONE.is_set() and not STOP_REQUESTED.is_set():
        try:
            ok, text = recognizer.listen_once(timeout=0.5, phrase_time_limit=2)
            if ok and text:
                if match_phrase is not None:
                    if match_phrase(text, stop_words)[0]:
                        request_stop()
                        break
                else:
                    for w in stop_words:
                        if w in text:
                            request_stop()
                            break
        except Exception:
            pass
