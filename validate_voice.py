"""Offline speech -> neural endpoint -> ASR -> intent benchmark, with no OS actions."""
import json
import time
from pathlib import Path

import numpy as np
from sherpa_onnx import OfflineRecognizer

from src.endpoint import SpeechEndpoint
from src.feedback import _init_vits
from src.recognize import SherpaONNXRecognizer, _block_to_wav, match_wake_word
from src.intents import IntentParser
from src.nlu.phonetic_corrector import PhoneticCorrector

ROOT = Path(__file__).resolve().parent


def main():
    tts = _init_vits()
    if tts is None:
        raise RuntimeError('缺少本地 TTS 测试模型')
    asr = SherpaONNXRecognizer.__new__(SherpaONNXRecognizer)
    model = ROOT / 'models' / 'sense_voice'
    asr.recognizer = OfflineRecognizer.from_sense_voice(model=str(model / 'model.int8.onnx'), tokens=str(model / 'tokens.txt'), num_threads=2)
    parser = IntentParser(str(ROOT / 'data/intents.json'))
    corrector = PhoneticCorrector()
    endpoint = SpeechEndpoint()
    commands = [('现在几点', 'get_time'), ('五分钟后提醒我喝水', 'set_timer'),
                ('音量调到百分之五十', 'set_volume'), ('搜索一下三体', 'web_search'),
                ('不要关机', 'unknown'), ('下一首', 'media_control')]
    cases = [(prefix + command, expected, prefix == '你好黑猫')
             for prefix in ('你好黑猫', '黑猫') for command, expected in commands]
    results = []
    for phrase, expected, required in cases:
        audio = tts.generate(phrase, sid=0, speed=1)
        samples = np.asarray(audio.samples, dtype=np.float32)
        samples = np.interp(np.arange(round(len(samples) * 16000 / audio.sample_rate)) * audio.sample_rate / 16000,
                            np.arange(len(samples)), samples).astype(np.float32)
        voiced = samples[np.abs(samples) > 0.005]
        gain = min(0.75 / max(float(np.max(np.abs(samples))), 0.001),
                   0.12 / max(float(np.sqrt(np.mean(voiced ** 2))), 0.001))
        samples *= gain
        samples = np.concatenate([np.zeros(4800, dtype=np.float32), samples])
        last_sound = np.flatnonzero(np.abs(samples) > 0.01)[-1] / 16000
        # A fan-like tone above the old RMS threshold continues after speech.
        padded = np.concatenate([samples, np.zeros(16000 * 2, dtype=np.float32)])
        padded += (np.sin(np.arange(len(padded)) * (2 * np.pi * 140 / 16000)) * 0.035).astype(np.float32)
        endpoint.reset()
        segment = None
        detected_at = None
        started = time.perf_counter()
        for offset in range(0, len(padded) - 512 + 1, 512):
            endpoint.accept(padded[offset:offset + 512], 16000)
            segment = endpoint.pop()
            if segment is not None:
                detected_at = (offset + 512) / 16000
                break
        vad_cpu = time.perf_counter() - started
        ok, text = asr._recognize_wav(_block_to_wav(segment, 16000)) if segment is not None else (False, '无语音端点')
        asr_time = time.perf_counter() - started - vad_cpu
        wake, command = match_wake_word(text, ['你好黑猫', '黑猫']) if ok else (False, '')
        intent, slots = parser.parse(corrector.correct(command)) if wake else ('wake_missed', {})
        record = {'phrase': phrase, 'recognized': text, 'expected': expected, 'actual': intent, 'slots': slots,
                  'passed': intent == expected, 'required': required,
                  'endpoint_ms': round((detected_at - last_sound) * 1000) if detected_at else None,
                  'asr_seconds': round(asr_time, 3), 'vad_cpu_seconds': round(vad_cpu, 3)}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    output = ROOT / 'docs' / 'voice_validation.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'VOICE_BENCHMARK {sum(item["passed"] for item in results)}/{len(results)}')
    if not all(item['passed'] for item in results if item['required']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
