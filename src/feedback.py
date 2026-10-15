import os
import sys
import queue
import time
import subprocess
import threading
import ctypes

_tts_lock = threading.RLock()
_worker_lock = threading.Lock()
_speaking = threading.Event()

# 引擎偏好配置（由 main.py 启动时通过 configure_tts 设置）
# 默认 edge：微软免费在线神经语音，无需注册/信用卡，开箱即用；
# 只有用户显式选「azure」并在设置里填了订阅密钥时，才会启用 Azure（需信用卡注册）。
_engine_pref = 'edge'
_edge_voice = 'zh-CN-XiaoxiaoNeural'
_character_profile = {}

# Azure(微软)语音合成：国内走 azure.cn 节点、音质高、支持感情风格，可选引擎。
# 需在 Azure 门户开通「语音服务」并拿到订阅密钥与区域(如 chinaeast2)，即需要信用卡注册；
# 无 Key 时不会启用，自动回落 edge。
_AZURE_KEY = ''                       # 订阅密钥（Azure 门户「密钥与终结点」，可选）
_AZURE_REGION = 'chinaeast2'          # 区域，需与订阅资源所在区域一致
_AZURE_VOICE = 'zh-CN-XiaoxiaoNeural' # 控制台「语音合成」可查音色

# 感情风格与语调（GUI 可调，configure_tts 设置）
# 风格：映射到 Edge mstts:express-as 的 style 值
_EDGE_STYLE = 'calm'      # affectionate / gentle / calm / chat / cheerful 等
_EDGE_RATE = '-5%'        # 语速：轻微放慢更从容；过慢(-12%)会显人机、拖沓
_EDGE_PITCH = '+0Hz'      # 音调：edge_tts 用 Hz（如 +0Hz/-6Hz），中性基准（下方引擎区会再次定义，以那里为准）

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
_tts_errors = {}
_tts_fallback_notice = None
_tts_cache_lock = threading.Lock()
_tts_engine_ttl = 60  # 秒


def configure_tts(engine='auto', voice='zh-CN-XiaoxiaoNeural',
                  style='calm', rate='-5%', pitch='+0Hz',
                  azure_key=None, azure_region=None, azure_voice=None, character_profile=None):
    """设置 TTS 引擎偏好：auto / vits / edge / pyttsx3 / azure，
    以及在线神经语音(edge/azure)通用的感情风格/语速/音调。
    auto 默认本地 VITS 优先(零网络延迟)，无模型/失败时回落 edge。"""
    global _engine_pref, _edge_voice, _EDGE_STYLE, _EDGE_RATE, _EDGE_PITCH
    global _AZURE_KEY, _AZURE_REGION, _AZURE_VOICE
    global _character_profile, _wake_audio
    global _tts_fallback_notice
    _tts_fallback_notice = None
    _wake_audio = None
    if character_profile is not None:
        _character_profile = dict(character_profile)
    with _tts_cache_lock:
        _tts_failed_engines.clear()
        _tts_errors.clear()
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
    if azure_key is not None:
        _AZURE_KEY = azure_key
    if azure_region is not None:
        _AZURE_REGION = azure_region
    if azure_voice:
        _AZURE_VOICE = azure_voice


_wake_audio = None  # 预生成的唤醒反馈音频 (samples, sample_rate)


def warmup_tts():
    """后台预热本地 TTS 引擎，并预生成唤醒反馈音频，减少首次响应延迟"""
    def _warm_impl():
        global _wake_audio
        try:
            if _engine_pref == 'gptsovits':
                from src.character_tts import synthesize
                result = synthesize('我在。', _character_profile)
                if result is not None:
                    _wake_audio = result
                return
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
    def _warm():
        with _tts_lock:
            _warm_impl()
    try:
        threading.Thread(target=_warm, daemon=True, name='tts-warmup').start()
    except Exception:
        pass


def say_wake():
    with _tts_lock:
        from src.stop import OUTPUT_DONE, STOP_REQUESTED
        if OUTPUT_DONE.is_set():
            # Stopping the previous answer must not silence the next wake.
            STOP_REQUESTED.clear()
        _speaking.set()
        try:
            if _wake_audio is not None:
                import sounddevice as sd
                samples, rate = _wake_audio
                sd.play(samples, rate, blocksize=2048)
                sd.wait()
                return True
            return say_sync('我在')
        finally:
            _speaking.clear()


def _get_tmp_dir():
    from src.cleaner import _TEMP_DIR
    return _TEMP_DIR


# ---------- 播放辅助：Windows MCI（零依赖播放 mp3，play ... wait 同步等待） ----------
def _play_mp3(path):
    import time
    winmm = ctypes.windll.winmm
    buf = ctypes.create_unicode_buffer(256)
    alias = 'assistant_tts'
    r = winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', buf, 256, None)
    if r != 0:
        raise RuntimeError(f'MCI open 失败（错误码 {r}）')
    try:
        if winmm.mciSendStringW(f'play {alias}', None, 0, None) != 0:
            raise RuntimeError('音频播放器未能开始播放。')
        length = ctypes.create_unicode_buffer(128)
        winmm.mciSendStringW(f'status {alias} length', length, 128, None)
        duration = int(length.value) / 1000 if length.value.isdigit() else 60
        deadline = time.monotonic() + min(300, max(10, duration + 5))
        # 轮询播放状态，支持中途停止
        while True:
            time.sleep(0.1)
            status = ctypes.create_unicode_buffer(128)
            if winmm.mciSendStringW(f'status {alias} mode', status, 128, None) != 0:
                raise RuntimeError('无法读取音频播放状态。')
            if status.value == 'stopped':
                break
            if time.monotonic() > deadline:
                raise TimeoutError('音频播放器没有按时结束。')
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
# 注意：edge_tts 7.x 的 pitch 仅接受带符号 Hz 格式(正则 ^[+-]\d+Hz$)，不接受百分比；
# 0 为中性基准。_speak_with_edge 内也会把残留的百分比写法兜底换算成 Hz。
_EDGE_PITCH = '+0Hz'      # 音调：edge_tts 用 Hz（如 +0Hz/-6Hz），中性基准


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
            from src.voice_health import edge_audio
            from src.stop import is_stop_requested
            data = asyncio.run(edge_audio(text, _edge_voice, _EDGE_RATE, _EDGE_PITCH, is_stop_requested))
            if data is None:
                return True
            with open(mp3_path, 'wb') as stream:
                stream.write(data)
            from src.stop import is_stop_requested
            if is_stop_requested():
                return True
            _play_mp3(mp3_path)
            return True
        finally:
            if unregister_active_file:
                unregister_active_file(mp3_path)
    except Exception as e:
        from src.voice_health import error_message
        _record_failure('edge', error_message(e))
        return False


# ---------- 引擎1.5：Azure(微软)语音合成（国内 azure.cn 节点，在线，音质高） ----------
def _norm_pct(value, default):
    """把 '-12%' / '+0Hz' / '12' 这类写成 Azure prosody 友好的格式。
    - 百分比：'-5%' -> '-5%'
    - Hz：'+0Hz' -> '+0Hz'（edge_tts 7.x 用 Hz，Azure 同样支持 Hz 相对值）
    """
    s = str(value).strip()
    try:
        if s.endswith('Hz'):
            return f'{float(s[:-2]):+.0f}Hz'
        return f'{float(s.replace("%", "")):+.0f}%'
    except Exception:
        return default


def _azure_ssml(text):
    """生成 Azure 神经语音 SSML：支持 mstts:express-as 感情风格 + prosody 语速/音调。
    （edge_tts 会把输入当纯文本 escape，无法用 SSML；Azure REST 接口原生支持，故可恢复感情风格）"""
    import xml.sax.saxutils as su
    safe = su.escape(text)
    rate = _norm_pct(_EDGE_RATE, '+0.00%')
    pitch = _norm_pct(_EDGE_PITCH, '+0.00%')
    if _EDGE_STYLE and _EDGE_STYLE != 'none':
        inner = (f'<mstts:express-as style="{_EDGE_STYLE}">'
                 f'<prosody rate="{rate}" pitch="{pitch}">{safe}</prosody>'
                 '</mstts:express-as>')
    else:
        inner = f'<prosody rate="{rate}" pitch="{pitch}">{safe}</prosody>'
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
            f'<voice name="{_AZURE_VOICE}">{inner}</voice></speak>')


def azure_audio(text, key, region, voice, rate, pitch, style):
    import urllib.request
    import xml.sax.saxutils as su
    from src.voice_health import azure_endpoint
    if not key.strip():
        raise ValueError('Azure 订阅密钥未填写；免费微软在线语音与 Azure 订阅语音是两种方式。')
    inner = f'<prosody rate="{_norm_pct(rate, "+0%")}" pitch="{_norm_pct(pitch, "+0Hz")}">{su.escape(text)}</prosody>'
    if style and style != 'none':
        inner = f'<mstts:express-as style="{su.escape(style)}">{inner}</mstts:express-as>'
    body = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
            f'<voice name={su.quoteattr(voice)}>{inner}</voice></speak>')
    req = urllib.request.Request(azure_endpoint(region), data=body.encode('utf-8'), method='POST', headers={
        'Ocp-Apim-Subscription-Key': key, 'Content-Type': 'application/ssml+xml',
        'X-Microsoft-OutputFormat': 'audio-16khz-128kbitrate-mono-mp3', 'User-Agent': 'KuronekoAssistant'})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = response.read(20 * 1024 * 1024 + 1)
        content_type = response.headers.get('Content-Type', '').lower()
    if len(data) > 20 * 1024 * 1024 or not data or not content_type.startswith('audio/'):
        raise RuntimeError('Azure 未返回有效音频，请检查密钥、区域及音色。')
    return data


def _speak_with_azure(text):
    from src.cleaner import register_active_file, unregister_active_file
    from src.voice_health import error_message
    mp3_path = os.path.join(_get_tmp_dir(), 'assistant_tts.mp3')
    register_active_file(mp3_path)
    try:
        audio = azure_audio(text, _AZURE_KEY, _AZURE_REGION, _AZURE_VOICE, _EDGE_RATE, _EDGE_PITCH, _EDGE_STYLE)
        from src.stop import is_stop_requested
        if is_stop_requested():
            return True
        with open(mp3_path, 'wb') as stream:
            stream.write(audio)
        _play_mp3(mp3_path)
        return True
    except Exception as exc:
        _record_failure('azure', error_message(exc))
        return False
    finally:
        unregister_active_file(mp3_path)


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
        from src.stop import is_stop_requested
        if is_stop_requested():
            return True
        sd.play(samples, audio.sample_rate, blocksize=2048)
        while sd.get_stream().active:
            if is_stop_requested():
                sd.stop()
                break
            _time.sleep(0.03)
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
        from src.stop import is_stop_requested
        engine.startLoop(False)
        try:
            deadline = time.monotonic() + max(8, min(45, len(text) * 0.35 + 5))
            while engine.isBusy():
                engine.iterate()
                if is_stop_requested():
                    engine.stop()
                    break
                if time.monotonic() >= deadline:
                    engine.stop()
                    raise TimeoutError('Windows 语音引擎没有按时结束播报')
                time.sleep(0.02)
        finally:
            engine.endLoop()
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
            "$s.Speak($env:KURONEKO_SPEECH_TEXT)"
        )
        environment = os.environ.copy()
        environment['KURONEKO_SPEECH_TEXT'] = text
        process = subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
            env=environment, creationflags=0x08000000,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        from src.stop import is_stop_requested
        deadline = time.monotonic() + 120
        while process.poll() is None:
            if is_stop_requested() or time.monotonic() > deadline:
                process.terminate()
                process.wait(timeout=3)
                return True
            time.sleep(0.05)
        if process.returncode:
            return False
        return True
    except Exception as e:
        print(f'[TTS] SAPI 播报失败: {e}')
        return False


# ---------- 主入口 ----------
def _speak_with_character(text):
    from src.character_tts import synthesize
    from src.stop import is_stop_requested
    try:
        result = synthesize(text, _character_profile, is_stop_requested)
        if result is None or is_stop_requested():
            return True
        import sounddevice as sd
        samples, rate = result
        sd.play(samples, rate, blocksize=2048)
        while sd.get_stream().active:
            if is_stop_requested():
                sd.stop()
                break
            time.sleep(0.03)
        return True
    except Exception as exc:
        from src.voice_health import error_message
        _record_failure('gptsovits', error_message(exc))
        return False


def _record_failure(engine, message):
    with _tts_cache_lock:
        first = engine not in _tts_errors
        _tts_errors[engine] = message
    print(f'[TTS] {engine}: {message}')
    if first:
        from src.gui import append_system
        fallback = '将尝试本地自然语音。' if engine in ('edge', 'azure') else '文字回答仍可使用。'
        append_system(f'{message} {fallback}')


def _current_order():
    """根据当前引擎偏好返回回退顺序"""
    if _engine_pref == 'gptsovits':
        return ['gptsovits']
    if _engine_pref == 'azure':
        return ['azure', 'vits', 'pyttsx3', 'sapi']
    if _engine_pref == 'edge':
        return ['edge', 'vits', 'pyttsx3', 'sapi']
    if _engine_pref == 'vits':
        return ['vits', 'pyttsx3', 'sapi', 'edge']
    if _engine_pref == 'pyttsx3':
        return ['pyttsx3', 'sapi']
    # auto：本地 VITS(已打包进 exe，零网络延迟、离线可用)优先；
    # 若 VITS 模型缺失/初始化失败，则回落 edge(微软免费在线神经语音，无需注册/信用卡)；
    # 断网或 edge 失败再回退本地机械音/气泡音兜底
    return ['vits', 'pyttsx3', 'sapi', 'edge']


def _speak_cached(text):
    """带引擎可用性缓存的播报：失败的引擎在 TTL 内跳过，离线时不再每条都卡网络超时重试。"""
    global _tts_fallback_notice
    for engine in _current_order():
        from src.stop import is_stop_requested
        if is_stop_requested():
            return
        now = time.time()
        with _tts_cache_lock:
            banned = _tts_failed_engines.get(engine)
            if banned and now < banned:
                continue
        ok = False
        try:
            if engine == 'gptsovits':
                ok = _speak_with_character(text)
            elif engine == 'azure':
                ok = _speak_with_azure(text)
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
            if engine != _current_order()[0] and _tts_fallback_notice != (_engine_pref, engine):
                _tts_fallback_notice = (_engine_pref, engine)
                from src.gui import append_system
                names = {'vits': '本地自然语音', 'pyttsx3': 'Windows 系统声音', 'sapi': 'Windows 系统声音', 'edge': '微软在线语音'}
                append_system(f'当前已回退到{names.get(engine, engine)}。可在设置中检测或试听所选声音。')
            return True
        with _tts_cache_lock:
            known_error = engine in _tts_errors
        if not known_error:
            _record_failure(engine, f'{engine} 语音合成或播放失败。')
        with _tts_cache_lock:
            _tts_failed_engines[engine] = time.time() + _tts_engine_ttl
    print('[TTS] 所有语音引擎均失败，仅保留文字输出')
    return False


def _tts_worker_loop():
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except ImportError:
        pass
    while True:
        item = _tts_queue.get()
        try:
            if item is None:
                return
            from src.stop import is_stop_requested
            with _tts_lock:
                if is_stop_requested():
                    continue
                _speaking.set()
                try:
                    _speak_cached(item)
                except Exception as exc:
                    print(f'[TTS] 播报失败: {exc}')
                finally:
                    _speaking.clear()
        finally:
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
    with _worker_lock:
        if _tts_worker is None or not _tts_worker.is_alive():
            _tts_worker = threading.Thread(target=_tts_worker_loop, daemon=True, name='tts-worker')
            _tts_worker.start()


def is_speaking():
    return _speaking.is_set() or _tts_queue.unfinished_tasks > 0


def wait_speaking_done(timeout=None):
    from src.lifecycle import shutdown
    deadline = None if timeout is None else time.monotonic() + timeout
    while is_speaking() and not shutdown.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            break
        shutdown.wait(0.03)


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
            return _speak_cached(text)
        finally:
            _speaking.clear()


def say(text):
    """语音播报文本：放入队列由后台 TTS 工作线程顺序播报，避免每条都新建线程。"""
    print(f'[语音] {text}')
    text = str(text or '').strip()
    if not text:
        return True

    from src.stop import is_stop_requested
    if is_stop_requested():
        return True
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
        toaster.show_toast(title, str(message)[:220], duration=3, threaded=True)
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # 回退：写入 stderr，确保在无控制台时也能被捕获
    if sys.stderr is not None:
        print(f'[通知] {title}: {message}', file=sys.stderr)
    return False
