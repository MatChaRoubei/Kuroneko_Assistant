"""Shared cancellation and application lifetime, independent of Tk/audio."""
import threading

shutdown = threading.Event()
voice_paused = threading.Event()
capture_cancelled = threading.Event()
busy = threading.Event()


def cancel_capture():
    capture_cancelled.set()


def request_exit():
    shutdown.set()
    capture_cancelled.set()
    from src.stop import request_stop
    request_stop()


def capture_should_stop():
    return shutdown.is_set() or voice_paused.is_set() or capture_cancelled.is_set()
