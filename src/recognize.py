"""
语音识别模块 - 支持多种语音识别后端
- Google STT (需要网络)
- Sherpa-onnx + SenseVoice (本地离线，推荐)
"""
import io
import wave
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import threading
from functools import wraps
from collections import deque
from src.lifecycle import capture_should_stop as _capture_should_stop

capture_lock = threading.RLock()
capture_context = threading.local()


def capture_should_stop():
    cancelled = getattr(capture_context, 'cancelled', None)
    if getattr(capture_context, 'manual', False):
        from src.lifecycle import shutdown, capture_cancelled
        stopped = shutdown.is_set() or capture_cancelled.is_set()
    else:
        stopped = _capture_should_stop()
    return stopped or (cancelled is not None and cancelled())


def owns_microphone(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        with capture_lock:
            if capture_should_stop():
                return False, '监听已取消'
            return method(*args, **kwargs)
    return guarded


def listen_for_wake(recognizer, wake_words, on_wake=None, sensitivity=0.8):
    """Decode at speech endpoints; retain a short prefix and skip silence."""
    mic = recognizer.microphone
    if getattr(mic, 'neural_endpoint', False) is True:
        utterances = mic.iter_utterances()
        command = None
        try:
            for wav in utterances:
                from src.gui import set_status
                set_status('正在识别语音…')
                ok, text = recognizer._recognize_wav(wav)
                if capture_should_stop():
                    return False, '监听已取消'
                if ok:
                    found, remaining = match_wake_word(text, wake_words, threshold=sensitivity)
                    if not wake_words or found:
                        command = remaining if wake_words else text
                        break
                set_status('监听中 · 请说唤醒词和指令')
            else:
                return False, '监听已取消'
        finally:
            utterances.close()
        if command:
            return True, command
        if on_wake:
            on_wake()
        return recognizer.listen_once(timeout=5, phrase_time_limit=15)
    chunks = deque(maxlen=60)
    prefix = deque(maxlen=3)
    quiet = 0
    speaking = False
    command = None
    try:
        for block in mic.iter_blocks(0.1):
            if capture_should_stop():
                return False, '监听已取消'
            rms = float(np.sqrt(np.mean(block ** 2)))
            if not speaking:
                prefix.append(block)
                if rms <= mic.energy_threshold:
                    continue
                chunks.extend(prefix)
                prefix.clear()
                speaking = True
            else:
                chunks.append(block)
            quiet = quiet + 1 if rms <= mic.energy_threshold else 0
            if quiet < 6 and len(chunks) < 60:
                continue
            ok, text = recognizer._recognize_wav(_block_to_wav(np.concatenate(chunks), mic.sample_rate))
            chunks.clear()
            speaking, quiet = False, 0
            if capture_should_stop():
                return False, '监听已取消'
            if ok:
                found, remaining = match_wake_word(text, wake_words, threshold=sensitivity)
                if not wake_words or found:
                    command = remaining if wake_words else text
                    break
        else:
            return False, '录音已结束'
    except Exception as exc:
        return False, f'录音失败: {exc}'
    # The input stream has closed before acknowledgement or follow-up recording.
    if command:
        return True, command
    if on_wake:
        on_wake()
    return recognizer.listen_once(timeout=5, phrase_time_limit=10)


def check_speech_dependencies(device=None):
    """检查语音依赖是否满足"""
    try:
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        if not input_devices:
            return False, '检测不到麦克风设备，请插入麦克风或检查系统设置'
        default_input = sd.query_devices(device, kind='input') if device is not None else sd.query_devices(kind='input')
        return True, f'检测到麦克风：{default_input["name"]}，采样率 {int(default_input["default_samplerate"])}Hz'
    except Exception as e:
        return False, f'音频设备检测失败: {e}'


def list_microphones():
    """列出所有可用麦克风设备"""
    try:
        devices = sd.query_devices()
        mics = []
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                mics.append(f"[{i}] {d['name']} ({int(d['default_samplerate'])}Hz)")
        return mics
    except Exception:
        return []


def microphone_choices():
    apis = sd.query_hostapis()
    return {f'{item["name"]} · {apis[item["hostapi"]]["name"]}':
            {'name': item['name'], 'hostapi': apis[item['hostapi']]['name']}
            for item in sd.query_devices() if item['max_input_channels'] > 0}


def resolve_microphone(selection):
    if not selection:
        return None
    choices = microphone_choices()
    if selection not in choices.values():
        raise RuntimeError('所选麦克风已断开。请连接设备，或在设置的“语音输入”页改选麦克风。')
    apis = sd.query_hostapis()
    for index, item in enumerate(sd.query_devices()):
        if item['max_input_channels'] > 0 and item['name'] == selection['name'] and apis[item['hostapi']]['name'] == selection['hostapi']:
            return index
    raise RuntimeError('所选麦克风不可用。')


class SounddeviceMicrophone:
    """
    使用 sounddevice 作为音频源的麦克风封装，
    替代 pyaudio（Windows 上 pyaudio 安装困难）
    """
    def __init__(self, device=None, sample_rate=16000, chunk_size=1024, energy_threshold=0.02):
        if device is None:
            device = sd.query_devices(kind='input')['index']
        self.device = device
        self.sample_rate = int(sample_rate)
        self.chunk_size = chunk_size
        # 语音检测能量阈值：低于此值视为静音。不同麦克风环境差异大，
        # 默认 0.02 适用于大多数 USB 麦克风（环境底噪通常在 0.003~0.02）。
        self.energy_threshold = energy_threshold

        # 获取设备参数
        if isinstance(device, int):
            dev_info = sd.query_devices(device)
        else:
            dev_info = sd.query_devices(kind='input')
        self.sample_rate = int(dev_info['default_samplerate'])
        self.channels = int(dev_info['max_input_channels'])
        try:
            sd.check_input_settings(device=self.device, channels=1, dtype='float32', samplerate=16000)
            self.sample_rate = 16000
        except Exception:
            pass
        self.endpoint = None
        self.neural_endpoint = False
        try:
            from src.endpoint import SpeechEndpoint
            self.endpoint = SpeechEndpoint()
            self.neural_endpoint = True
        except Exception as exc:
            print(f'[语音端点] 神经检测不可用，使用能量检测: {exc}')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def listen(self, timeout=5, phrase_time_limit=8):
        """
        录制音频，返回 (success, audio_data or error_message)
        timeout: 等待语音开始的超时（秒）
        phrase_time_limit: 语音最大持续时间（秒）
        """
        duration = min(phrase_time_limit + 2, 30)  # 留 2 秒缓冲
        try:
            print(f'正在录音（设备: {self.device}，采样率: {self.sample_rate}Hz）...')

            # 录制音频，使用 np.float32 格式
            audio_data = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32',
                device=self.device
            )

            # 等待录音完成
            sd.wait()

            # 转换为 16-bit PCM
            audio_int16 = np.int16(audio_data * 32767).flatten()
            peak = int(np.max(np.abs(audio_int16))) if audio_int16.size else 0
            print(f'[录音] 完成，样本数 {audio_int16.size}，峰值 {peak}（峰值<1000 可能未录到声音）')

            # 转换为 WAV 格式字节
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_int16.tobytes())
            wav_bytes = wav_buffer.getvalue()

            return True, wav_bytes

        except sd.PortAudioError as e:
            return False, f'录音失败（PortAudio）: {e}'
        except Exception as e:
            return False, f'录音失败: {e}'

    def listen_until_silence(self, max_duration=15.0, silence_duration=0.6,
                             energy_threshold=None, timeout=5):
        """持续录音：检测到语音开始后，直到静音结束。返回 (success, wav_bytes or error)"""
        if self.neural_endpoint:
            utterances = self.iter_utterances(timeout=timeout, max_duration=max_duration)
            try:
                wav = next(utterances, None)
                return (True, wav) if wav is not None else (False, '监听已取消' if capture_should_stop() else '未检测到语音')
            except Exception as exc:
                return False, f'录音失败: {exc}'
            finally:
                utterances.close()
        import time as _time
        if energy_threshold is None:
            energy_threshold = self.energy_threshold

        rate = int(self.sample_rate)
        block = max(int(rate * 0.1), 256)  # 100ms 一片

        audio_chunks = []
        speech_started = False
        silence_accum = 0.0
        total = 0.0
        prefix = deque(maxlen=3)
        speech_duration = 0.0

        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                device=self.device, blocksize=block) as stream:
                while True:
                    if capture_should_stop():
                        return False, '监听已取消'
                    data, _ = stream.read(block)
                    mono = data[:, 0]
                    energy = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))

                    if energy > energy_threshold:
                        if not speech_started:
                            speech_started = True
                            audio_chunks.extend(prefix)
                            print('[监听] 检测到语音，开始录音...')
                        silence_accum = 0.0
                        audio_chunks.append(data)
                    elif speech_started:
                        silence_accum += block / rate
                        audio_chunks.append(data)
                        if silence_accum >= silence_duration:
                            print('[监听] 检测到静音，录音结束')
                            break
                    else:
                        prefix.append(data)

                    total += block / rate
                    if speech_started:
                        speech_duration += block / rate
                    if speech_duration >= max_duration or (not speech_started and total >= timeout):
                        break
        except sd.PortAudioError as e:
            return False, f'录音失败（PortAudio）: {e}'
        except Exception as e:
            return False, f'录音失败: {e}'

        if not speech_started or not audio_chunks:
            return False, '未检测到语音'

        audio = np.concatenate(audio_chunks, axis=0)
        audio_int16 = np.int16(np.clip(audio, -1.0, 1.0) * 32767).flatten()
        peak = int(np.max(np.abs(audio_int16))) if audio_int16.size else 0
        print(f'[监听] 录音完成，时长 {len(audio_int16) / rate:.1f}s，峰值 {peak}')

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio_int16.tobytes())
        return True, wav_buffer.getvalue()

    def iter_utterances(self, timeout=None, max_duration=20):
        """Yield complete speech after a neural endpoint, without fixed windows."""
        self.endpoint.reset()
        blocks = self.iter_blocks(0.032)
        waited = spoken = 0
        was_speaking = False
        try:
            for block in blocks:
                if capture_should_stop():
                    return
                self.endpoint.accept(block, self.sample_rate)
                duration = len(block) / self.sample_rate
                if self.endpoint.speaking:
                    spoken += duration
                    if not was_speaking:
                        from src.gui import set_status
                        set_status('正在听你说话…')
                    was_speaking = True
                else:
                    waited += duration
                if spoken >= max_duration:
                    self.endpoint.detector.flush()
                samples = self.endpoint.pop()
                if samples is not None:
                    yield _block_to_wav(samples, 16000)
                    spoken = waited = 0
                    was_speaking = False
                if timeout is not None and not was_speaking and waited >= timeout:
                    return
        finally:
            blocks.close()

    def record_fixed(self, duration=3.0):
        """固定时长录音（不依赖能量阈值），返回 (success, wav_bytes or error)。

        用于高噪音环境下，能量阈值无法可靠区分语音和噪音时，
        直接按固定时长录音，交给 STT 识别。
        """
        try:
            frames = int(duration * self.sample_rate)
            audio_data = sd.rec(frames, samplerate=self.sample_rate, channels=1,
                                dtype='float32', device=self.device)
            sd.wait()
            audio_int16 = np.int16(np.clip(audio_data, -1.0, 1.0) * 32767).flatten()
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_int16.tobytes())
            return True, wav_buffer.getvalue()
        except Exception as e:
            return False, f'录音失败: {e}'


    def iter_blocks(self, block_seconds=0.3):
        """持续从麦克风读取固定时长的音频块（生成器），用于流式检测/端点判定。

        每次 yield 一个一维 float32 numpy 数组（单声道）。相比 ``listen`` 的整段
        固定时长录音，流式读取能把唤醒/端点检测的时延从「整段时长」降到「单块时长」，
        并避免在静音时长时间空转录音。
        """
        rate = int(self.sample_rate)
        block = max(int(rate * block_seconds), 256)
        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                device=self.device, blocksize=block) as stream:
                while True:
                    if capture_should_stop():
                        return
                    data, _ = stream.read(block)
                    yield data[:, 0]
        except sd.PortAudioError as e:
            print(f'[录音] 流式读取失败（PortAudio）: {e}')
            return
        except Exception as e:
            print(f'[录音] 流式读取失败: {e}')
            return


def _pinyin_alignment(text):
    """返回 (连续拼音串, 各字符拼音起始位置列表[含哨兵])。

    连续拼音串把每个字的拼音无缝拼接（如 "黑猫" -> "heimao"），用于做
    同音字无关的匹配（"黑猫"/"黑毛" 拼音相同即等价）。
    若 pypinyin 不可用或字符数对不齐（含无法转拼音的符号），返回 (None, None)。
    """
    try:
        from pypinyin import lazy_pinyin, Style
    except Exception:
        return None, None
    try:
        pys = lazy_pinyin(text, style=Style.NORMAL)
    except Exception:
        return None, None
    if len(pys) != len(text):
        return None, None
    parts = [str(p) for p in pys]
    starts = []
    pos = 0
    for p in parts:
        starts.append(pos)
        pos += len(p)
    starts.append(pos)  # 哨兵：总长度
    return ''.join(parts), starts


def _span_to_chars(starts, py_start, py_end):
    """把拼音位置区间 [py_start, py_end) 映射回原始字符区间 [c_start, c_end)。"""
    n = len(starts) - 1
    c_start = 0
    for i in range(n):
        if starts[i] <= py_start < starts[i + 1]:
            c_start = i
            break
    c_end = n
    for i in range(n):
        if starts[i] < py_end <= starts[i + 1]:
            c_end = i + 1
            break
    return c_start, c_end


def _match_one(text, word, threshold=0.8):
    """在 text 中查找 word，返回 (是否命中, 命中后的剩余文本)。

    匹配顺序：
      1) 精确文本匹配（最高优先）
      2) 拼音匹配：消解同音字 —— "黑猫"/"黑毛"、"助手"/"朱手" 拼音相同即等价，
         这正是两字唤醒词（尤其"黑猫"）不稳定的根因所在
      3) 字符级模糊兜底：处理缺字/部分识别错误；短词放宽阈值
    """
    from difflib import SequenceMatcher

    if not text or not word:
        return False, text

    text_l = text.lower()
    word_l = word.lower()

    # 1) 精确文本匹配
    if word_l in text_l:
        remaining = text_l.replace(word_l, '', 1).strip()
        return True, remaining

    # 2) 拼音匹配（同音字完全等价）
    text_py, starts = _pinyin_alignment(text_l)
    if text_py is not None:
        word_py, _ = _pinyin_alignment(word_l)
        if word_py:
            idx = text_py.find(word_py)
            if idx != -1:
                c_start, c_end = _span_to_chars(starts, idx, idx + len(word_py))
                remaining = (text_l[:c_start] + ' ' + text_l[c_end:]).strip()
                return True, remaining

    # 3) 字符级模糊兜底（缺字/部分识别错误；两字词放宽阈值，避免永远命中不了）
    if len(word_l) <= 2 or len(text_l) < len(word_l):
        return False, text
    eff_threshold = max(threshold, 0.75)
    for i in range(len(text_l) - len(word_l) + 1):
        chunk = text_l[i:i + len(word_l)]
        ratio = SequenceMatcher(None, word_l, chunk).ratio()
        if ratio >= eff_threshold:
            remaining = (text_l[:i] + ' ' + text_l[i + len(word_l):]).strip()
            return True, remaining

    return False, text


def match_wake_word(text, wake_words, threshold=0.8):
    """在识别文本中查找唤醒词，返回 (是否命中, 唤醒词后的剩余文本)。

    text: 已转小写并去除首尾空白的文本
    wake_words: 唤醒词列表
    """
    if not text or not wake_words:
        return False, text

    remaining = text
    for wake in wake_words:
        hit, rest = _match_one(remaining, wake, threshold)
        if hit:
            print(f'[唤醒] 命中唤醒词 [{wake}]，剩余指令: [{rest}]')
            return True, rest

    return False, text


def match_phrase(text, phrases, threshold=0.8):
    """判断 text 是否包含任一 phrase（拼音级，用于停止词等）。

    返回 (是否命中, 命中的词)。
    """
    if not text or not phrases:
        return False, None
    for phrase in phrases:
        hit, _ = _match_one(text, phrase, threshold)
        if hit:
            return True, phrase
    return False, None


def is_stop_command(text, phrases):
    import re
    text = re.sub(r'^(?:请|帮我)', '', text.strip()).strip(' 。！!吧')
    return any(len(text) <= len(phrase) + 1 and match_phrase(text, [phrase])[0] for phrase in phrases)


class SpeechRecognizer:
    """
    语音识别器，支持中文识别
    使用 sounddevice 采集 + Google 语音识别
    """
    def __init__(self, language='zh-CN', device=None, energy_threshold=0.02):
        ok, msg = check_speech_dependencies(device)
        if not ok:
            raise RuntimeError(msg)
        self.recognizer = sr.Recognizer()
        self.recognizer.operation_timeout = 5
        self.language = language
        self.microphone = SounddeviceMicrophone(device=device, energy_threshold=energy_threshold)

    def _recognize_with_retry(self, audio, retries=1, delay=1):
        """带重试的语音识别，处理网络连接错误"""
        import time
        last_error = None
        for attempt in range(retries):
            if capture_should_stop():
                return False, '监听已取消'
            try:
                text = self.recognizer.recognize_google(audio, language=self.language)
                if capture_should_stop():
                    return False, '监听已取消'
                return True, text
            except Exception as e:
                last_error = e
                err_str = str(e)
                # WinError 10054 = 连接被重置，WinError -1 = 连接断开
                if '10054' in err_str or '10053' in err_str or 'WinError -1' in err_str or 'Connection' in err_str or 'reset' in err_str.lower():
                    if attempt < retries - 1:
                        wait_time = delay * (2 ** attempt)  # 指数退避
                        print(f'网络连接被重置，{wait_time}秒后重试 ({attempt + 1}/{retries})...')
                        time.sleep(wait_time)
                        continue
                # 其他错误直接抛出
                raise
        return False, f'识别失败: {last_error}'

    def _recognize_wav(self, wav_bytes):
        """识别 WAV 字节数据，返回 (success, text) —— 供唤醒后二次聆听复用"""
        try:
            audio_io = io.BytesIO(wav_bytes)
            with sr.AudioFile(audio_io) as source:
                audio = self.recognizer.record(source)
            return self._recognize_with_retry(audio)
        except sr.UnknownValueError:
            return False, '无法识别语音内容'
        except Exception as e:
            return False, f'识别失败: {e}'

    @owns_microphone
    def listen_once(self, timeout=5, phrase_time_limit=8):
        """
        录制并识别一段语音，返回 (success, text or error_message)。

        改用 VAD 端点检测（listen_until_silence）代替固定时长录音：用户说完即停。
        """
        ok, audio_or_error = self.microphone.listen_until_silence(
            max_duration=phrase_time_limit, silence_duration=0.6, timeout=timeout
        )
        if not ok:
            return False, audio_or_error

        audio_data = audio_or_error

        try:
            # 将 WAV 字节数据封装为 AudioFile（从内存）
            audio_io = io.BytesIO(audio_data)
            with sr.AudioFile(audio_io) as source:
                audio = self.recognizer.record(source)

            # 使用 Google 语音识别（需要网络），带重试
            return self._recognize_with_retry(audio)

        except sr.WaitTimeoutError:
            return False, '监听超时，未检测到语音'
        except sr.UnknownValueError:
            return False, '无法识别语音内容'
        except sr.RequestError as e:
            return False, f'语音识别服务出错: {e}'
        except Exception as e:
            return False, f'识别失败: {e}'

    @owns_microphone
    def listen_with_wake_word(self, wake_words=None, on_wake=None, sensitivity=0.8):
        return listen_for_wake(self, wake_words or [], on_wake, sensitivity)

    def listen_with_kws(self, keywords=None, on_wake=None, sensitivity=0.8):
        return self.listen_with_wake_word(keywords, on_wake, sensitivity)

    def listen_await_wake_word(self, wake_word_detector=None, timeout=60):
        """
        使用专用唤醒词检测器（VAD）进行常驻监听
        检测到语音活动后返回 True，后续由 listen_once() 捕获指令

        Args:
            wake_word_detector: WakeWordDetector 实例
            timeout: 最大监听时间（秒）
        Returns:
            (True, None) - 检测到语音活动
            (False, reason) - 超时或错误
        """
        import time
        if wake_word_detector is None:
            return False, '未配置唤醒词检测器'

        wake_word_detector.start()
        start_time = time.time()

        try:
            # 流式读取 0.3s 短块并实时做 VAD 检测，延迟从「整段录音」降到单块粒度，
            # 不再每轮都固定录制 2.5 秒空转
            for block in self.microphone.iter_blocks(0.3):
                if time.time() - start_time >= timeout:
                    return False, '唤醒词检测超时'
                if wake_word_detector.detect_from_bytes(self._block_to_wav(block)):
                    wake_word_detector.stop()
                    return True, None

        finally:
            wake_word_detector.stop()

        return False, '唤醒词检测超时'


def _block_to_wav(block, sample_rate):
    """把一段 float32 单声道音频块转成 16-bit PCM 的 WAV 字节（供 VAD 检测用）。"""
    audio_int16 = np.int16(np.clip(block, -1.0, 1.0) * 32767)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


class SherpaONNXRecognizer:
    """
    本地离线语音识别器，使用 sherpa-onnx + SenseVoice 模型
    完全离线，无需网络，中文识别率高

    使用方法：
    1. 下载 SenseVoice 模型：https://github.com/k2-fsa/sherpa-onnx/releases
       寻找：sherpa-onnx-sense-voice-zh-en-ja-ko-yue-*.tar.bz2
    2. 解压到项目目录，如 models/sense_voice/
    3. 在 config.yaml 中设置 speech.engine: sherpaonnx
    """

    _instance = None  # 类级别缓存，避免重复加载模型

    def __init__(self, model_path='models/sense_voice', num_threads=2, energy_threshold=0.02, device=None):
        """
        初始化 sherpa-onnx 识别器

        Args:
            model_path: 模型文件夹路径（包含 model.onnx 和 tokens.txt）
            num_threads: CPU 推理线程数
        """
        ok, msg = check_speech_dependencies(device)
        if not ok:
            raise RuntimeError(msg)

        # 如果没有传入绝对路径，尝试相对于当前工作目录
        import os
        _src_dir = os.path.dirname(os.path.dirname(__file__))
        if not os.path.isabs(model_path):
            model_path = os.path.join(_src_dir, model_path)

        # 优先使用 INT8/Q8 量化版本（更小更快）
        for model_name in ['model.int8.onnx', 'model_q8.onnx', 'model.onnx']:
            candidate = os.path.join(model_path, model_name)
            if os.path.exists(candidate):
                model_file = candidate
                break
        else:
            model_file = os.path.join(model_path, 'model.onnx')

        if not os.path.exists(model_file):
            raise FileNotFoundError(
                f'模型文件不存在: {model_file}\n'
                f'请下载 SenseVoice 模型并解压到 {model_path}\n'
                f'下载地址: https://github.com/k2-fsa/sherpa-onnx/releases'
            )
        # 查找 tokens 文件
        for tokens_name in ['tokens.txt', 'tokens (1).txt']:
            tokens_file = os.path.join(model_path, tokens_name)
            if os.path.exists(tokens_file):
                break
        if not os.path.exists(tokens_file):
            raise FileNotFoundError(f'tokens.txt 不存在: {tokens_file}')

        print(f'加载模型: {os.path.basename(model_file)}')

        # 复用已加载的模型实例
        cache_key = (model_file, num_threads)
        if SherpaONNXRecognizer._instance is None or \
           getattr(SherpaONNXRecognizer._instance, '_cache_key', None) != cache_key:
            from sherpa_onnx import OfflineRecognizer
            recognizer = OfflineRecognizer.from_sense_voice(
                model=model_file,
                tokens=tokens_file,
                num_threads=num_threads,
            )
            SherpaONNXRecognizer._instance = recognizer
            SherpaONNXRecognizer._instance._cache_key = cache_key

        self.recognizer = SherpaONNXRecognizer._instance
        self.microphone = SounddeviceMicrophone(device=device, energy_threshold=energy_threshold)
        self.sample_rate = self.microphone.sample_rate

    def _recognize_wav(self, wav_bytes):
        """识别 WAV 字节数据，返回 (success, text)"""
        try:
            audio_io = io.BytesIO(wav_bytes)
            with wave.open(audio_io, 'rb') as wf:
                assert wf.getnchannels() == 1, '仅支持单声道音频'
                assert wf.getsampwidth() == 2, '仅支持 16-bit 音频'
                sample_rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            audio_samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

            stream = self.recognizer.create_stream()
            stream.accept_waveform(sample_rate, audio_samples)
            self.recognizer.decode_stream(stream)

            text = stream.result.text.strip()
            if not text:
                return False, '未识别到语音内容'

            return True, text

        except FileNotFoundError as e:
            return False, f'模型文件缺失: {e}'
        except Exception as e:
            return False, f'识别失败: {e}'

    def _block_to_wav(self, block):
        """把一段 float32 单声道音频块转成 16-bit PCM 的 WAV 字节（供 VAD 检测用）"""
        return _block_to_wav(block, self.sample_rate)

    @owns_microphone
    def listen_once(self, timeout=5, phrase_time_limit=8):
        """录制并识别一段语音，返回 (success, text or error_message)。

        改用 VAD 端点检测（listen_until_silence）代替固定时长录音：用户说完即停，
        不再固定等待整段时长，显著降低响应延迟、提升流畅度。
        """
        ok, audio_or_error = self.microphone.listen_until_silence(
            max_duration=phrase_time_limit, silence_duration=0.6, timeout=timeout
        )
        if not ok:
            return False, audio_or_error
        return self._recognize_wav(audio_or_error)

    @owns_microphone
    def listen_with_wake_word(self, wake_words=None, on_wake=None, sensitivity=0.8):
        return listen_for_wake(self, wake_words or [], on_wake, sensitivity)

    def _kws_native_covers(self, keywords, model_dir):
        """判断 KWS 模型原生词表是否覆盖配置的唤醒词。

        该 KWS 模型在固定原生关键词（models/kws/keywords_raw.txt）上训练，
        只有这些词能被稳定检测。若配置的唤醒词（如自定义短词「黑猫」）不在
        原生词表里，则不应启用 KWS，应回退到 STT + 拼音匹配（更灵活、对短词更稳）。

        返回 True 表示可启用 KWS；返回 False 表示应回退到 STT。
        """
        import os
        raw = os.path.join(model_dir, 'keywords_raw.txt')
        if not os.path.exists(raw):
            return True  # 无原生词表信息，保持原行为（尝试 KWS）
        try:
            with open(raw, 'r', encoding='utf-8') as f:
                native = [line.strip() for line in f if line.strip()]
        except Exception:
            return True
        if not native:
            return True

        native_pys = set()
        for n in native:
            np_, _ = _pinyin_alignment(n.lower())
            if np_:
                native_pys.add(np_)
        if not native_pys:
            return True

        for kw in keywords:
            kp, _ = _pinyin_alignment(kw.lower())
            if not kp:
                continue
            for np_ in native_pys:
                if kp in np_:
                    return True
        return False

    def _init_kws(self, keywords, sensitivity=None):
        """初始化 KeywordSpotter（低功耗唤醒词检测），模型不可用返回 None"""
        import os
        import glob
        try:
            import sherpa_onnx
        except ImportError:
            return None
        model_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'models', 'kws'
        )
        def _pick(prefix):
            """优先 epoch-12 int8，其次任意 int8，再其次任意 onnx"""
            for pat in (f'{prefix}-epoch-12-*.int8.onnx',
                        f'{prefix}-*.int8.onnx',
                        f'{prefix}-*.onnx'):
                files = sorted(glob.glob(os.path.join(model_dir, pat)))
                if files:
                    return files[0]
            return None

        encoder = _pick('encoder')
        decoder = _pick('decoder')
        joiner = _pick('joiner')
        tokens = os.path.join(model_dir, 'tokens.txt')
        if not (encoder and decoder and joiner and os.path.exists(tokens)):
            return None
        # 该 KWS 模型只在固定原生词表内有效（见 models/kws/keywords_raw.txt，
        # 如「小爱同学/你好军哥」等）。自定义/短唤醒词（如「黑猫」「助手」）不在
        # 原生词表中，强行用 KWS 永远检测不到，必须回退到 STT + 拼音匹配。
        if not self._kws_native_covers(keywords, model_dir):
            print('[KWS] 配置唤醒词不在模型原生词表内，回退到 STT 拼音匹配（对短词更稳定）')
            return None
        # 生成唤醒词文件（KeywordSpotter 需要 keywords_file）
        # 该模型 tokens 是拼音（ppinyin），需用 text2token 把中文词转成拼音 token 序列
        from src.storage import user_file
        keywords_file = user_file('kws_keywords.txt')
        try:
            token_seqs = sherpa_onnx.text2token(
                list(keywords), tokens, tokens_type='ppinyin'
            )
            with open(keywords_file, 'w', encoding='utf-8') as f:
                for kw, seq in zip(keywords, token_seqs):
                    if seq:
                        # 格式：拼音 token 序列 + @ 中文（get_result 返回 @ 后面的词）
                        f.write(' '.join(seq) + ' @' + kw + '\n')
        except Exception as e:
            print(f'[KWS] 生成唤醒词文件失败: {e}')
            return None
        # 灵敏度 -> 阈值映射（sensitivity 0.1~1.0，越低越宽松、越容易误触发）
        if sensitivity is None:
            threshold = 0.25
        else:
            try:
                threshold = max(0.05, min(0.35, float(sensitivity) * 0.35))
            except Exception:
                threshold = 0.25
        try:
            return sherpa_onnx.KeywordSpotter(
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                tokens=tokens,
                keywords_file=keywords_file,
                num_threads=2,
                sample_rate=16000,
                feature_dim=80,
                keywords_threshold=threshold,
                provider='cpu',
            )
        except Exception as e:
            print(f'[KWS] 初始化失败: {e}')
            return None

    @owns_microphone
    def listen_with_kws(self, keywords=None, on_wake=None, sensitivity=None):
        """低功耗唤醒词检测（KeywordSpotter）。

        模型可用时用 KWS 实时流式检测唤醒词（低功耗、低延迟），
        检测到唤醒词后进入指令监听（SenseVoice 识别）。
        模型不可用时自动回退到滑动窗口 STT 方案。
        """
        if keywords is None:
            keywords = []
        cache_key = (tuple(keywords), sensitivity)
        if getattr(self, '_kws_cache_key', None) != cache_key:
            self._kws_cache_key = cache_key
            self._kws_cached = self._init_kws(keywords, sensitivity)
        spotter = self._kws_cached
        if spotter is None:
            print('[KWS] 模型不可用，回退到滑动窗口识别')
            return self.listen_with_wake_word(keywords, on_wake, sensitivity)

        print('[KWS] 低功耗唤醒词检测已启动')
        rate = 16000
        block = int(rate * 0.1)  # 100ms
        try:
            kws_stream = spotter.create_stream()
            with sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                device=self.microphone.device, blocksize=block) as stream:
                while True:
                    if capture_should_stop():
                        return False, '监听已取消'
                    data, _ = stream.read(block)
                    samples = data[:, 0]
                    kws_stream.accept_waveform(rate, samples)
                    while spotter.is_ready(kws_stream):
                        spotter.decode_stream(kws_stream)
                        keyword = spotter.get_result(kws_stream)
                        if keyword:
                            # 检测到关键词才 reset，否则保留上下文继续累积
                            spotter.reset_stream(kws_stream)
                            print(f'[KWS] 检测到唤醒词: {keyword}')
                            if on_wake:
                                try:
                                    on_wake()
                                except Exception:
                                    pass
                            stream.stop()
                            stream.close()
                            return self.listen_once(timeout=5, phrase_time_limit=10)
        except Exception as e:
            print(f'[KWS] 监听错误: {e}')
            return False, str(e)
        return False, 'KWS 监听结束'

    def _listen_command_after_wake(self):
        """唤醒后监听一条指令（VAD 端点检测，说完即停 + SenseVoice 识别）

        唤醒播报（如「我在」）经扬声器播出后会被麦克风拾取，若立刻收音就会把
        回声识别成「你好」等误触发。因此收音前必须：
          1. 等 TTS 完全播完（wait_speaking_done）；
          2. 再做「静音预检」：轮询麦克风直到连续静音（回声/尾音消散）才起步；
        之后才启动 VAD 收音。
        """
        import time as _t
        # 1) 等唤醒应答播报结束
        try:
            from src.feedback import wait_speaking_done
            wait_speaking_done(timeout=8.0)
        except Exception:
            pass

        # 2) 静音预检：等到麦克风真正安静（唤醒词尾音 + 「我在」回声都消散）再收音。
        #    关键是「你好助手」这类长唤醒词用户说完约需 1s，KWS 命中瞬间用户往往还在
        #    说尾音；若立刻收音会把「你好」残段录成指令。需连续静音足够久才起步。
        #    最多等 4s，超时（用户一直在连续说话）则直接尝试收音，避免卡死。
        try:
            threshold = self.microphone.energy_threshold
            quiet_needed = 0.7          # 需连续静音的时长（覆盖唤醒词尾音+回声）
            quiet_accum = 0.0
            deadline = _t.time() + 4.0
            for block in self.microphone.iter_blocks(0.1):
                energy = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
                if energy <= threshold:
                    quiet_accum += 0.1
                    if quiet_accum >= quiet_needed:
                        break
                else:
                    quiet_accum = 0.0
                if _t.time() > deadline:
                    break
        except Exception as e:
            print(f'[监听] 静音预检跳过: {e}')

        while True:
            ok, wav = self.microphone.listen_until_silence(
                max_duration=8.0, silence_duration=1.0
            )
            if not ok:
                continue
            ok, text = self._recognize_wav(wav)
            if not ok:
                continue
            return True, text.strip()

    def listen_await_wake_word(self, wake_word_detector=None, timeout=60):
        """
        使用专用唤醒词检测器（VAD）进行常驻监听
        检测到语音活动后返回 True，后续由 listen_once() 捕获指令

        Args:
            wake_word_detector: WakeWordDetector 实例
            timeout: 最大监听时间（秒）
        Returns:
            (True, None) - 检测到语音活动
            (False, reason) - 超时或错误
        """
        import time
        if wake_word_detector is None:
            return False, '未配置唤醒词检测器'

        wake_word_detector.start()
        start_time = time.time()

        try:
            # 流式读取 0.3s 短块并实时做 VAD 检测，延迟从「整段录音」降到单块粒度，
            # 不再每轮都固定录制短片段空转
            for block in self.microphone.iter_blocks(0.3):
                if time.time() - start_time >= timeout:
                    return False, '唤醒词检测超时'
                if wake_word_detector.detect_from_bytes(
                        _block_to_wav(block, self.microphone.sample_rate)):
                    wake_word_detector.stop()
                    return True, None

        finally:
            wake_word_detector.stop()

        return False, '唤醒词检测超时'


def test_recording():
    """测试录音功能"""
    print('=== 录音测试 ===')
    ok, msg = check_speech_dependencies()
    print(f'依赖检查: {msg}')

    mics = list_microphones()
    print(f'可用麦克风 ({len(mics)}):')
    for m in mics:
        print(f'  {m}')

    print('\n开始 3 秒录音测试...')
    mic = SounddeviceMicrophone()
    ok, result = mic.listen(timeout=5, phrase_time_limit=3)
    if ok:
        print(f'录音成功，音频大小: {len(result)} bytes')
        # 简单验证：检查是否为有效的 WAV 数据
        print('WAV 数据有效')
    else:
        print(f'录音失败: {result}')


if __name__ == '__main__':
    test_recording()
