"""Exercise Tk widget behavior with a withdrawn root, without desktop input."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from src import gui
from src.lifecycle import busy, shutdown, voice_paused


class WindowInteractions(unittest.TestCase):
    def setUp(self):
        shutdown.clear()
        busy.clear()
        voice_paused.clear()
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()
        shutdown.clear()
        voice_paused.clear()

    def build_window(self, callback):
        ui = gui.MainWindow(on_submit=Mock(return_value=True))
        result = []
        def exercise():
            try:
                callback(ui)
            except BaseException as exc:
                result.append(exc)
            finally:
                self.root.quit()
        self.root.after(70, exercise)
        with patch.object(tk, 'Tk', return_value=self.root), patch('src.lifecycle.request_exit'):
            ui.run()
        if result:
            raise result[0]

    def test_input_history_preserves_unsubmitted_draft(self):
        def exercise(ui):
            ui._set_controls(True, False)
            ui.entry.insert(0, '现在几点')
            ui._submit()
            ui.entry.insert(0, '还没写完的内容')
            ui._recall(-1)
            self.assertEqual(ui.entry.get(), '现在几点')
            ui._recall(1)
            self.assertEqual(ui.entry.get(), '还没写完的内容')
        self.build_window(exercise)

    def test_busy_input_is_retained_with_clear_feedback(self):
        def exercise(ui):
            ui._set_controls(True, True)
            ui.entry.insert(0, '下一条指令')
            ui._submit()
            self.assertEqual(ui.entry.get(), '下一条指令')
            self.assertIn('输入内容会保留', ui.input_hint.cget('text'))
            self.assertEqual(ui.send_button.cget('text'), '处理中…')
        self.build_window(exercise)

    def test_idle_escape_does_not_cancel_next_wake(self):
        def exercise(ui):
            with patch('src.stop.request_stop') as stop:
                ui._stop()
                stop.assert_not_called()
        self.build_window(exercise)

    def test_retry_unpauses_capture(self):
        def exercise(ui):
            voice_paused.set()
            ui._on_retry = Mock()
            ui._retry_microphone()
            self.assertFalse(voice_paused.is_set())
            ui._on_retry.assert_called_once()
        self.build_window(exercise)

    def test_settings_preserve_model_and_disable_irrelevant_options(self):
        from src.settings_ui import SettingsWindow, ENGINE_OPTIONS
        ui = Mock(root=self.root)
        ui._post = lambda fn: fn()
        previous = gui._runtime.copy()
        try:
            gui._runtime['tts_engine'] = 'vits'
            with patch.object(SettingsWindow, 'refresh_models'), patch.object(SettingsWindow, 'refresh_microphones'), patch.object(gui, 'load_selected_model', return_value='offline-model'):
                dialog = SettingsWindow(ui)
            self.assertEqual(str(dialog.voice_combo.cget('state')), 'disabled')
            self.assertEqual(dialog.fields['model'].get(), 'offline-model')
            edge = next(key for key, value in ENGINE_OPTIONS.items() if value == 'edge')
            dialog.fields['engine'].set(edge)
            dialog.update_applicability()
            self.assertEqual(str(dialog.voice_combo.cget('state')), 'readonly')
            self.assertEqual(str(dialog.azure_fields[0].cget('state')), 'disabled')
            with patch.object(gui, 'save_settings', return_value=False):
                self.assertFalse(dialog.apply())
                self.assertIn('保存失败', dialog.message.get())
            dialog.window.destroy()
        finally:
            gui._runtime = previous


if __name__ == '__main__':
    unittest.main()
