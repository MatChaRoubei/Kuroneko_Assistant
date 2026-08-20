"""
专用唤醒词检测模块 - 低功耗常驻监听
支持 sherpa-onnx VAD / Porcupine / 纯文本回退
"""
import io
import wave
import numpy as np
from abc import ABC, abstractmethod


class WakeWordDetector(ABC):
    """唤醒词检测器抽象基类"""

    def __init__(self, wake_words=None, sensitivity=0.5):
        self.wake_words = wake_words or []
        self.sensitivity = sensitivity
        self._is_running = False

    @abstractmethod
    def start(self):
        """启动常驻监听"""
        pass

    @abstractmethod
    def stop(self):
        """停止监听"""
        pass

    @abstractmethod
    def detect_once(self, audio_chunk: np.ndarray, sample_rate: int) -> bool:
        """
        检测一段音频是否包含唤醒词
        Args:
            audio_chunk: 音频数据 (numpy array, int16 or float32)
            sample_rate: 采样率
        Returns:
            True if wake word detected
        """
        pass

    def detect_from_bytes(self, wav_bytes: bytes) -> bool:
        """从 WAV 字节数据检测"""
        audio_io = io.BytesIO(wav_bytes)
        try:
            with wave.open(audio_io, 'rb') as wf:
                if wf.getnchannels() != 1:
                    return False
                frames = wf.readframes(wf.getnframes())
                sample_rate = wf.getframerate()
                audio_samples = np.frombuffer(frames, dtype=np.int16)
                return self.detect_once(audio_samples, sample_rate)
        except Exception:
            return False


class SherpaONNXVADWakeWordDetector(WakeWordDetector):
    """
    Sherpa-ONNX VAD (Voice Activity Detection) 唤醒词检测器
    完全离线、中文友好、可以复用已下载的 sherpa-onnx 模型
    https://github.com/k2-fsa/sherpa-onnx
    """

    def __init__(self, wake_words=None, sensitivity=0.5,
                 model_path='models/sense_voice'):
        super().__init__(wake_words, sensitivity)
        import os as _os
        # 向上两级：nlu/wake_word_detector.py -> src/ -> 项目根目录
        _src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        if not _os.path.isabs(model_path):
            model_path = _os.path.join(_src_dir, model_path)
        self.model_path = model_path
        self._vad = None

    def _init_engine(self):
        """初始化 sherpa-onnx VAD 引擎"""
        try:
            from sherpa_onnx import VoiceActivityDetector, VadModelConfig, SileroVadModelConfig
        except ImportError:
            raise RuntimeError(
                "sherpa-onnx 未安装。请运行: pip install sherpa-onnx\n"
                "或使用文本回退模式（设置 wake_word_engine: text_fallback）"
            )

        import os
        # 查找 VAD 模型文件
        vad_dirs = [
            self.model_path,
            os.path.join(self.model_path, 'vad'),
        ]
        vad_model_path = None
        for vad_dir in vad_dirs:
            for name in ['silero_vad.onnx', 'vad.onnx', 'vad.int8.onnx', 'vad_q8.onnx']:
                candidate = os.path.join(vad_dir, name)
                if os.path.exists(candidate):
                    vad_model_path = candidate
                    break
            if vad_model_path:
                break

        if not vad_model_path:
            raise FileNotFoundError(
                f"VAD 模型文件未找到，请检查以下路径:\n"
                f"  {self.model_path}/vad/\n"
                f"下载 VAD 模型: https://github.com/k2-fsa/sherpa-onnx/releases\n"
                f"或设置 wake_word_engine: text_fallback 禁用 VAD"
            )

        self._vad = VoiceActivityDetector(
            config=VadModelConfig(
                num_threads=4,
                silero_vad=SileroVadModelConfig(
                    model=vad_model_path,
                    threshold=0.5,
                    min_silence_duration=0.5,
                    min_speech_duration=0.25,
                    window_size=512,
                    max_speech_duration=20,
                )
            )
        )

    def start(self):
        if self._vad is None:
            try:
                self._init_engine()
            except Exception:
                self._vad = None
        self._is_running = True

    def stop(self):
        self._is_running = False
        if self._vad is not None:
            try:
                self._vad.reset()
            except Exception:
                pass
        self._vad = None

    def detect_once(self, audio_chunk: np.ndarray, sample_rate: int) -> bool:
        """
        使用 VAD 检测语音活动
        返回 True 表示检测到语音（即有人说话）
        """
        if self._vad is None:
            return False

        # 喂入音频数据
        self._vad.accept_waveform(audio_chunk)
        # 检查是否检测到语音
        return self._vad.is_speech_detected()

    def detect_from_bytes(self, wav_bytes: bytes) -> bool:
        """从 WAV 字节数据检测"""
        audio_io = io.BytesIO(wav_bytes)
        try:
            with wave.open(audio_io, 'rb') as wf:
                if wf.getnchannels() != 1:
                    return False
                frames = wf.readframes(wf.getnframes())
                sample_rate = wf.getframerate()
                audio_samples = np.frombuffer(frames, dtype=np.int16)
                return self.detect_once(audio_samples, sample_rate)
        except Exception:
            return False


class TextBasedWakeWordFallback(WakeWordDetector):
    """
    基于文本的唤醒词检测（当前实现的回退方案）
    当专用 VAD 模型不可用时使用
    实际上是在 STT 识别后再做文本匹配
    """

    def __init__(self, wake_words=None, sensitivity=0.8):
        super().__init__(wake_words, sensitivity)
        from difflib import SequenceMatcher
        self._matcher = SequenceMatcher

    def start(self):
        self._is_running = True

    def stop(self):
        self._is_running = False

    def detect_once(self, audio_chunk: np.ndarray, sample_rate: int) -> bool:
        """此方法不支持直接音频检测，总是返回 False"""
        return False

    def detect_from_text(self, text: str) -> bool:
        """从已识别的文本检测唤醒词（由 STT 调用后调用）"""
        if not text or not self.wake_words:
            return False

        normalized = text.lower().strip()
        for wake in self.wake_words:
            wake_lower = wake.lower()
            if wake_lower in normalized:
                return True

            # 模糊匹配
            wake_prefix = wake_lower[:max(2, len(wake_lower) - 2)]
            for i in range(len(normalized) - len(wake_prefix) + 1):
                chunk = normalized[i:i + len(wake_prefix) + 2]
                ratio = self._matcher(None, wake_prefix, chunk).ratio()
                if ratio >= self.sensitivity:
                    return True
        return False


class PorcupineWakeWordDetector(WakeWordDetector):
    """
    Picovoice Porcupine 唤醒词检测器
    轻量级、离线可用、支持多唤醒词
    https://github.com/Picovoice/porcupine
    """

    def __init__(self, wake_words=None, sensitivity=0.5, model_path=None):
        super().__init__(wake_words, sensitivity)
        self.model_path = model_path
        self._porcupine = None

    def _init_engine(self):
        try:
            import porcupine
        except ImportError:
            raise RuntimeError(
                "Porcupine 未安装。请运行: pip install porcupine\n"
                "或使用文本回退模式（设置 wake_word_engine: text_fallback）"
            )

        if not self.model_path or not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"Porcupine 模型文件未找到: {self.model_path}\n"
                "请从 https://console.picovoice.ai/ 下载 .ppn 模型文件\n"
                "或设置 wake_word_engine: text_fallback 禁用 Porcupine"
            )

        self._porcupine = porcupine.create(
            keyword_paths=[self.model_path],
            sensitivities=[self.sensitivity],
        )

    def start(self):
        if self._porcupine is None:
            self._init_engine()
        self._is_running = True

    def stop(self):
        self._is_running = False
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None

    def detect_once(self, audio_chunk: np.ndarray, sample_rate: int) -> bool:
        if self._porcupine is None:
            return False

        # Porcupine 需要 16kHz 采样率
        if sample_rate != 16000:
            import scipy.signal
            num_samples = int(len(audio_chunk) * 16000 / sample_rate)
            audio_chunk = scipy.signal.resample(audio_chunk, num_samples)

        if audio_chunk.dtype == np.float32:
            audio_chunk = (audio_chunk * 32767).astype(np.int16)

        result = self._porcupine.process(audio_chunk)
        return result >= 0


import os


def create_wake_word_detector(config: dict):
    """
    工厂函数：根据配置创建合适的唤醒词检测器
    """
    wake_words = config.get('wake_words', [])
    engine = config.get('wake_word_engine', 'text_fallback')

    if engine == 'porcupine':
        return PorcupineWakeWordDetector(
            wake_words=wake_words,
            sensitivity=config.get('wake_word_sensitivity', 0.5),
            model_path=config.get('porcupine_model_path'),
        )
    elif engine == 'sherpaonnx_vad':
        try:
            return SherpaONNXVADWakeWordDetector(
                wake_words=wake_words,
                sensitivity=config.get('wake_word_sensitivity', 0.5),
                model_path=config.get('speech_model_path', 'models/sense_voice'),
            )
        except Exception as e:
            import logging
            logging.warning(f'sherpa-onnx VAD 初始化失败，回退到文本模式: {e}')
            return TextBasedWakeWordFallback(
                wake_words=wake_words,
                sensitivity=config.get('wake_word_sensitivity', 0.8),
            )
    elif engine == 'text_fallback':
        return TextBasedWakeWordFallback(
            wake_words=wake_words,
            sensitivity=config.get('wake_word_sensitivity', 0.8),
        )
    else:
        import logging
        logging.warning(f"未知的唤醒词引擎: {engine}，使用文本模式")
        return TextBasedWakeWordFallback(wake_words=wake_words)
