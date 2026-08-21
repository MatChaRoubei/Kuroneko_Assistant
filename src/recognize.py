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


def check_speech_dependencies():
    """检查语音依赖是否满足"""
    try:
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        if not input_devices:
            return False, '检测不到麦克风设备，请插入麦克风或检查系统设置'
        default_input = sd.query_devices(kind='input')
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

    def listen_until_silence(self, max_duration=15.0, silence_duration=1.2,
                             energy_threshold=None):
        if energy_threshold is None:
            energy_threshold = self.energy_threshold
        """持续录音：检测到语音开始后，直到静音结束。
        返回 (success, wav_bytes or error)"""
        import time as _time

        rate = int(self.sample_rate)
        block = max(int(rate * 0.1), 256)  # 100ms 一片

        audio_chunks = []
        speech_started = False
        silence_accum = 0.0
        total = 0.0

        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                device=self.device, blocksize=block) as stream:
                while True:
                    data, _ = stream.read(block)
                    mono = data[:, 0]
                    energy = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))

                    if energy > energy_threshold:
                        if not speech_started:
                            speech_started = True
                            print('[监听] 检测到语音，开始录音...')
                        silence_accum = 0.0
                        audio_chunks.append(data)
                    elif speech_started:
                        silence_accum += block / rate
                        audio_chunks.append(data)
                        if silence_accum >= silence_duration:
                            print('[监听] 检测到静音，录音结束')
                            break

                    total += block / rate
                    if total >= max_duration:
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


def match_wake_word(text, wake_words, threshold=0.8):
    """在识别文本中查找唤醒词，返回 (是否命中, 唤醒词后的剩余文本)。

    text: 已转小写并去除首尾空白的文本
    wake_words: 唤醒词列表
    """
    from difflib import SequenceMatcher

    if not text or not wake_words:
        return False, text

    remaining = text
    for wake in wake_words:
        wake_lower = wake.lower()
        if wake_lower in remaining:
            remaining = remaining.replace(wake_lower, '', 1).strip()
            print(f'[唤醒] 精确匹配唤醒词 [{wake}]，剩余指令: [{remaining}]')
            return True, remaining

        # 模糊匹配：ASR 可能把唤醒词识别错
        wake_prefix = wake_lower[:max(2, len(wake_lower) - 2)]
        for i in range(len(remaining) - len(wake_prefix) + 1):
            chunk = remaining[i:i + len(wake_prefix) + 2]
            ratio = SequenceMatcher(None, wake_prefix, chunk).ratio()
            if ratio >= threshold:
                remaining = remaining[i + len(wake_prefix) + 2:].strip()
                print(f'[唤醒] 模糊匹配唤醒词 [{wake}]（相似度 {ratio:.2f}），剩余指令: [{remaining}]')
                return True, remaining

    return False, text


class SpeechRecognizer:
    """
    语音识别器，支持中文识别
    使用 sounddevice 采集 + Google 语音识别
    """
    def __init__(self, language='zh-CN'):
        ok, msg = check_speech_dependencies()
        if not ok:
            raise RuntimeError(msg)
        self.recognizer = sr.Recognizer()
        self.language = language
        self.microphone = SounddeviceMicrophone()

    def _recognize_with_retry(self, audio, retries=3, delay=1):
        """带重试的语音识别，处理网络连接错误"""
        import time
        last_error = None
        for attempt in range(retries):
            try:
                text = self.recognizer.recognize_google(audio, language=self.language)
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

    def listen_once(self, timeout=5, phrase_time_limit=8):
        """
        录制并识别一段语音，返回 (success, text or error_message)
        """
        ok, audio_or_error = self.microphone.listen(timeout=timeout, phrase_time_limit=phrase_time_limit)
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

    def listen_with_wake_word(self, wake_words=None, retries=2):
        """持续监听：检测语音 -> 录音到静音 -> 识别 -> 检查唤醒词。
        唤醒后进入聆听状态，直到用户说完指令。"""
        if wake_words is None:
            wake_words = []

        while True:
            ok, wav = self.microphone.listen_until_silence()
            if not ok:
                print(f'[监听] {wav}')
                continue

            ok, text = self._recognize_wav(wav)
            if not ok:
                print(f'[监听] {text}')
                continue

            normalized = text.lower().strip()
            print(f'[调试] 识别结果: [{text}] -> normalized: [{normalized}]')

            if not wake_words:
                return True, normalized

            found_wake = False
            remaining = normalized
            for wake in wake_words:
                wake_lower = wake.lower()
                if wake_lower in remaining:
                    remaining = remaining.replace(wake_lower, '', 1).strip()
                    found_wake = True
                    print(f'[调试] 精确匹配唤醒词 [{wake}] 成功，剩余: [{remaining}]')
                    break

                from difflib import SequenceMatcher
                wake_prefix = wake_lower[:max(2, len(wake_lower) - 2)]
                for i in range(len(remaining) - len(wake_prefix) + 1):
                    chunk = remaining[i:i + len(wake_prefix) + 2]
                    ratio = SequenceMatcher(None, wake_prefix, chunk).ratio()
                    if ratio >= 0.8:
                        remaining = remaining[i + len(wake_prefix) + 2:].strip()
                        found_wake = True
                        print(f'[调试] 模糊匹配唤醒词 [{wake}] 成功（相似度 {ratio:.2f}），剩余: [{remaining}]')
                        break
                if found_wake:
                    break

            if not found_wake:
                print('未检测到唤醒词，继续监听...')
                continue

            if remaining:
                # 唤醒词 + 指令一起说
                return True, remaining

            # 只说唤醒词：进入聆听状态，等用户说指令
            print('已唤醒，请说出指令...')
            ok, wav2 = self.microphone.listen_until_silence()
            if not ok:
                continue
            ok, text2 = self._recognize_wav(wav2)
            if not ok:
                continue
            return True, text2.strip()

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
            while time.time() - start_time < timeout:
                # 录制短音频片段（约 0.3 秒）
                ok, audio_or_err = self.microphone.listen(
                    timeout=5,
                    phrase_time_limit=0.5
                )
                if not ok:
                    continue

                # VAD 检测是否包含语音
                if wake_word_detector.detect_from_bytes(audio_or_err):
                    wake_word_detector.stop()
                    return True, None

        finally:
            wake_word_detector.stop()

        return False, '唤醒词检测超时'


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

    def __init__(self, model_path='models/sense_voice', num_threads=2, energy_threshold=0.02):
        """
        初始化 sherpa-onnx 识别器

        Args:
            model_path: 模型文件夹路径（包含 model.onnx 和 tokens.txt）
            num_threads: CPU 推理线程数
        """
        ok, msg = check_speech_dependencies()
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
        self.microphone = SounddeviceMicrophone(energy_threshold=energy_threshold)
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

            audio_samples = np.frombuffer(frames, dtype=np.int16)

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

    def listen_once(self, timeout=5, phrase_time_limit=8):
        """录制并识别一段语音，返回 (success, text or error_message)"""
        ok, audio_or_error = self.microphone.listen(timeout=timeout, phrase_time_limit=phrase_time_limit)
        if not ok:
            return False, audio_or_error
        return self._recognize_wav(audio_or_error)

    def listen_with_wake_word(self, wake_words=None, on_wake=None, sensitivity=0.8):
        """持续监听唤醒词（高噪音环境友好）。

        不再依赖能量阈值切分语音（环境噪音大时能量阈值不可靠），
        改为持续流式录音 + 滑动窗口定期识别，靠本地 STT（SenseVoice）
        的抗噪能力从背景噪音中识别出唤醒词。

        Args:
            wake_words: 唤醒词列表
            on_wake: 命中唤醒词瞬间的回调（如播报"我在"）
        Returns:
            (True, 指令文本) 或 (False, 错误信息)
        """
        if wake_words is None:
            wake_words = []

        rate = int(self.sample_rate)
        window = 3.0   # 每次识别的音频窗口（秒）
        step = 1.5     # 窗口前进步长（秒），重叠以降低漏检
        block = int(rate * 0.1)
        window_frames = int(rate * window)
        step_frames = int(rate * step)

        buffer = np.zeros((0,), dtype=np.float32)
        _fail_count = 0

        def _to_wav(audio):
            audio_int16 = np.int16(np.clip(audio, -1.0, 1.0) * 32767)
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(audio_int16.tobytes())
            return buf.getvalue()

        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                device=self.microphone.device, blocksize=block) as stream:
                while True:
                    # 采集 step 秒音频
                    collected = 0
                    parts = []
                    while collected < step_frames:
                        data, _ = stream.read(block)
                        parts.append(data[:, 0])
                        collected += data.shape[0]
                    new_audio = np.concatenate(parts)

                    buffer = np.concatenate([buffer, new_audio])
                    if buffer.shape[0] > window_frames:
                        buffer = buffer[-window_frames:]
                    if buffer.shape[0] < window_frames:
                        continue  # 窗口还没攒满，继续采集

                    ok, text = self._recognize_wav(_to_wav(buffer))
                    if not ok:
                        _fail_count += 1
                        if _fail_count % 10 == 1:
                            print(f'[监听] 未识别到内容（已 {_fail_count} 次），继续监听...')
                        continue
                    _fail_count = 0

                    normalized = text.lower().strip()
                    print(f'[监听] 识别: [{text}]')

                    if not wake_words:
                        return True, normalized

                    found_wake, remaining = match_wake_word(normalized, wake_words, threshold=sensitivity)
                    if not found_wake:
                        continue

                    # 命中唤醒词：触发回调（如播报"我在"）
                    if on_wake:
                        try:
                            on_wake()
                        except Exception:
                            pass

                    if remaining:
                        return True, remaining

                    # 只说唤醒词：进入聆听指令状态（固定时长录音识别指令）
                    print('[唤醒] 已唤醒，请说出指令...')
                    while True:
                        ok2, wav2 = self.microphone.record_fixed(6.0)
                        if not ok2:
                            continue
                        ok2, text2 = self._recognize_wav(wav2)
                        if not ok2:
                            continue
                        return True, text2.strip()
        except Exception as e:
            print(f'[监听] 录音错误: {e}')
            return False, str(e)

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
        # 生成唤醒词文件（KeywordSpotter 需要 keywords_file）
        # 该模型 tokens 是拼音（ppinyin），需用 text2token 把中文词转成拼音 token 序列
        keywords_file = os.path.join(model_dir, 'keywords.txt')
        try:
            token_seqs = sherpa_onnx.text2token(
                list(keywords), tokens, tokens_type='ppinyin'
            )
            with open(keywords_file, 'w', encoding='utf-8') as f:
                for seq in token_seqs:
                    if seq:
                        f.write(' '.join(seq) + '\n')
        except Exception as e:
            print(f'[KWS] 生成唤醒词文件失败: {e}')
            return None
        # 灵敏度 -> 阈值映射（sensitivity 0.1~1.0，越低越宽松、越容易误触发）
        if sensitivity is None:
            threshold = 0.25
        else:
            try:
                threshold = max(0.05, min(0.5, float(sensitivity) * 0.5))
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

    def listen_with_kws(self, keywords=None, on_wake=None, sensitivity=None):
        """低功耗唤醒词检测（KeywordSpotter）。

        模型可用时用 KWS 实时流式检测唤醒词（低功耗、低延迟），
        检测到唤醒词后进入指令监听（SenseVoice 识别）。
        模型不可用时自动回退到滑动窗口 STT 方案。
        """
        if keywords is None:
            keywords = []
        spotter = self._init_kws(keywords, sensitivity)
        if spotter is None:
            print('[KWS] 模型不可用，回退到滑动窗口识别')
            return self.listen_with_wake_word(keywords, on_wake, sensitivity)

        print('[KWS] 低功耗唤醒词检测已启动')
        rate = 16000
        block = int(rate * 0.1)  # 100ms
        try:
            kws_stream = spotter.create_stream()
            with sd.InputStream(samplerate=rate, channels=1, dtype='int16',
                                device=self.microphone.device, blocksize=block) as stream:
                while True:
                    data, _ = stream.read(block)
                    samples = data[:, 0]
                    kws_stream.accept_waveform(rate, samples)
                    while spotter.is_ready(kws_stream):
                        spotter.decode_stream(kws_stream)
                        keyword = spotter.get_result(kws_stream)
                        spotter.reset_stream(kws_stream)
                        if keyword:
                            print(f'[KWS] 检测到唤醒词: {keyword}')
                            if on_wake:
                                try:
                                    on_wake()
                                except Exception:
                                    pass
                            return self._listen_command_after_wake()
        except Exception as e:
            print(f'[KWS] 监听错误: {e}')
            return False, str(e)
        return False, 'KWS 监听结束'

    def _listen_command_after_wake(self):
        """唤醒后监听一条指令（固定时长录音 + SenseVoice 识别）"""
        while True:
            ok, wav = self.microphone.record_fixed(6.0)
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
            while time.time() - start_time < timeout:
                ok, audio_or_err = self.microphone.listen(
                    timeout=5,
                    phrase_time_limit=0.5
                )
                if not ok:
                    continue

                if wake_word_detector.detect_from_bytes(audio_or_err):
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
