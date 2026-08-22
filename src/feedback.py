import os
import sys
import queue
import time
import subprocess
import threading
import ctypes

_tts_lock = threading.Lock()
_speaking = threading.Event()

# 引擎偏好配置（由 main.py 启动时通过 configure_tts 设置）
_engine_pref = 'auto'
_edge_voice = 'zh-CN-XiaoxiaoNeural'

# 火山(字节)语音合成：国内节点、质量高，作为优先引擎
_volc_api_key = ''
_volc_voice = 'zh_female_qingxin'   # 控制台「音色库」可查，如 zh_female_qingxin
_volc_speed = 1.0                    # 语速比例 0.5~2.0
_volc_pitch = 1.0                    # 音调比例 0.5~2.0

# 感情风格与语调（GUI 可调，configure_tts 设置）
# 风格：映射到 Edge mstts:express-as 的 style 值
_EDGE_STYLE = 'calm'      # affectionate / gentle / calm / chat / cheerful 等
_EDGE_RATE = '-12%'       # 语速：相对基准的快慢，如 -20% ~ +20%
_EDGE_PITCH = '-6%'       # 音调：相对基准的高低，如 -20% ~ +20%

# 风格显示名 -> Edge style 值（供 GUI 下拉使用）
EDGE_STYLE_OPTIONS = {
    '平静慵懒 (calm)': 'calm',
    '温柔 (gentle)': 'gentle',
    '亲昵 (affectionate)': 'affectionate',
    '聊天 (chat)': 'chat',
    '开心 (cheerful)': 'cheerful',
    '叙述 (narration-relaxed)': 'narration-relaxed',
    '不加风格 (none)': 'none',
}

# 惰性缓存
_pyttsx3_engine = None
_pyttsx3_tried = False
_vits_tts = None
_vits_tried = False

# TTS 队列与工作线程：避免每条播报都新建线程（减少线程抖动，连续播报更顺滑）
_tts_queue = queue.Queue()
_tts_worker = None
# 引擎 -> 可重试的绝对时间戳（epoch）。失败后在 TTL 内跳过该引擎，
# 离线时不再每条都卡网络超时去重试 edge-tts
_tts_failed_engines = {}
_tts_cache_lock = threading.Lock()
_tts_engine_ttl = 60  # 秒


def configure_tts(engine='auto', voice='zh-CN-XiaoxiaoNeural',
                  style='calm', rate='-5%', pitch='0%',
                  api_key=None, api_voice=None, api_speed=None):
    """设置 TTS 引擎偏好：auto / edge / vits / pyttsx3 / volcengine，以及 Edge 感情风格/语速/音调"""
    global _engine_pref, _edge_voice, _EDGE_STYLE, _EDGE_RATE, _EDGE_PITCH
    global _volc_api_key, _volc_voice, _volc_speed, _volc_pitch
    if engine:
        _engine_pref = engine
    if voice:
        _edge_voice = voice
    if style:
        _EDGE_STYLE = style
    if rate:
        _EDGE_RATE = rate
    if pitch:
        _EDGE_PITCH = pitch
    if api_key is not None:
        _volc_api_key = api_key
    if api_voice:
        _volc_voice = api_voice
    if api_speed is not None:
        try:
            _volc_speed = float(api_speed)
        except Exception:
            pass


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
# 感情风格与语调：平静慵懒的黑猫人设（calm 风格 + 略慢略低）
_EDGE_STYLE = 'calm'      # affectionate / gentle / calm / chat 等
_EDGE_RATE = '-5%'        # 语速：轻微放慢更从容；过慢(-12%)会显人机、拖沓
_EDGE_PITCH = '0%'        # 音调：保持基准；负降调会让女声发闷不自然


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
            # 重要：edge_tts 的 Communicate 会把传入文本 escape 后当作纯文本，
            # 若传入 SSML 字符串，<speak ...> 标签会被逐字朗读（用户听到的
            # "speak version 1.0 / synthesis" 正是这个 bug）。因此必须传纯文本，
            # 并用 rate/pitch 参数控制语速与音调（edge_tts 不支持 express-as 风格）。
            communicate = edge_tts.Communicate(
                text,
                voice=_edge_voice,
                rate=_EDGE_RATE,
                pitch=_EDGE_PITCH,
                # 国内连微软境外节点经常慢/不稳：连线 4s、接收 12s 超时，
                # 超时即快速失败回退到本地引擎，避免答案已显示却迟迟不读
                connect_timeout=4,
                receive_timeout=12,
            )
            asyncio.run(communicate.save(mp3_path))
            _play_mp3(mp3_path)
            return True
        finally:
            if unregister_active_file:
                unregister_active_file(mp3_path)
    except Exception as e:
            print(f'[TTS] edge-tts 失败: {e}')
            return False


# ---------- 引擎1.5：火山(字节)语音合成（国内节点，在线，音质高） ----------
def _speak_with_volcengine(text):
    """火山语音合成：POST /api/v1/tts，Authorization: Bearer <API Key>，直接返回 mp3。
    需先在火山控制台开通「语音合成」并创建 API Key 填入设置。"""
    if not _volc_api_key:
        return False
    import json
    import urllib.request
    import urllib.error
    url = 'https://openspeech.bytedance.com/api/v1/tts'
    body = {
        'text': text,
        'voice_type': _volc_voice or 'zh_female_qingxin',
        'format': 'mp3',
        'speed': _volc_speed,
        'pitch': _volc_pitch,
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={
            'Authorization': f'Bearer {_volc_api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get('Content-Type', '')
            audio = resp.read()
        # 鉴权/参数错误时返回 JSON 而非音频，首字节为 '{'
        if not audio or (audio[:1] == b'{' and 'audio' not in ctype):
            print(f'[TTS] volcengine 返回非音频(可能鉴权失败): '
                  f'{audio[:200].decode("utf-8", "ignore")}')
            return False
        mp3_path = os.path.join(_get_tmp_dir(), 'assistant_tts.mp3')
        with open(mp3_path, 'wb') as f:
            f.write(audio)
        _play_mp3(mp3_path)
        return True
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'ignore')[:200]
        except Exception:
            pass
        print(f'[TTS] volcengine HTTP 错误 {e.code}: {detail}')
        return False
    except Exception as e:
        print(f'[TTS] volcengine 失败: {e}')
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
def _current_order():
    """根据当前引擎偏好返回回退顺序"""
    if _engine_pref == 'volcengine':
        return ['volcengine', 'edge', 'pyttsx3', 'sapi', 'vits']
    if _engine_pref == 'edge':
        return ['edge', 'pyttsx3', 'sapi', 'vits']
    if _engine_pref == 'vits':
        return ['vits', 'edge', 'pyttsx3', 'sapi']
    if _engine_pref == 'pyttsx3':
        return ['pyttsx3', 'sapi']
    # auto：火山(国内、音质高)优先；未配置 Key 时 _speak_with_volcengine 会
    # 立即返回 False 并被缓存跳过，自动落到 edge；断网再掉本地机械音/气泡音
    return ['volcengine', 'edge', 'pyttsx3', 'sapi', 'vits']


def _speak_cached(text):
    """带引擎可用性缓存的播报：失败的引擎在 TTL 内跳过，离线时不再每条都卡网络超时重试。"""
    for engine in _current_order():
        now = time.time()
        with _tts_cache_lock:
            banned = _tts_failed_engines.get(engine)
            if banned and now < banned:
                continue
        ok = False
        try:
            if engine == 'volcengine':
                ok = _speak_with_volcengine(text)
            elif engine == 'edge':
                ok = _speak_with_edge(text)
            elif engine == 'vits':
                ok = _speak_with_vits(text)
            elif engine == 'pyttsx3':
                ok = _speak_with_pyttsx3(text)
            elif engine == 'sapi':
                ok = _speak_with_sapi(text)
        except Exception as e:
            print(f'[TTS] {engine} 异常: {e}')
            ok = False
        if ok:
            with _tts_cache_lock:
                _tts_failed_engines.pop(engine, None)
            print(f'[TTS] 已使用 {engine} 引擎播报')
            return
        with _tts_cache_lock:
            _tts_failed_engines[engine] = time.time() + _tts_engine_ttl
    print('[TTS] 所有语音引擎均失败，仅保留文字输出')


def _tts_worker_loop():
    """后台 TTS 工作线程：从队列顺序取文本播报，避免每条都新建线程。"""
    while True:
        item = _tts_queue.get()
        if item is None:
            _tts_queue.task_done()
            break
        # 已在请求停止（如用户说了停止词）：跳过本条待播报，快速静音
        from src.stop import is_stop_requested
        if is_stop_requested():
            _tts_queue.task_done()
            continue
        with _tts_lock:
            _speaking.set()
            try:
                _speak_cached(item)
            finally:
                _speaking.clear()
        _tts_queue.task_done()


def clear_speaking():
    """清空待播报队列（停止词触发时调用，让助手尽快静音）。
    当前正在播报的一条会自然结束（edge/vits 的 mp3 播放已支持中途停止）。"""
    try:
        while not _tts_queue.empty():
            try:
                _tts_queue.get_nowait()
                _tts_queue.task_done()
            except Exception:
                break
    except Exception:
        pass


def _ensure_tts_worker():
    global _tts_worker
    if _tts_worker is None or not _tts_worker.is_alive():
        _tts_worker = threading.Thread(target=_tts_worker_loop, daemon=True, name='tts-worker')
        _tts_worker.start()


def is_speaking():
    """当前是否正在语音播报"""
    return _speaking.is_set()


def wait_speaking_done(timeout=None):
    """等待语音播报结束（主循环在监听前调用，避免录入助手自己的回声）"""
    if timeout is None:
        while _speaking.is_set():
            time.sleep(0.05)
    else:
        end = time.time() + timeout
        while _speaking.is_set() and time.time() < end:
            time.sleep(0.05)


def say_sync(text):
    """同步播报：阻塞直到播报完成。用于唤醒反馈等需要等待播报完的场景。"""
    print(f'[语音] {text}')
    text = str(text or '').strip()
    if not text:
        return True
    _ensure_tts_worker()
    with _tts_lock:
        _speaking.set()
        try:
            _speak_cached(text)
        finally:
            _speaking.clear()
    return True


def say(text):
    """语音播报文本：放入队列由后台 TTS 工作线程顺序播报，避免每条都新建线程。"""
    print(f'[语音] {text}')
    text = str(text or '').strip()
    if not text:
        return True

    # 先标记"正在播报"，让主循环在真正开播前就停止监听（防回声）
    _speaking.set()
    _ensure_tts_worker()
    try:
        _tts_queue.put(text)
    except Exception as e:
        _speaking.clear()
        print(f'[TTS] 入队失败: {e}')
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
