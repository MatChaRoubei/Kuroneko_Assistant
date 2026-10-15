"""Per-user Windows single instance and activation without a network port."""
import ctypes
import sys
import threading


class SingleInstance:
    def __init__(self):
        self.mutex = None
        self.event = None
        self.closed = threading.Event()

    def acquire(self):
        if sys.platform != 'win32':
            return True
        from ctypes import wintypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateEventW.restype = wintypes.HANDLE
        self.api.SetEvent.argtypes = [wintypes.HANDLE]
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.mutex = self.api.CreateMutexW(None, False, 'Local\\KuronekoAssistant.Mutex')
        exists = ctypes.get_last_error() == 183
        self.event = self.api.CreateEventW(None, False, False, 'Local\\KuronekoAssistant.Activate')
        if exists:
            self.api.SetEvent(self.event)
            self.close()
            return False
        return True

    def watch(self, show):
        if not self.event:
            return
        def watch():
            while not self.closed.is_set():
                if self.api.WaitForSingleObject(self.event, 250) == 0:
                    show()
        self.thread = threading.Thread(target=watch, daemon=True, name='activation')
        self.thread.start()

    def close(self):
        self.closed.set()
        if hasattr(self, 'thread'):
            self.thread.join(timeout=0.5)
        for handle in (self.event, self.mutex):
            if handle:
                self.api.CloseHandle(handle)
        self.event = self.mutex = None
