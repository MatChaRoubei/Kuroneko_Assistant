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


# ---------- 打断（barge-in）：播放期间检测主人开口即中断 ----------
_barge_in_thread = None


def start_barge_in_listener(recognizer):
    """启动打断监听：播放/生成期间并行用麦克风能量检测，主人一开口就中断当前播报。

    与停止词监听不同，barge-in 不要求说出特定停止词，只要检测到语音能量
    （主人开始说话）即 clear_speaking() + request_stop()。
    复用 recognizer.microphone 做轻量能量轮询，不调用识别 API，开销低。
    """
    global _barge_in_thread
    if _barge_in_thread is not None and _barge_in_thread.is_alive():
        return
    if recognizer is None:
        return
    mic = getattr(recognizer, 'microphone', None)
    if mic is None:
        return
    _barge_in_thread = threading.Thread(
        target=_barge_in_loop, args=(mic,), daemon=True
    )
    _barge_in_thread.start()


def _barge_in_loop(mic):
    import numpy as np
    energy_threshold = getattr(mic, 'energy_threshold', 0.02)
    # 放大阈值：播放期间环境可能有助手回声，稍提高灵敏度避免误触，但不至于完全失效
    trigger = max(energy_threshold * 3, 0.05)
    chunk = 0.2  # 每次读 0.2s 音频块做能量判断
    sr = getattr(mic, 'sample_rate', 16000)
    n = int(chunk * sr)
    try:
        import sounddevice as sd
    except Exception:
        return
    dev = getattr(mic, 'device', None)
    while not OUTPUT_DONE.is_set() and not STOP_REQUESTED.is_set():
        try:
            audio = sd.rec(n, samplerate=sr, channels=1, dtype='float32', device=dev)
            sd.wait()
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > trigger:
                print(f'[barge-in] 检测到主人开口（能量 {peak:.3f}），中断当前播报')
                # 清掉待播队列并请求停止；正在播的这一句由播放层自然结束
                try:
                    from src.feedback import clear_speaking
                    clear_speaking()
                except Exception:
                    pass
                request_stop()
                break
        except Exception:
            # 麦克风被占用或其他异常：短暂退避后重试，不致命
            time.sleep(0.3)
    global _barge_in_thread
    _barge_in_thread = None
