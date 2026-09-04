"""Simply Capture: a focused desktop region recorder."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import sys
import threading
from time import monotonic, sleep
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from capture_core import CaptureRegion, normalize_region, recording_path, validate_fps


def resource_path(filename: str) -> Path:
    """Resolve resources in source checkouts and PyInstaller bundles."""

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / filename


class CaptureWorker(threading.Thread):
    def __init__(
        self,
        region: CaptureRegion,
        output_file: Path,
        fps: float,
        stop_event: threading.Event,
        events: queue.Queue,
    ):
        super().__init__(name="capture-worker", daemon=True)
        self.region = region
        self.output_file = output_file
        self.fps = fps
        self.stop_event = stop_event
        self.events = events

    def run(self):
        writer = cv2.VideoWriter(
            str(self.output_file),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.fps,
            (self.region.width, self.region.height),
        )
        if not writer.isOpened():
            writer.release()
            self.events.put(("error", "The MP4 encoder could not be opened."))
            return

        frames = 0
        interval = 1.0 / self.fps
        deadline = monotonic()
        try:
            with mss.mss() as screen:
                while not self.stop_event.is_set():
                    image = np.asarray(screen.grab(self.region.as_mss()))
                    writer.write(cv2.cvtColor(image, cv2.COLOR_BGRA2BGR))
                    frames += 1
                    deadline += interval
                    sleep(max(0.0, deadline - monotonic()))
        except Exception as exc:
            self.events.put(("error", f"Recording failed: {exc}"))
        else:
            self.events.put(("saved", self.output_file, frames))
        finally:
            writer.release()


class ScreenRecorder:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.region: Optional[CaptureRegion] = None
        self.output_directory = Path.home() / "Videos"
        if not self.output_directory.is_dir():
            self.output_directory = Path.cwd()
        self.stop_event = threading.Event()
        self.worker: Optional[CaptureWorker] = None
        self.events: queue.Queue = queue.Queue()
        self.selection_window: Optional[tk.Toplevel] = None
        self.selection_canvas: Optional[tk.Canvas] = None
        self.selection_start = (0, 0)
        self.selection_rectangle = None

        self._load_icons()
        self._build_ui()
        self.root.bind_all("<Control-Shift-S>", self.toggle_recording)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_events)

    @property
    def recording(self) -> bool:
        return self.worker is not None and self.worker.is_alive() and not self.stop_event.is_set()

    def _load_icon(self, filename: str):
        path = resource_path(filename)
        if not path.is_file():
            return None
        image = Image.open(path).resize((112, 112), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def _load_icons(self):
        self.start_icon = self._load_icon("icon.png")
        self.hover_icon = self._load_icon("icon_hover.png") or self.start_icon

    def _build_ui(self):
        self.root.title("Simply Capture")
        self.root.geometry("520x430")
        self.root.minsize(440, 390)
        self.root.configure(bg="#202225")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#202225", foreground="#f2f3f5", font=("Segoe UI", 10))
        style.configure("TButton", background="#35373c", padding=8)
        style.map("TButton", background=[("active", "#5865f2")])
        style.configure("TLabel", background="#202225", foreground="#b5bac1")

        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Simply Capture", font=("Segoe UI", 20, "bold")).pack(pady=(0, 16))

        ttk.Button(frame, text="Choose output folder", command=self.choose_output_directory).pack(fill="x")
        self.folder_label = ttk.Label(frame, text=str(self.output_directory), wraplength=450)
        self.folder_label.pack(fill="x", pady=(6, 14))

        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        ttk.Button(controls, text="Select recording region", command=self.select_region).pack(side="left", expand=True, fill="x")
        ttk.Label(controls, text="FPS").pack(side="left", padx=(12, 4))
        self.fps_variable = tk.StringVar(value="20")
        ttk.Spinbox(controls, from_=1, to=60, width=5, textvariable=self.fps_variable).pack(side="left")

        self.region_label = ttk.Label(frame, text="No region selected")
        self.region_label.pack(pady=14)

        if self.start_icon:
            self.record_button = ttk.Button(frame, image=self.start_icon, command=self.toggle_recording)
        else:
            self.record_button = ttk.Button(frame, text="Start recording", command=self.toggle_recording)
        self.record_button.pack(pady=8)

        self.status_label = ttk.Label(frame, text="Idle")
        self.status_label.pack(pady=8)
        ttk.Label(frame, text="Ctrl+Shift+S starts or stops recording while this app is focused.").pack()

    def choose_output_directory(self):
        selected = filedialog.askdirectory(initialdir=self.output_directory)
        if selected:
            self.output_directory = Path(selected)
            self.folder_label.configure(text=str(self.output_directory))

    def select_region(self):
        if self.recording:
            messagebox.showwarning("Recording active", "Stop the recording before changing the region.")
            return
        self.root.withdraw()
        window = tk.Toplevel(self.root)
        window.attributes("-fullscreen", True)
        window.attributes("-alpha", 0.3)
        window.configure(bg="black")
        window.bind("<Escape>", lambda _event: self._cancel_selection())

        canvas = tk.Canvas(window, cursor="cross", bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<ButtonPress-1>", self._selection_started)
        canvas.bind("<B1-Motion>", self._selection_moved)
        canvas.bind("<ButtonRelease-1>", self._selection_finished)
        canvas.focus_set()
        self.selection_window = window
        self.selection_canvas = canvas

    def _selection_started(self, event):
        self.selection_start = (event.x_root, event.y_root)
        self.selection_rectangle = self.selection_canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff4d4d", width=2
        )

    def _selection_moved(self, event):
        if self.selection_rectangle is not None:
            start_x = self.selection_start[0] - self.selection_window.winfo_rootx()
            start_y = self.selection_start[1] - self.selection_window.winfo_rooty()
            self.selection_canvas.coords(self.selection_rectangle, start_x, start_y, event.x, event.y)

    def _selection_finished(self, event):
        try:
            self.region = normalize_region(*self.selection_start, event.x_root, event.y_root)
        except ValueError as exc:
            self._close_selection()
            messagebox.showerror("Invalid region", str(exc))
            return
        self._close_selection()
        self.region_label.configure(
            text=f"{self.region.width}×{self.region.height} at ({self.region.left}, {self.region.top})"
        )
        self.status_label.configure(text="Region ready")

    def _cancel_selection(self):
        self._close_selection()
        self.status_label.configure(text="Selection cancelled")

    def _close_selection(self):
        if self.selection_window is not None:
            self.selection_window.destroy()
        self.selection_window = None
        self.selection_canvas = None
        self.selection_rectangle = None
        self.root.deiconify()

    def toggle_recording(self, _event=None):
        if self.worker is None:
            self.start_recording()
        elif not self.stop_event.is_set():
            self.stop_recording()

    def start_recording(self):
        if self.region is None:
            messagebox.showerror("No region", "Select a recording region first.")
            return
        try:
            fps = validate_fps(self.fps_variable.get())
            output_file = recording_path(self.output_directory)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        self.stop_event.clear()
        self.worker = CaptureWorker(self.region, output_file, fps, self.stop_event, self.events)
        self.worker.start()
        self.status_label.configure(text=f"Recording {output_file.name}")
        self._set_button_state(True)

    def stop_recording(self):
        if self.worker is not None:
            self.stop_event.set()
            self.status_label.configure(text="Finishing recording…")
            self.record_button.configure(state="disabled")

    def _set_button_state(self, recording: bool):
        if self.start_icon:
            self.record_button.configure(image=self.hover_icon if recording else self.start_icon)
        else:
            self.record_button.configure(text="Stop recording" if recording else "Start recording")

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "saved":
                    _, output_file, frames = event
                    self.worker = None
                    self.stop_event.clear()
                    self.record_button.configure(state="normal")
                    self._set_button_state(False)
                    self.status_label.configure(text=f"Saved {frames:,} frames to {output_file.name}")
                elif event[0] == "error":
                    self.worker = None
                    self.stop_event.clear()
                    self.record_button.configure(state="normal")
                    self._set_button_state(False)
                    messagebox.showerror("Recording error", event[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def on_close(self):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askokcancel("Quit", "Stop the active recording and quit?"):
                return
            self.stop_event.set()
            self.worker.join(timeout=2)
        self.root.destroy()


def main():
    os.environ.setdefault("OPENCV_VIDEOIO_PRIORITY_MSMF", "0")
    root = tk.Tk()
    ScreenRecorder(root)
    root.mainloop()


if __name__ == "__main__":
    main()
