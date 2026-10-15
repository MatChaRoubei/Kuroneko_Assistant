"""Neural speech endpoints; frame size is transport, never an utterance timer."""
from pathlib import Path
from collections import deque

import numpy as np


class SpeechEndpoint:
    def __init__(self):
        from src.config import get_resource_root
        model = Path(get_resource_root()) / 'models' / 'vad' / 'silero_vad.onnx'
        if not model.is_file():
            raise FileNotFoundError('缺少 Silero VAD 模型，请运行 python setup_models.py。')
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.4
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.max_speech_duration = 20
        config.sample_rate = 16000
        config.num_threads = 1
        self.detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self.history = deque()
        self.received = self.history_size = 0

    def reset(self):
        self.detector.reset()
        self.history.clear()
        self.received = self.history_size = 0

    def accept(self, audio, rate):
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if rate != 16000:
            count = round(len(audio) * 16000 / rate)
            audio = np.interp(np.arange(count) * rate / 16000, np.arange(len(audio)), audio).astype(np.float32)
        self.history.append(audio.copy())
        self.received += len(audio)
        self.history_size += len(audio)
        while self.history_size > 32 * 16000 and len(self.history) > 1:
            self.history_size -= len(self.history.popleft())
        self.detector.accept_waveform(audio)

    def pop(self):
        if self.detector.empty():
            return None
        segment = self.detector.front
        # Neural activation can start after a quiet consonant. Keep 250 ms of
        # real input before its start rather than losing the first wake syllable.
        history_start = self.received - self.history_size
        start = max(history_start, segment.start - 4000)
        end = min(self.received, segment.start + len(segment.samples))
        history = np.concatenate(self.history)
        samples = history[start - history_start:end - history_start].copy()
        self.detector.pop()
        return samples

    @property
    def speaking(self):
        return self.detector.is_speech_detected()
