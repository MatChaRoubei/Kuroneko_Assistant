import asyncio
import unittest
from unittest.mock import patch

from src import feedback
from src.character_tts import check_readiness
from src.voice_health import azure_endpoint, edge_audio, error_message, probe


class VoiceConnections(unittest.TestCase):
    def test_legacy_endpoint_is_migrated_once_and_future_endpoint_preserved(self):
        import edge_tts.communicate as communicate
        import edge_tts.constants as constants
        from edge_tts.drm import DRM
        from src.edge_compat import ensure_compatibility
        legacy = 'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=test'
        with patch.object(communicate, 'WSS_URL', legacy), patch.object(constants, 'WSS_URL', legacy), patch.object(DRM, 'parse_rfc2616_date'):
            self.assertTrue(ensure_compatibility())
            self.assertIn('api.msedgeservices.com/tts/', communicate.WSS_URL)
            self.assertFalse(ensure_compatibility())
        with patch.object(communicate, 'WSS_URL', 'wss://future.microsoft.example/tts'):
            self.assertFalse(ensure_compatibility())
            self.assertEqual(communicate.WSS_URL, 'wss://future.microsoft.example/tts')

    def test_http_date_parser_is_independent_of_process_locale(self):
        from src.edge_compat import parse_server_date
        self.assertEqual(parse_server_date('Mon, 12 Oct 2026 03:16:09 GMT'), 1791774969.0)
        self.assertEqual(parse_server_date('Mon, 12 Oct 2026 03:16:09 +0000'), 1791774969.0)
        self.assertIsNone(parse_server_date('not a date'))
        self.assertIsNone(parse_server_date(''))

    def test_sync_playback_does_not_claim_success_when_every_engine_failed(self):
        with patch.object(feedback, '_speak_cached', return_value=False):
            self.assertFalse(feedback.say_sync('测试'))

    def test_azure_uses_the_resources_cloud_and_rejects_url_input(self):
        self.assertIn('japaneast.tts.speech.microsoft.com/', azure_endpoint('japaneast'))
        self.assertIn('chinaeast2.tts.speech.azure.cn/', azure_endpoint('chinaeast2'))
        self.assertIn('usgovvirginia.tts.speech.azure.us/', azure_endpoint('usgovvirginia'))
        with self.assertRaises(ValueError):
            azure_endpoint('eastus.example/path')

    def test_403_message_does_not_leak_request_url(self):
        exc = RuntimeError('https://proxy:secret@host/?TrustedClientToken=secret')
        exc.status = 403
        message = error_message(exc)
        self.assertIn('403', message)
        self.assertNotIn('secret', message)

    def test_edge_rejects_empty_audio_and_closes_stream(self):
        closed = []
        async def chunks():
            try:
                yield {'type': 'SentenceBoundary'}
            finally:
                closed.append(True)
        with patch('edge_tts.Communicate') as communicate:
            communicate.return_value.stream.side_effect = chunks
            with self.assertRaises(RuntimeError):
                asyncio.run(edge_audio('测试', 'zh-CN-XiaoxiaoNeural', '+0%', '+0Hz'))
        self.assertEqual(closed, [True])

    def test_edge_cancellation_closes_pending_connection(self):
        closed = []
        async def chunks():
            try:
                await asyncio.sleep(30)
                yield {'type': 'audio', 'data': b'test'}
            finally:
                closed.append(True)
        polls = []
        def cancelled():
            polls.append(True)
            return len(polls) > 1
        with patch('edge_tts.Communicate') as communicate:
            communicate.return_value.stream.side_effect = chunks
            self.assertIsNone(asyncio.run(edge_audio('测试', 'voice', '+0%', '+0Hz', cancelled)))
        self.assertEqual(closed, [True])

    def test_gpt_missing_service_reports_deployment_not_model_success(self):
        import httpx
        with patch('src.character_tts.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError('connection refused')
            ok, message = probe('gptsovits', {'character': {}})
        self.assertFalse(ok)
        self.assertIn('不包含 GPT-SoVITS 服务', message)

    def test_gpt_rejects_another_http_service_and_missing_audio(self):
        with patch('src.character_tts.httpx.Client') as client:
            response = client.return_value.__enter__.return_value.get.return_value
            response.status_code = 200
            response.json.return_value = {'paths': {'/chat': {}}}
            with self.assertRaisesRegex(RuntimeError, '/tts'):
                check_readiness({'ref_audio_path': 'missing.wav'})
            response.json.return_value = {'paths': {'/tts': {'post': {}}}}
            with self.assertRaisesRegex(ValueError, '不存在'):
                check_readiness({'ref_audio_path': 'missing.wav'})

    def test_success_requires_actual_gpt_waveform_without_playback(self):
        import numpy as np
        with patch('src.character_tts.check_readiness'), patch('src.character_tts.synthesize', return_value=(np.ones(1600), 16000)), patch('sounddevice.play') as play:
            ok, message = probe('gptsovits', {'character': {}})
            self.assertTrue(ok)
            self.assertIn('合成成功', message)
            play.assert_not_called()

    def test_online_failure_prefers_local_natural_voice_and_reports_fallback(self):
        from src.stop import begin_output, end_output
        previous = feedback._engine_pref
        begin_output()
        try:
            feedback.configure_tts('edge')
            with patch.object(feedback, '_speak_with_edge', return_value=False), patch.object(feedback, '_speak_with_vits', return_value=True), patch.object(feedback, '_speak_with_pyttsx3') as system, patch('src.gui.append_system') as notice:
                self.assertTrue(feedback._speak_cached('测试'))
                system.assert_not_called()
                self.assertTrue(any('本地自然语音' in str(args) for args in notice.call_args_list))
        finally:
            feedback.configure_tts(previous)
            end_output()


if __name__ == '__main__':
    unittest.main()
