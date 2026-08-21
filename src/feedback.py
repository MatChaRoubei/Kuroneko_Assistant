import os
import sys
import subprocess
import threading
import ctypes

_tts_lock = threading.Lock()
_speaking = threading.Event()

# 引擎偏好配置（由 main.py 启动时通过 configure_tts 设置）
_engine_pref = 'auto'
_edge_voice = 'zh-CN-XiaoxiaoNeural'

# 惰性缓存
_pyttsx3_engine = None
_pyttsx3_tried = False
_vits_tts = None
_vits_tried = False


def configure_tts(engine='auto', voice='zh-CN-XiaoxiaoNeural'):
    """设置 TTS 引擎偏好：auto / edge / vits / pyttsx3"""
    global _engine_pref, _edge_voice
    if engine:
        _engine_pref = engine
    if voice:
        _edge_voice = voice


_wake_audio = None  # 预生成的唤醒反馈音频 (samples, sample_rate)


def warmup_tts():
    """后台预热本地 TTS 引擎，并预生成唤醒反馈音频，减少首次响应延迟"""
    def _warm():
        global _wake_audio
        try:
            if _engine_pref in ('vits', 'auto'):
                tts = _init_vits()
                if tts is not None:
                    import numpy as np
                    audio = tts.generate('我在', sid=0, speed=1.0)
                    samples = np.asarray(audio.samples, dtype=np.float32)
                    if samples.size:
                        _wake_audio = (samples, audio.sample_rate)
                        print('[TTS] 唤醒反馈音频已预生成')
        except Exception:
            pass
    try:
        threading.Thread(target=_warm, daemon=True, name='tts-warmup').start()
    except Exception:
        pass


def say_wake():
    """快速播报唤醒反馈「我在」：优先用预生成音频，跳过合成延迟"""
    import sounddevice as sd
    if _wake_audio is not None:
        try:
            samples, rate = _wake_audio
            sd.play(samples, rate, blocksize=8192)
            sd.wait()
            return True
        except Exception as e:
            print(f'[TTS] 快速唤醒播报失败: {e}')
    return say_sync('我在')


def _get_tmp_dir():
    import tempfile
    return tempfile.gettempdir()


# ---------- 播放辅助：Windows MCI（零依赖播放 mp3，play ... wait 同步等待） ----------
def _play_mp3(path):
    import time
    winmm = ctypes.windll.winmm
    buf = ctypes.create_unicode_buffer(256)
    alias = 'assistant_tts'
    r = winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', buf, 256, None)
    if r != 0:
        raise RuntimeError(f'MCI open 失败: {buf.value}')
    try:
        winmm.mciSendStringW(f'play {alias}', None, 0, None)
        # 轮询播放状态，支持中途停止
        while True:
            time.sleep(0.1)
            status = ctypes.create_unicode_buffer(128)
            winmm.mciSendStringW(f'status {alias} mode', status, 128, None)
            if status.value == 'stopped':
                break
            from src.stop import is_stop_requested
            if is_stop_requested():
                winmm.mciSendStringW(f'stop {alias}', None, 0, None)
                break
    finally:
        winmm.mciSendStringW(f'close {alias}', None, 0, None)


# ---------- 引擎1：edge-tts（微软神经网络语音，在线，音质最自然） ----------
def _speak_with_edge(text):
    try:
        import asyncio
        import edge_tts
        mp3_path = os.path.join(_get_tmp_dir(), 'assistant_tts.mp3')
        try:
            from src.cleaner import register_active_file, unregister_active_file
        except ImportError:
            register_active_file = unregister_active_file = None
        if register_active_file:
            register_active_file(mp3_path)  # 标记正在使用，清理时跳过
        try:
            asyncio.run(edge_tts.Communicate(text, voice=_edge_voice).save(mp3_path))
            _play_mp3(mp3_path)
            return True
        finally:
            if unregister_active_file:
                unregister_active_file(mp3_path)
    except Exception as e:
        print(f'[TTS] edge-tts 失败: {e}')
        return False


# ---------- 引擎2：本地 VITS（sherpa-onnx，离线自然语音） ----------
def _init_vits():
    global _vits_tts, _vits_tried
    if _vits_tried:
        return _vits_tts
    _vits_tried = True
    try:
        import sherpa_onnx
        from src.config import get_resource_root
        model_dir = os.path.join(get_resource_root(), 'models', 'vits-melo-tts-zh_en')
        model_file = os.path.join(model_dir, 'model.onnx')
        tokens_file = os.path.join(model_dir, 'tokens.txt')
        if not (os.path.exists(model_file) and os.path.exists(tokens_file)):
            print('[TTS] VITS 模型不存在，本地神经语音不可用')
            return None
        lexicon_file = os.path.join(model_dir, 'lexicon.txt')
        dict_dir = os.path.join(model_dir, 'dict')
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=model_file,
                    tokens=tokens_file,
                    lexicon=lexicon_file if os.path.exists(lexicon_file) else '',
                    dict_dir=dict_dir if os.path.isdir(dict_dir) else '',
                ),
                num_threads=2,
            ),
            max_num_sentences=2,
        )
        _vits_tts = sherpa_onnx.OfflineTts(tts_config)
        return _vits_tts
    except Exception as e:
        print(f'[TTS] VITS 初始化失败: {e}')
        _vits_tts = None
        return None


def _speak_with_vits(text):
    tts = _init_vits()
    if tts is None:
        return False
    try:
        import numpy as np
        import sounddevice as sd
        import time as _time
        audio = tts.generate(text, sid=0, speed=1.0)
        samples = np.asarray(audio.samples, dtype=np.float32)
        if samples.size == 0:
            return False
        dur = len(samples) / audio.sample_rate
        print(f'[TTS] VITS 播报开始，时长 {dur:.1f}s', flush=True)
        t0 = _time.time()
        # 加大 buffer，降低长音频流式播放时的 underrun 概率
        sd.play(samples, audio.sample_rate, blocksize=8192)
        sd.wait()
        print(f'[TTS] VITS 播报完成，实际耗时 {_time.time() - t0:.1f}s', flush=True)
        return True
    except Exception as e:
        print(f'[TTS] VITS 播报失败: {e}')
        return False


# ---------- 引擎3：pyttsx3（机械音兜底） ----------
def _init_pyttsx3():
    global _pyttsx3_engine, _pyttsx3_tried
    if _pyttsx3_tried:
        return _pyttsx3_engine
    _pyttsx3_tried = True
    try:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            voices = engine.getProperty('voices')
            for v in voices:
                name = (getattr(v, 'name', '') or '').lower()
                if any(k in name for k in ('chinese', 'zh', 'huihui', 'yaoyao', 'kangkang')):
                    engine.setProperty('voice', v.id)
                    break
        except Exception:
            pass
        try:
            engine.setProperty('rate', 170)
        except Exception:
            pass
        _pyttsx3_engine = engine
    except Exception as e:
        print(f'[TTS] pyttsx3 初始化失败: {e}')
        _pyttsx3_engine = None
    return _pyttsx3_engine


def _speak_with_pyttsx3(text):
    engine = _init_pyttsx3()
    if engine is None:
        return False
    try:
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        print(f'[TTS] pyttsx3 播报失败: {e}')
        return False


def _speak_with_sapi(text):
    """使用 Windows 自带 SAPI 语音引擎（PowerShell），零第三方依赖，打包后也能发声"""
    if sys.platform != 'win32':
        return False
    try:
        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Speak($args[0]); "
        )
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script, text],
            check=True,
            creationflags=creationflags,
            capture_output=True,
        )
        return True
    except Exception as e:
        print(f'[TTS] SAPI 播报失败: {e}')
        return False


# ---------- 主入口 ----------
def _speak(text):
    if _engine_pref == 'edge':
        order = ['edge', 'vits', 'pyttsx3', 'sapi']
    elif _engine_pref == 'vits':
        order = ['vits', 'edge', 'pyttsx3', 'sapi']
    elif _engine_pref == 'pyttsx3':
        order = ['pyttsx3', 'sapi']
    else:  # auto：在线优先，断网回退本地，最后机械音兜底
        order = ['edge', 'vits', 'pyttsx3', 'sapi']

    for engine in order:
        try:
            if engine == 'edge' and _speak_with_edge(text):
                return
            if engine == 'vits' and _speak_with_vits(text):
                return
            if engine == 'pyttsx3' and _speak_with_pyttsx3(text):
                return
            if engine == 'sapi' and _speak_with_sapi(text):
                return
        except Exception as e:
            print(f'[TTS] {engine} 异常: {e}')
    print('[TTS] 所有语音引擎均失败，仅保留文字输出')


def is_speaking():
    """当前是否正在语音播报"""
    return _speaking.is_set()


def wait_speaking_done(timeout=None):
    """等待语音播报结束（主循环在监听前调用，避免录入助手自己的回声）"""
    import time as _time
    if timeout is None:
        while _speaking.is_set():
            _time.sleep(0.05)
    else:
        end = _time.time() + timeout
        while _speaking.is_set() and _time.time() < end:
            _time.sleep(0.05)


def say_sync(text):
    """同步播报：阻塞直到播报完成。用于唤醒反馈等需要等待播报完的场景。"""
    print(f'[语音] {text}')
    text = str(text or '').strip()
    if not text:
        return True
    with _tts_lock:
        _speaking.set()
        try:
            _speak(text)
        finally:
            _speaking.clear()
    return True


def say(text):
    """语音播报文本，异步执行，避免阻塞主循环"""
    print(f'[语音] {text}')
    text = str(text or '').strip()
    if not text:
        return True

    # 先标记"正在播报"，避免主循环在播报线程真正开始前就进入监听
    _speaking.set()

    def _worker():
        with _tts_lock:
            _speaking.set()  # 锁内再标记，避免连续播报时标记被上一个 worker 误清
            try:
                _speak(text)
            finally:
                _speaking.clear()

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as e:
        _speaking.clear()
        print(f'[TTS] 启动播报线程失败: {e}')
    return True


def notify(title, message):
    """发送系统通知，失败时回退到 stderr"""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=5)
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # 回退：写入 stderr，确保在无控制台时也能被捕获
    if sys.stderr is not None:
        print(f'[通知] {title}: {message}', file=sys.stderr)
    return False
