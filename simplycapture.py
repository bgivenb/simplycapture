"""Simply Capture desktop application entry point."""
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import sys
import tkinter as tk
from tkinter import messagebox

from app_state import APP_DIR
from desktop_ui import ScreenRecorder, resource_path
from recording_engine import CaptureWorker
from windows_integration import enable_dpi


def main():
    enable_dpi()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(APP_DIR / "app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, "Local\\SimplyCapture.Desktop.2")
    duplicate = kernel.GetLastError() == 183
    root = tk.Tk()
    if duplicate:
        root.withdraw()
        messagebox.showinfo("Simply Capture is already open", "Use the tray icon to show the running app.", parent=root)
        root.destroy()
        return
    try:
        ScreenRecorder(root)
        root.report_callback_exception = lambda *args: report_error(root, *args)
        root.mainloop()
    except Exception:
        logging.exception("Application startup failed")
        messagebox.showerror("Simply Capture", f"The app could not start. See {APP_DIR / 'app.log'} for details.", parent=root)
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(mutex)


def report_error(root, kind, value, traceback):
    logging.error("UI error", exc_info=(kind, value, traceback))
    messagebox.showerror("Simply Capture", "Something went wrong. Your recordings are preserved. Check App logs in Settings for details.", parent=root)


if __name__ == "__main__":
    main()
