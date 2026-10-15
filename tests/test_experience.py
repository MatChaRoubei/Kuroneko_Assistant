import io
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from src import feedback, gui, recognize, stop, storage
from src.lifecycle import busy, capture_cancelled, shutdown, voice_paused


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        shutdown.clear()
        voice_paused.clear()
        capture_cancelled.clear()
        busy.clear()
        stop.begin_output()

    def tearDown(self):
        stop.end_output()
        feedback.clear_speaking()

    def test_atomic_settings_failure_keeps_previous_file_and_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'settings.json'
            storage.write_json(target, {'wake_words': ['黑猫']})
            previous = gui._runtime.copy()
            with patch.object(gui, 'get_settings_file', return_value=str(target)), patch.object(storage.os, 'replace', side_effect=PermissionError):
                self.assertFalse(gui.save_settings(['新唤醒词'], 0.5, 'vits'))
            self.assertEqual(gui._runtime, previous)
            self.assertEqual(storage.read_json(target), {'wake_words': ['黑猫']})
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_bundled_data_goes_to_current_user(self):
        with tempfile.TemporaryDirectory() as folder:
            storage.data_directory.cache_clear()
            try:
                with patch.object(storage.sys, 'frozen', True, create=True), patch.dict(storage.os.environ, {'LOCALAPPDATA': folder}), patch.object(storage, 'app_directory', return_value=Path(folder) / 'read-only-app'):
                    self.assertEqual(storage.data_directory(), Path(folder) / 'KuronekoAssistant')
            finally:
                storage.data_directory.cache_clear()

    def test_sherpa_receives_normalized_float_audio(self):
        engine = recognize.SherpaONNXRecognizer.__new__(recognize.SherpaONNXRecognizer)
        stream = Mock()
        stream.result.text = '现在几点'
        engine.recognizer = Mock()
        engine.recognizer.create_stream.return_value = stream
        data = io.BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', ''))
            wav.writeframes(np.array([-32768, 0, 32767], dtype=np.int16).tobytes())
        self.assertEqual(engine._recognize_wav(data.getvalue()), (True, '现在几点'))
        samples = stream.accept_waveform.call_args.args[1]
        self.assertEqual(samples.dtype, np.float32)
        self.assertLessEqual(float(np.max(np.abs(samples))), 1)

    def test_queue_waits_for_all_sentences_and_survives_engine_exception(self):
        spoken = []
        def speak(text):
            time.sleep(0.03)
            spoken.append(text)
            if text == 'failure':
                raise RuntimeError('simulated')
        with patch.object(feedback, '_speak_cached', side_effect=speak):
            for text in ('first', 'failure', 'last'):
                feedback.say(text)
            feedback.wait_speaking_done(timeout=2)
        self.assertEqual(spoken, ['first', 'failure', 'last'])
        self.assertEqual(feedback._tts_queue.unfinished_tasks, 0)
        self.assertTrue(feedback._tts_worker.is_alive())

    def test_stop_clears_pending_output_and_next_turn_works(self):
        feedback._tts_queue.put('stale')
        stop.request_stop()
        self.assertEqual(feedback._tts_queue.unfinished_tasks, 0)
        stop.begin_output()
        with patch.object(feedback, '_speak_cached') as speak:
            feedback.say('new turn')
            feedback.wait_speaking_done(timeout=2)
            speak.assert_called_once_with('new turn')

    def test_silence_never_triggers_decoder(self):
        microphone = Mock(sample_rate=16000, energy_threshold=0.02)
        microphone.iter_blocks.return_value = iter([np.zeros(1600, dtype=np.float32)] * 100)
        recognizer = Mock(microphone=microphone)
        recognize.listen_for_wake(recognizer, ['黑猫'])
        recognizer._recognize_wav.assert_not_called()

    def test_endpoint_wake_keeps_combined_command(self):
        microphone = Mock(sample_rate=16000, energy_threshold=0.02)
        microphone.iter_blocks.return_value = iter([np.ones(1600, dtype=np.float32) * 0.1] * 6 + [np.zeros(1600, dtype=np.float32)] * 6)
        recognizer = Mock(microphone=microphone)
        recognizer._recognize_wav.return_value = (True, '黑猫现在几点')
        self.assertEqual(recognize.listen_for_wake(recognizer, ['黑猫']), (True, '现在几点'))
        recognizer.listen_once.assert_not_called()

    def test_cancelled_capture_returns_without_opening_audio(self):
        capture_cancelled.set()
        engine = recognize.SherpaONNXRecognizer.__new__(recognize.SherpaONNXRecognizer)
        engine.microphone = Mock()
        self.assertEqual(engine.listen_once(), (False, '监听已取消'))
        engine.microphone.listen_until_silence.assert_not_called()

    def test_cleanup_leaves_other_applications_and_fresh_audio_alone(self):
        from src import cleaner
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            other_app = root / '_MEI_other_app'
            other_app.mkdir()
            (other_app / 'important').write_text('running')
            audio = root / 'assistant_tts.mp3'
            audio.write_bytes(b'playing')
            with patch.object(cleaner, '_TEMP_DIR', folder):
                self.assertEqual(cleaner.cleanup_temp_files(), (0, 0))
            self.assertTrue(audio.exists())
            self.assertTrue(other_app.exists())

    def test_ai_cancellation_closes_stream_without_running_tools(self):
        from src import executor
        response = Mock()
        def chunks():
            stop.request_stop()
            yield {'message': {'content': 'should not speak'}}
        response.__iter__ = Mock(return_value=chunks())
        with patch.object(executor.ollama, 'chat', return_value=response), patch.object(executor, 'get_ollama_model', return_value='fake'), patch.object(executor, '_execute_tool') as execute:
            result = executor._agent_loop()
        self.assertIn('已停止', result[1])
        response.close.assert_called_once()
        execute.assert_not_called()

    def test_submit_reserves_busy_before_worker_consumes(self):
        from src.main import Assistant
        app = Assistant.__new__(Assistant)
        import queue
        app.commands = queue.Queue(maxsize=1)
        app.ready = threading.Event()
        app.ready.set()
        app.ui = Mock()
        self.assertTrue(app.submit('现在几点'))
        self.assertFalse(app.submit('现在几点'))
        self.assertEqual(app.commands.qsize(), 1)

    def test_stop_during_first_token_wait_returns_immediately(self):
        from src import executor
        entered = threading.Event()
        release = threading.Event()
        def delayed_chat(**kwargs):
            entered.set()
            release.wait(2)
            return iter([])
        with patch.object(executor.ollama, 'chat', side_effect=delayed_chat):
            result = []
            consumer = threading.Thread(target=lambda: result.extend(executor._chat_stream(model='fake', stream=True)))
            consumer.start()
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            stop.request_stop()
            consumer.join(timeout=0.4)
            self.assertFalse(consumer.is_alive())
            self.assertLess(time.monotonic() - start, 0.4)
            release.set()

    def test_google_fallback_exposes_same_wake_interface(self):
        engine = recognize.SpeechRecognizer.__new__(recognize.SpeechRecognizer)
        with patch.object(engine, 'listen_with_wake_word', return_value=(True, '现在几点')) as listen:
            self.assertEqual(engine.listen_with_kws(['黑猫'], sensitivity=0.7), (True, '现在几点'))
            self.assertEqual(listen.call_args.args[2], 0.7)

    def test_refresh_preserves_unavailable_model_selection(self):
        from src.settings_ui import model_choices
        self.assertIn('my-model', model_choices('my-model', []))
        self.assertEqual(model_choices('自动选择', ['one', 'one']), ['自动选择', 'one'])

    def test_stop_before_worker_dequeues_prevents_command_execution(self):
        import queue
        from src.main import Assistant
        app = Assistant.__new__(Assistant)
        app.commands = queue.Queue(maxsize=1)
        app.ready = threading.Event()
        app.ready.set()
        app.ui = Mock()
        app.logger = Mock()
        app.corrector = app.fuzzy = app.recognizer = None
        app.plugins = Mock()
        app.update_status = Mock(side_effect=shutdown.set)
        self.assertTrue(app.submit('打开记事本'))
        stop.request_stop()
        with patch('src.executor.execute_intent') as execute:
            app.execute_loop()
        execute.assert_not_called()
        app.plugins.try_execute.assert_not_called()
        self.assertFalse(busy.is_set())
        shutdown.clear()

    def test_settings_and_model_save_as_one_document(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'settings.json')
            previous = gui._runtime.copy()
            try:
                with patch.object(gui, 'get_settings_file', return_value=path):
                    self.assertTrue(gui.save_settings(['黑猫'], 0.6, 'vits', ollama_model='chosen-model', stop_words=['停止']))
                    self.assertEqual(gui.load_selected_model(), 'chosen-model')
                data = storage.read_json(path)
                self.assertEqual(data['ollama_model'], 'chosen-model')
                self.assertEqual(data['stop_words'], ['停止'])
            finally:
                gui._runtime = previous

    def test_wake_acknowledgement_recovers_after_stopped_answer(self):
        stop.request_stop()
        stop.end_output()
        def acknowledge(text):
            self.assertFalse(stop.is_stop_requested())
            return True
        with patch.object(feedback, '_wake_audio', None), patch.object(feedback, 'say_sync', side_effect=acknowledge):
            self.assertTrue(feedback.say_wake())


if __name__ == '__main__':
    unittest.main()
