import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from src.nlu.commands import parse_command, duration_seconds
from src.nlu.phonetic_corrector import PhoneticCorrector
from src.intents import IntentParser
from src.nlu.fuzzy_regex import FuzzyRegexMatcher
from src.reminders import ReminderManager
from src.lifecycle import shutdown, voice_paused, capture_cancelled


class VoiceFunctionality(unittest.TestCase):
    def test_both_parsers_preserve_meaning_and_extract_spoken_values(self):
        path = str(Path(__file__).resolve().parents[1] / 'data/intents.json')
        parsers = (IntentParser(path).parse, FuzzyRegexMatcher(path).match)
        corrector = PhoneticCorrector(path)
        cases = [
            ('五分钟后提醒我喝水', 'set_timer', {'seconds': 300, 'content': '喝水'}),
            ('帮我设置五分钟定时', 'set_timer', {'seconds': 300}),
            ('半小时后提醒我休息', 'set_timer', {'seconds': 1800}),
            ('一分钟三十秒后提醒我看烤箱', 'set_timer', {'seconds': 90}),
            ('取消第十二号提醒', 'cancel_reminder', {'target': 12}),
            ('取消所有提醒', 'cancel_reminder', {'target': 'all'}),
            ('查看提醒', 'list_reminders', {}),
            ('音量调到百分之五十', 'set_volume', {'value': 50}),
            ('音量大一点', 'adjust_volume', {'delta': 10}),
            ('取消静音', 'mute_volume', {'muted': False}),
            ('不要关机', 'unknown', {}), ('怎么关闭程序', 'unknown', {}),
            ('记住我喜欢三文鱼', 'remember', {'content': '我喜欢三文鱼'}),
            ('搜索一下三体', 'web_search', {'query': '三体'}),
            ('暂停音乐', 'media_control', {'action': 'play_pause'}),
            ('最小化当前窗口', 'window_control', {'action': 'minimize'}),
            ('取消关机', 'cancel_shutdown', {}),
            ('开启连续对话', 'dialogue_mode', {'enabled': True}),
            ('结束对话', 'dialogue_mode', {'enabled': False}),
        ]
        for parser in parsers:
            for phrase, expected, slots in cases:
                with self.subTest(parser=parser, phrase=phrase):
                    intent, result = parser(corrector.correct(phrase))
                    self.assertEqual(intent, expected)
                    for name, value in slots.items():
                        self.assertEqual(result[name], value)

    def test_reminder_survives_restart_and_only_fires_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'reminders.json')
            clock = [1000]
            delivered = []
            first = ReminderManager(path, delivered.append, clock=lambda: clock[0])
            self.assertTrue(first.add('5', '喝水')[0])
            second = ReminderManager(path, delivered.append, clock=lambda: clock[0])
            clock[0] += 6
            second.tick()
            second.tick()
            self.assertEqual([item['title'] for item in delivered], ['喝水'])
            self.assertEqual(second.list(), [])

    def test_reminder_cancel_and_invalid_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ReminderManager(str(Path(folder) / 'reminders.json'), Mock())
            for seconds in ('oops', -1, float('nan'), 99999999):
                self.assertFalse(manager.add(seconds)[0])
            manager.add(1, 'one'); manager.add(2, 'two')
            self.assertTrue(manager.cancel('latest')[0])
            self.assertEqual([item['title'] for item in manager.list()], ['one'])
            self.assertTrue(manager.cancel('all')[0])

    def test_audio_failure_is_reported_as_failure(self):
        from src import executor
        with patch.object(executor, '_audio_endpoint', side_effect=RuntimeError('offline')):
            self.assertFalse(executor.set_volume(50)[0])
            self.assertFalse(executor.control_volume('read')[0])
        self.assertFalse(executor.set_volume(None)[0])

    def test_volume_delta_uses_current_volume_without_changing_real_device(self):
        from src import executor
        context = MagicMock()
        endpoint = context.__enter__.return_value
        endpoint.GetMasterVolumeLevelScalar.return_value = 0.95
        with patch.object(executor, '_audio_endpoint', return_value=context):
            self.assertTrue(executor.control_volume('adjust', 10)[0])
        endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(1, None)

    def test_stop_word_inside_content_is_not_a_stop_command(self):
        from src.recognize import is_stop_command
        self.assertTrue(is_stop_command('请停止', ['停止']))
        self.assertFalse(is_stop_command('搜索一下停止词', ['停止']))
        self.assertFalse(is_stop_command('不要停止', ['停止']))

    def test_low_sensitivity_does_not_wake_on_generic_hello(self):
        from src.recognize import match_wake_word
        self.assertFalse(match_wake_word('你好', ['你好黑猫'], threshold=0.3)[0])
        self.assertEqual(match_wake_word('你好黑毛现在几点', ['你好黑猫']), (True, '现在几点'))

    def test_neural_endpoint_ignores_continuous_fan_tone(self):
        from src.endpoint import SpeechEndpoint
        import numpy as np
        model = Path(__file__).resolve().parents[1] / 'models/vad/silero_vad.onnx'
        if not model.exists():
            self.skipTest('Run setup_models.py to download the neural VAD model')
        endpoint = SpeechEndpoint()
        noise = (np.sin(np.arange(16000 * 3) * 2 * np.pi * 140 / 16000) * 0.035).astype(np.float32)
        for offset in range(0, len(noise) - 512, 512):
            endpoint.accept(noise[offset:offset + 512], 16000)
            self.assertIsNone(endpoint.pop())

    def test_neural_capture_returns_full_speech_and_closes_input(self):
        from src.recognize import SounddeviceMicrophone
        import numpy as np
        mic = SounddeviceMicrophone.__new__(SounddeviceMicrophone)
        mic.sample_rate = 16000
        mic.endpoint = Mock()
        mic.endpoint.speaking = True
        mic.endpoint.pop.return_value = np.ones(3200, dtype=np.float32) * 0.1
        closed = []
        def blocks(_):
            try:
                yield np.ones(512, dtype=np.float32)
            finally:
                closed.append(True)
        mic.iter_blocks = blocks
        shutdown.clear(); capture_cancelled.clear(); voice_paused.clear()
        utterances = mic.iter_utterances()
        wav = next(utterances)
        utterances.close()
        self.assertTrue(wav.startswith(b'RIFF'))
        self.assertEqual(closed, [True])

    def test_manual_capture_works_when_background_listening_is_paused(self):
        from src.recognize import capture_context, capture_should_stop
        shutdown.clear(); capture_cancelled.clear(); voice_paused.set()
        try:
            capture_context.manual = True
            self.assertFalse(capture_should_stop())
            capture_cancelled.set()
            self.assertTrue(capture_should_stop())
        finally:
            capture_context.manual = False
            voice_paused.clear(); capture_cancelled.clear()

    def test_character_voice_keeps_chinese_japanese_language_and_explicit_source(self):
        from src.character_tts import make_request, validate_profile
        address, body = make_request('我在。ふん。', {'ref_audio_path': 'C:/voice/kuroneko.wav', 'prompt_lang': 'ja'})
        self.assertEqual(address, 'http://127.0.0.1:9880/tts')
        self.assertEqual(body['text_lang'], 'auto')
        self.assertEqual(body['prompt_lang'], 'ja')
        with self.assertRaises(ValueError):
            validate_profile({'ref_audio_path': ''})
        with self.assertRaises(ValueError):
            validate_profile({'url': 'https://external.example', 'ref_audio_path': 'sample.wav'})


if __name__ == '__main__':
    unittest.main()
