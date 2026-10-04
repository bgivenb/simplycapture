"""Windows global shortcuts; no keyboard hooks or elevated permissions."""
import ctypes
from ctypes import wintypes
import threading


class GlobalHotkeys:
    KEYS = {1: (ord("R"), "record"), 2: (ord("P"), "pause"), 3: (ord("H"), "show")}

    def __init__(self, events):
        self.events = events
        self.registered = []
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._listen, name="global-shortcuts", daemon=True)
        self.thread.start()
        self.ready.wait(2)

    def _listen(self):
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # Create the message queue before exposing the thread to close().
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        for key_id, (key, action) in self.KEYS.items():
            if user32.RegisterHotKey(None, key_id, 0x4000 | 0x0002 | 0x0004, key):
                self.registered.append(key_id)
        self.ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    action = self.KEYS.get(msg.wParam)
                    if action:
                        self.events.put(("command", action[1]))
        finally:
            for key_id in self.registered:
                user32.UnregisterHotKey(None, key_id)

    def poll(self):
        # Messages are read on a dedicated thread so Tk cannot consume them first.
        pass

    def close(self):
        if hasattr(self, "thread_id"):
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)


def enable_dpi():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimplyCapture.Desktop.2")
