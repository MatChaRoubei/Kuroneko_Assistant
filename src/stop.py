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


def start_stop_listener(use_voice=False, recognizer=None):
    """启动停止监听线程：语音模式识别“停”，文本模式按 Esc 键"""
    global _listener_thread
    if _listener_thread is not None and _listener_thread.is_alive():
        return

    if use_voice:
        return  # 语音模式不启动"说停"监听，避免与唤醒监听争抢麦克风

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


def _listen_voice(recognizer):
    while not OUTPUT_DONE.is_set() and not STOP_REQUESTED.is_set():
        try:
            ok, text = recognizer.listen_once(timeout=0.5, phrase_time_limit=2)
            if ok:
                for w in ('停', '停止', '停一下', '别说了', '闭嘴'):
                    if w in text:
                        request_stop()
                        break
        except Exception:
            pass
