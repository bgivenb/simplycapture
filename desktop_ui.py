"""Simply Capture's native desktop workspace."""
import ctypes
import logging
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk
import pystray

from app_state import APP_DIR, VERSION, StateStore, format_duration
from audio_capture import AudioSettings, list_devices
from capture_core import CaptureRegion, normalize_region, recording_path, validate_fps
from recording_engine import CaptureWorker, QUALITY
from discord_export import DiscordExporter, probe_media, plan_export
from windows_integration import GlobalHotkeys

BG, PANEL, EDGE = "#10121c", "#191d2b", "#30364c"
TEXT, MUTED, ACCENT, GREEN, RED = "#f1f2fa", "#a3adc5", "#a78bfa", "#63d8b3", "#fa748d"


def resource_path(filename):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / filename


class ScreenRecorder:
    def __init__(self, root, store=None, integrations=True):
        self.root, self.store = root, store or StateStore()
        self.config = self.store.load()
        self.events = queue.Queue()
        self.worker, self.countdown_id, self.selection_window = None, None, None
        self.export_worker = None
        self.closing, self.finalizing, self.pending_start = False, False, False
        self.stop_event = threading.Event()
        self.audio_settings = AudioSettings()
        self.locked_controls, self.pages, self.nav_buttons = [], {}, {}
        self.history = [r for r in self.config["history"] if isinstance(r, dict) and "path" in r]
        self.output_directory = Path(self.config.get("output_directory", str(Path.home() / "Videos" / "Simply Capture")))
        try:
            self.output_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.output_directory = Path.home()
        with mss.MSS() as screen:
            self.monitors = screen.monitors[1:]
            self.desktop = screen.monitors[0]
        if not self.monitors:
            raise RuntimeError("No displays were found.")
        monitor = self.monitors[0]
        self.region = normalize_region(monitor["left"], monitor["top"],
                                       monitor["left"] + monitor["width"], monitor["top"] + monitor["height"])
        self.tray = None
        self.hotkeys = None
        self._build_ui()
        self.refresh_audio()
        if integrations:
            self.hotkeys = GlobalHotkeys(self.events)
            self._start_tray()
        for key, action, key_id in (("r", self.toggle_recording, 1), ("p", self.pause_recording, 2),
                                    ("h", self.show_window, 3)):
            if not self.hotkeys or key_id not in self.hotkeys.registered:
                self.root.bind_all(f"<Control-Shift-{key.upper()}>", lambda event, fn=action: fn())
        self.root.bind("<Escape>", lambda event: self.cancel_countdown())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._poll_events)
        self.root.after(120, self._update_meters)
        self._load_library()

    @property
    def busy(self):
        return self.worker is not None or self.pending_start or self.export_worker is not None

    @property
    def recording(self):
        return self.worker is not None and not self.finalizing and not self.stop_event.is_set()

    def _label(self, parent, text, size=10, color=MUTED, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

    def _button(self, parent, text, command, kind="normal", **kwargs):
        return ttk.Button(parent, text=text, command=command, style=f"{kind}.TButton", **kwargs)

    def _lock(self, widget):
        self.locked_controls.append(widget)
        return widget

    def _card(self, parent, title):
        card = tk.Frame(parent, bg=PANEL, highlightbackground=EDGE, highlightthickness=1, padx=18, pady=16)
        self._label(card, title, 12, TEXT, True).pack(anchor="w", pady=(0, 12))
        return card

    def _build_ui(self):
        root = self.root
        root.title("Simply Capture")
        root.geometry("1140x840")
        root.minsize(1000, 760)
        root.configure(bg=BG)
        root.iconbitmap(str(resource_path("icon.ico")))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=PANEL, foreground=TEXT)
        style.configure("normal.TButton", background="#2b3146", foreground=TEXT, borderwidth=0, padding=(14, 9))
        style.configure("primary.TButton", background="#9271ed", foreground="#ffffff", borderwidth=0,
                        padding=(24, 13), font=("Segoe UI", 12, "bold"))
        style.configure("danger.TButton", background="#ac3d58", foreground="#ffffff", borderwidth=0, padding=(24, 13),
                        font=("Segoe UI", 12, "bold"))
        for kind in ("normal", "primary", "danger"):
            style.map(f"{kind}.TButton", background=[("disabled", "#25293a"), ("active", "#7056bb")],
                      foreground=[("disabled", "#69728b")])
        style.configure("TCombobox", fieldbackground="#252a3b", background="#30364c", foreground=TEXT,
                        arrowcolor=TEXT, padding=5, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE)
        style.map("TCombobox", fieldbackground=[("readonly", "#252a3b"), ("disabled", "#1b1e2a")],
                  foreground=[("readonly", TEXT), ("disabled", "#69728b")], selectbackground=[("readonly", "#252a3b")],
                  selectforeground=[("readonly", TEXT)])
        root.option_add("*TCombobox*Listbox.background", "#252a3b")
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", "#7056bb")
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=3)
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", "#69728b")])
        style.configure("Horizontal.TScale", background=PANEL, troughcolor="#30364c", bordercolor=PANEL,
                        lightcolor=ACCENT, darkcolor=ACCENT, sliderrelief="flat", sliderlength=14)
        style.configure("meter.Horizontal.TProgressbar", troughcolor="#30364c", background=GREEN,
                        bordercolor=PANEL, lightcolor=GREEN, darkcolor=GREEN, borderwidth=0, thickness=6)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=38,
                        borderwidth=0)
        style.configure("Treeview.Heading", background="#252a3b", foreground=MUTED, relief="flat", padding=8)
        style.map("Treeview", background=[("selected", "#493969")])
        style.configure("TEntry", fieldbackground="#252a3b", foreground=TEXT, padding=7)

        sidebar = tk.Frame(root, bg="#141724", width=174, padx=18, pady=24)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.logo_image = ImageTk.PhotoImage(Image.open(resource_path("icon.png")).resize((52, 52), Image.Resampling.LANCZOS))
        self._label(sidebar, "", image=self.logo_image).pack(anchor="w", pady=(0, 12))
        self._label(sidebar, "SIMPLY", 14, TEXT, True).pack(anchor="w")
        self._label(sidebar, "CAPTURE", 14, TEXT, True).pack(anchor="w", pady=(0, 34))
        for name in ("Record", "Captures", "Settings"):
            button = tk.Button(sidebar, text=name, anchor="w", bd=0, relief="flat", bg="#141724", fg=MUTED,
                               activebackground="#302942", activeforeground=TEXT, padx=12, pady=12,
                               font=("Segoe UI", 11), command=lambda page=name: self.show_page(page))
            button.pack(fill="x", pady=3)
            self.nav_buttons[name] = button
        footer = tk.Frame(sidebar, bg="#141724")
        footer.pack(side="bottom", fill="x")
        self._button(footer, "About & help", self.about).pack(fill="x", pady=10)
        self._label(footer, "SAVED ON YOUR PC", 8, GREEN, True).pack(anchor="w")
        self._label(footer, f"Version {VERSION}", 9).pack(anchor="w", pady=(5, 0))

        main = tk.Frame(root, bg=BG, padx=26, pady=18)
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", pady=(0, 14))
        self.page_title = self._label(header, "Make something worth sharing.", 23, TEXT, True)
        self.page_title.pack(side="left")
        self.badge = self._label(header, "● READY", 10, GREEN, True)
        self.badge.pack(side="right", padx=4)
        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True)
        for name in ("Record", "Captures", "Settings"):
            page = tk.Frame(self.content, bg=BG)
            self.pages[name] = page
        self._build_record_page(self.pages["Record"])
        self._build_library_page(self.pages["Captures"])
        self._build_settings_page(self.pages["Settings"])
        bottom = tk.Frame(main, bg=BG)
        bottom.pack(side="bottom", fill="x", pady=(12, 0), before=self.content)
        self.status_label = self._label(bottom, "Choose a display or drag a region, then start recording.", 9, MUTED, wraplength=780, anchor="w")
        self.status_label.pack(anchor="w")
        self.show_page("Record")

    def _build_record_page(self, page):
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2, minsize=310)
        page.rowconfigure(0, weight=1)
        capture = self._card(page, "01  Capture area")
        capture.grid(row=0, column=0, sticky="nsew", padx=(0, 14), pady=(0, 14))
        choices = [f"Display {i + 1} · {m['width']} × {m['height']}" for i, m in enumerate(self.monitors)] + ["Custom region"]
        self.display_var = tk.StringVar(value=choices[0])
        self.display_combo = self._lock(ttk.Combobox(capture, textvariable=self.display_var, values=choices, state="readonly"))
        self.display_combo.pack(fill="x", pady=(0, 10))
        self.display_combo.bind("<<ComboboxSelected>>", self.select_display)
        self.preview_canvas = tk.Canvas(capture, bg="#0c0e16", height=150, highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda event: self._draw_preview())
        self.preview_image = None
        self.region_label = self._label(capture, "", 9)
        self.region_label.pack(anchor="w", pady=10)
        self._update_region_label()
        actions = tk.Frame(capture, bg=PANEL)
        actions.pack(fill="x")
        self._lock(self._button(actions, "Drag a region", self.select_region)).pack(side="left")
        self._lock(self._button(actions, "Preview", self.preview_selection)).pack(side="left", padx=6)
        self._lock(self._button(actions, "Screenshot", self.take_screenshot)).pack(side="right")

        audio = self._card(page, "02  Your audio mix")
        audio.grid(row=0, column=1, sticky="nsew", pady=(0, 14))
        self.audio_enabled, self.audio_selectors, self.gain_vars, self.meters = {}, {}, {}, {}
        self.mute_vars, self.gain_scales = {}, {}
        for name in ("PC audio", "Microphone"):
            source_row = tk.Frame(audio, bg=PANEL)
            source_row.pack(fill="x")
            enabled = tk.BooleanVar(value=bool(self.config["audio_enabled"].get(name, False)))
            self.audio_enabled[name] = enabled
            self._lock(ttk.Checkbutton(source_row, text=name, variable=enabled)).pack(side="left", pady=(2, 3))
            mute = tk.BooleanVar(value=False)
            self.mute_vars[name] = mute
            ttk.Checkbutton(source_row, text="Mute", variable=mute, command=lambda source=name: self._apply_gain(source)).pack(side="right")
            selector = self._lock(ttk.Combobox(audio, state="readonly", width=25))
            selector.pack(fill="x", pady=(0, 5))
            self.audio_selectors[name] = selector
            gain_row = tk.Frame(audio, bg=PANEL)
            gain_row.pack(fill="x")
            try:
                gain = max(0, min(150, float(self.config["gains"].get(name, 70))))
            except (TypeError, ValueError):
                gain = 70
            variable = tk.DoubleVar(value=gain)
            self.gain_vars[name] = variable
            label = self._label(gain_row, f"{gain:.0f}%", 9, TEXT, width=5)
            label.pack(side="right")
            scale = ttk.Scale(gain_row, from_=0, to=150, variable=variable,
                              command=lambda value, source=name, display=label: self._gain_changed(source, value, display))
            scale.pack(side="left", fill="x", expand=True)
            self.gain_scales[name] = scale
            self.audio_settings.set_gain(name, gain)
            meter = tk.Canvas(audio, height=6, bg=EDGE, highlightthickness=0)
            meter.pack(fill="x", pady=(3, 10))
            self.meters[name] = meter
        self.noise_var = tk.BooleanVar(value=bool(self.config.get("noise_reduction", False)))
        self._lock(ttk.Checkbutton(audio, text="Reduce microphone noise", variable=self.noise_var)).pack(anchor="w", pady=(5, 0))
        self._lock(self._button(audio, "Refresh devices", self.refresh_audio)).pack(anchor="w", pady=(6, 0))
        self.audio_hint = self._label(audio, "Meters respond while recording.", 9, wraplength=300, justify="left")
        self.audio_hint.pack(anchor="w", pady=(6, 0))

        options = self._card(page, "03  Recording options")
        options.configure(pady=10)
        options.winfo_children()[0].pack_configure(pady=(0, 5))
        options.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        row = tk.Frame(options, bg=PANEL)
        row.pack(fill="x")
        self.fps_variable = tk.StringVar(value=str(self.config.get("fps", "30")))
        self.quality_var = tk.StringVar(value=str(self.config.get("quality", "Balanced")))
        if self.quality_var.get() not in QUALITY:
            self.quality_var.set("Balanced")
        self.countdown_var = tk.StringVar(value=str(self.config.get("countdown", "3")))
        self.auto_stop_var = tk.StringVar(value=str(self.config.get("auto_stop", "Off")))
        for title, variable, values, width in (("Frame rate", self.fps_variable, ("15", "24", "30", "60"), 7),
                                                ("Quality", self.quality_var, tuple(QUALITY), 16),
                                                ("Countdown", self.countdown_var, ("0", "3", "5", "10"), 8),
                                                ("Stop after", self.auto_stop_var, ("Off", "1 min", "5 min", "10 min", "30 min", "60 min"), 9)):
            field = tk.Frame(row, bg=PANEL)
            field.pack(side="left", padx=(0, 22))
            self._label(field, title, 9).pack(anchor="w", pady=(0, 5))
            self._lock(ttk.Combobox(field, values=values, textvariable=variable, width=width, state="readonly")).pack()
        self._label(options, "MP4 · H.264 video + AAC audio · original capture resolution", 9).pack(anchor="w", pady=(6, 0))
        transport = tk.Frame(page, bg=BG)
        transport.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.record_button = self._button(transport, "●  Start recording", self.toggle_recording, "primary")
        self.record_button.pack(side="left")
        self.pause_button = self._button(transport, "Pause", self.pause_recording, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self._button(transport, "Hide to tray", self.hide_to_tray).pack(side="left")
        self.timer_label = self._label(transport, "00:00:00", 25, TEXT, True)
        self.timer_label.pack(side="right")
        shortcuts = self._label(page, "Ctrl + Shift + R  record / stop      Ctrl + Shift + P  pause / resume      Ctrl + Shift + H  show app", 9)
        shortcuts.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_library_page(self, page):
        tools = tk.Frame(page, bg=BG)
        tools.pack(fill="x", pady=(0, 16))
        self._button(tools, "Open recording", self.open_selected).pack(side="left")
        self._button(tools, "Show in folder", self.reveal_selected).pack(side="left", padx=8)
        self._button(tools, "Export for Discord", self.export_dialog, "primary").pack(side="left")
        self._button(tools, "Import", self.import_capture).pack(side="left", padx=8)
        self._button(tools, "Open output folder", self.open_output).pack(side="right")
        table = tk.Frame(page, bg=PANEL)
        table.pack(fill="both", expand=True)
        self.library = ttk.Treeview(table, columns=("name", "when", "duration", "size"), show="headings", selectmode="browse")
        for key, title, width in (("name", "Capture", 330), ("when", "Created", 170),
                                  ("duration", "Length", 85), ("size", "Size", 85)):
            self.library.heading(key, text=title)
            self.library.column(key, width=width, minwidth=60, stretch=key == "name")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.library.yview)
        self.library.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.library.pack(fill="both", expand=True)
        self.library.bind("<Double-1>", lambda event: self.open_selected())
        self.library.bind("<<TreeviewSelect>>", lambda event: self._library_preview())
        self.library_image = self._label(page, "Your saved videos and screenshots will appear here.", 10)
        self.library_image.pack(fill="x", pady=14)
        self._label(page, "Double-click a capture to open it in your default video player or image viewer.", 9).pack(anchor="w")

    def _build_settings_page(self, page):
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)
        output = self._card(page, "Save location")
        output.grid(row=0, column=0, sticky="nsew", padx=(0, 14), pady=(0, 14))
        self.folder_label = self._label(output, str(self.output_directory), 10, TEXT, wraplength=340, anchor="w")
        self.folder_label.pack(fill="x", pady=(0, 12))
        row = tk.Frame(output, bg=PANEL)
        row.pack(fill="x")
        self._lock(self._button(row, "Choose folder", self.choose_output_directory)).pack(side="left")
        self._button(row, "Open folder", self.open_output).pack(side="left", padx=8)
        self._label(output, "File name prefix", 9).pack(anchor="w", pady=(16, 5))
        self.prefix_var = tk.StringVar(value=str(self.config.get("prefix", "simply-capture")))
        self._lock(ttk.Entry(output, textvariable=self.prefix_var, width=28)).pack(anchor="w")
        behavior = self._card(page, "Recording behavior")
        behavior.grid(row=0, column=1, sticky="nsew", pady=(0, 14))
        self.minimize_var = tk.BooleanVar(value=bool(self.config.get("minimize", True)))
        self.pointer_var = tk.BooleanVar(value=bool(self.config.get("pointer", False)))
        self._lock(ttk.Checkbutton(behavior, text="Minimize when recording starts", variable=self.minimize_var)).pack(anchor="w", pady=5)
        self._lock(ttk.Checkbutton(behavior, text="Highlight the mouse pointer", variable=self.pointer_var)).pack(anchor="w", pady=5)
        self._label(behavior, "Stop and pause from the tray icon or the global shortcuts while the app is minimized.", 9, wraplength=310, justify="left").pack(anchor="w", pady=10)
        discord = self._card(page, "Discord export")
        discord.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        self.discord_limit_var = tk.StringVar(value=str(self.config.get("discord_limit", "20")))
        self.discord_resolution_var = tk.StringVar(value=str(self.config.get("discord_resolution", "Auto")))
        self.discord_auto_var = tk.BooleanVar(value=bool(self.config.get("discord_auto", False)))
        self._label(discord, "Maximum file size (MB)", 9).pack(anchor="w", pady=(0, 5))
        self._lock(ttk.Combobox(discord, textvariable=self.discord_limit_var, values=("10", "20", "50", "100", "500"), width=12, state="readonly")).pack(anchor="w")
        self._label(discord, "Resolution", 9).pack(anchor="w", pady=(12, 5))
        self._lock(ttk.Combobox(discord, textvariable=self.discord_resolution_var, values=("Auto", "480p", "720p", "1080p"), width=12, state="readonly")).pack(anchor="w")
        self._lock(ttk.Checkbutton(discord, text="Create a Discord copy after recording", variable=self.discord_auto_var)).pack(anchor="w", pady=(12, 5))
        self._label(discord, "Choose the limit shown in Discord. Auto adjusts resolution to the clip length and file size.\n\nYou can also trim and export a saved video from Captures. The original stays untouched.", 9, wraplength=320, justify="left").pack(anchor="w", pady=8)
        support = self._card(page, "Help & recovery")
        support.grid(row=1, column=1, sticky="nsew")
        self._label(support, "Settings and the capture library are saved automatically.\n\nInterrupted recordings are kept in the recovery folder so you can recover the video and audio.", 10, TEXT, wraplength=310, justify="left").pack(anchor="w")
        row = tk.Frame(support, bg=PANEL)
        row.pack(fill="x", pady=(14, 0))
        self._button(row, "Recovery folder", self.open_recovery).pack(anchor="w", pady=3)
        self._button(row, "App logs", self.open_logs).pack(anchor="w", pady=3)
        self.hotkey_status = self._label(support, "", 9, wraplength=310, justify="left")
        self.hotkey_status.pack(anchor="w", pady=(12, 0))

    def show_page(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for key, button in self.nav_buttons.items():
            button.configure(bg="#302942" if key == name else "#141724", fg=TEXT if key == name else MUTED)
        self.page_title.configure(text={"Record": "Your screen. Your sound.", "Captures": "Your captures", "Settings": "Make it yours"}[name])
        if name == "Captures":
            self._load_library()
        if name == "Settings":
            unavailable = [self.hotkeys.KEYS[k][1] for k in self.hotkeys.KEYS if k not in self.hotkeys.registered] if self.hotkeys else []
            self.hotkey_status.configure(text=f"Shortcuts already in use: {', '.join(unavailable)}. Those shortcuts work only while the app is focused." if unavailable else "Global shortcuts are available while Simply Capture is running.")

    def _gain_changed(self, name, value, label):
        label.configure(text=f"{float(value):.0f}%")
        self._apply_gain(name)

    def _apply_gain(self, name):
        self.audio_settings.set_gain(name, 0 if self.mute_vars[name].get() else self.gain_vars[name].get())

    def _update_meters(self):
        for name, meter in self.meters.items():
            level = self.audio_settings.meter(name) if self.recording else 0
            meter.delete("all")
            if level:
                meter.create_rectangle(0, 0, meter.winfo_width() * level, 6, fill=RED if level > 0.92 else GREEN, outline="")
        if self.tray and self.worker and self.worker.clock:
            state = "Paused" if self.worker.clock.paused else "Recording"
            self.tray.title = f"Simply Capture · {state} {format_duration(self.worker.clock.elapsed())}"
        if not self.closing:
            self.root.after(120, self._update_meters)

    def refresh_audio(self):
        if self.busy:
            return
        try:
            selected = {name: selector.get() for name, selector in self.audio_selectors.items()}
            self.device_lists = list_devices()
            for name, selector in self.audio_selectors.items():
                devices = self.device_lists[name]
                values = [d["name"] for d in devices]
                selector.configure(values=values)
                prior = selected.get(name) or self.config["devices"].get(name)
                if prior in values:
                    selector.current(values.index(prior))
                elif values:
                    selector.current(0)
                else:
                    selector.set("No device available")
            self.audio_hint.configure(text="Meters respond while recording.")
        except Exception as exc:
            logging.exception("Audio enumeration failed")
            self.device_lists = {}
            for selector in self.audio_selectors.values():
                selector.set("Audio unavailable")
            self.audio_hint.configure(text="Audio is unavailable. Check Windows sound settings and refresh devices.")

    def select_display(self, event=None):
        index = self.display_combo.current()
        if index == len(self.monitors):
            self.select_region()
            return
        if 0 <= index < len(self.monitors):
            m = self.monitors[index]
            self.region = normalize_region(m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])
            self.preview_image = None
            self._update_region_label()
            self._draw_preview()

    def _update_region_label(self):
        r = self.region
        self.region_label.configure(text=f"{r.width} × {r.height} px   ·   Position {r.left}, {r.top}")

    def _draw_preview(self):
        canvas = self.preview_canvas
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if self.preview_image and w > 10 and h > 10:
            image = self.preview_image.copy()
            image.thumbnail((max(1, w - 8), max(1, h - 8)), Image.Resampling.LANCZOS)
            self.preview_photo = ImageTk.PhotoImage(image)
            canvas.create_image(w / 2, h / 2, image=self.preview_photo)
        else:
            cx, cy = w / 2, h / 2
            canvas.create_rectangle(cx - 34, cy - 26, cx + 34, cy + 18, outline="#7056bb", width=2)
            canvas.create_line(cx, cy + 18, cx, cy + 29, fill="#7056bb", width=2)
            canvas.create_line(cx - 18, cy + 29, cx + 18, cy + 29, fill="#7056bb", width=2)
            canvas.create_text(cx, cy + 53, text="Preview your capture area", fill=MUTED, font=("Segoe UI", 10))

    def preview_selection(self):
        if self.busy:
            return
        self.root.withdraw()
        self.root.after(180, self._capture_preview)

    def _capture_preview(self):
        try:
            with mss.MSS() as screen:
                shot = screen.grab(self.region.as_mss())
                self.preview_image = Image.frombytes("RGB", shot.size, shot.rgb)
            self._draw_preview()
        except Exception as exc:
            self.show_window()
            messagebox.showerror("Preview unavailable", str(exc), parent=self.root)
        finally:
            self.show_window()

    def select_region(self):
        if self.busy:
            return
        self.root.withdraw()
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        d = self.desktop
        window.geometry(f"{d['width']}x{d['height']}+0+0")
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.35)
        window.configure(bg="black")
        window.update_idletasks()
        handle = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        ctypes.windll.user32.SetWindowPos(handle, 0, d["left"], d["top"], d["width"], d["height"], 0x0040)
        canvas = tk.Canvas(window, cursor="cross", bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(30, 30, anchor="nw", text="Drag to select a region   ·   Esc to cancel", fill="white", font=("Segoe UI", 18, "bold"))
        self.selection_window, self.selection_canvas = window, canvas
        self.selection_start, self.selection_rectangle = None, None
        window.bind("<Escape>", lambda event: self._close_selection())
        canvas.bind("<ButtonPress-1>", self._selection_started)
        canvas.bind("<B1-Motion>", self._selection_moved)
        canvas.bind("<ButtonRelease-1>", self._selection_finished)
        canvas.focus_force()

    def _selection_started(self, event):
        self.selection_start = (event.x_root, event.y_root)
        if self.selection_rectangle:
            self.selection_canvas.delete(self.selection_rectangle)
        self.selection_rectangle = self.selection_canvas.create_rectangle(event.x, event.y, event.x, event.y, outline=ACCENT, width=3)

    def _selection_moved(self, event):
        if self.selection_start:
            x, y = self.selection_start
            self.selection_canvas.coords(self.selection_rectangle, x - self.selection_window.winfo_rootx(),
                                         y - self.selection_window.winfo_rooty(), event.x, event.y)

    def _selection_finished(self, event):
        if not self.selection_start:
            return
        try:
            region = normalize_region(*self.selection_start, event.x_root, event.y_root)
        except ValueError as exc:
            self._close_selection()
            messagebox.showerror("Choose a larger region", str(exc), parent=self.root)
            return
        self.region = region
        self.display_combo.current(len(self.monitors))
        self._update_region_label()
        self._close_selection(show=False)
        self.root.after(180, self._capture_preview)

    def _close_selection(self, show=True):
        if self.selection_window:
            self.selection_window.destroy()
            self.selection_window = None
        if show:
            if self.display_combo.current() == len(self.monitors) and not self.preview_image:
                self.display_combo.current(0)
                self.select_display()
            self.show_window()

    def choose_output_directory(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(initialdir=self.output_directory, parent=self.root)
        if selected:
            self.output_directory = Path(selected)
            self.folder_label.configure(text=str(self.output_directory))
            self.save_settings()

    def take_screenshot(self):
        if self.busy:
            return
        self.root.withdraw()
        self.root.after(180, self._save_screenshot)

    def _save_screenshot(self):
        try:
            path = recording_path(self.output_directory, prefix=self.prefix_var.get(), extension=".png")
            with mss.MSS() as screen:
                shot = screen.grab(self.region.as_mss())
                Image.frombytes("RGB", shot.size, shot.rgb).save(path)
            self.add_history(path, 0, "Screenshot")
            self.status_label.configure(text=f"Screenshot saved: {path.name}")
        except Exception as exc:
            messagebox.showerror("Screenshot failed", str(exc), parent=self.root)
        finally:
            self.show_window()

    def toggle_recording(self, event=None):
        if self.export_worker:
            return
        if self.pending_start:
            self.cancel_countdown()
        elif self.worker:
            if not self.finalizing:
                self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.busy or self.selection_window or self.root.grab_current():
            return
        try:
            fps = validate_fps(self.fps_variable.get())
            output = recording_path(self.output_directory, prefix=self.prefix_var.get())
            # Fail before recording if the output location is not writable.
            probe = output.with_suffix(".write-test")
            with probe.open("xb"):
                pass
            probe.unlink()
            countdown = int(self.countdown_var.get())
            auto_stop = 0 if self.auto_stop_var.get() == "Off" else int(self.auto_stop_var.get().split()[0]) * 60
            devices = {}
            for name, enabled in self.audio_enabled.items():
                if enabled.get():
                    available = self.device_lists.get(name, [])
                    index = self.audio_selectors[name].current()
                    if index < 0 or index >= len(available):
                        raise ValueError(f"Choose an available {name} device or turn that source off.")
                    devices[name] = available[index]["index"]
            self.audio_settings.devices = devices
            for name in self.gain_vars:
                self._apply_gain(name)
            self.save_settings()
        except (OSError, ValueError) as exc:
            self.show_window()
            messagebox.showerror("Check recording settings", str(exc), parent=self.root)
            return
        self.pending_options = (fps, output, auto_stop)
        self.pending_start = True
        self._set_locked(True)
        self.timer_label.configure(text="00:00:00")
        self._countdown(countdown)

    def _countdown(self, remaining):
        self.countdown_id = None
        if not self.pending_start:
            return
        if remaining > 0:
            self.badge.configure(text=f"● STARTING IN {remaining}", fg=ACCENT)
            self.record_button.configure(text="Cancel countdown", style="normal.TButton")
            self.status_label.configure(text=f"Recording starts in {remaining} seconds. Press Esc to cancel.")
            self.countdown_id = self.root.after(1000, lambda: self._countdown(remaining - 1))
        else:
            if self.minimize_var.get():
                self.root.iconify()
            self.countdown_id = self.root.after(220, self._begin_recording)

    def cancel_countdown(self):
        if self.pending_start:
            if self.countdown_id:
                self.root.after_cancel(self.countdown_id)
                self.countdown_id = None
            self.pending_start = False
            self._reset_transport()
            self.status_label.configure(text="Countdown cancelled.")
            self.show_window()

    def _begin_recording(self):
        self.countdown_id = None
        if not self.pending_start:
            return
        self.pending_start = False
        self.stop_event.clear()
        fps, output, auto_stop = self.pending_options
        self.worker = CaptureWorker(self.region, output, fps, self.stop_event, self.events, self.audio_settings,
                                    quality=self.quality_var.get(), pointer=self.pointer_var.get(), auto_stop=auto_stop,
                                    noise_reduction=self.noise_var.get())
        self.worker.start()
        self.finalizing = False
        self.record_button.configure(text="■  Stop recording", style="danger.TButton")
        self.badge.configure(text="● RECORDING", fg=RED)
        self.status_label.configure(text=f"Recording to {output.name}   ·   Ctrl + Shift + R to stop")

    def pause_recording(self):
        if self.recording and self.worker.clock:
            paused = self.worker.pause()
            self.pause_button.configure(text="Resume" if paused else "Pause")
            self.badge.configure(text="● PAUSED" if paused else "● RECORDING", fg=ACCENT if paused else RED)
            self.status_label.configure(text="Paused. Resume to continue the same recording." if paused else "Recording resumed.")
            self._update_tray_icon(paused)

    def stop_recording(self):
        if self.worker:
            self.stop_event.set()
            self.finalizing = True
            self.pause_button.configure(state="disabled")
            self.record_button.configure(text="Saving…", state="disabled")
            self.badge.configure(text="● SAVING", fg=ACCENT)
            self.status_label.configure(text="Finishing your recording. Keep the app open while it saves.")

    def _set_locked(self, locked):
        for widget in self.locked_controls:
            widget.configure(state="disabled" if locked else "readonly" if isinstance(widget, ttk.Combobox) else "normal")

    def _reset_transport(self):
        self.worker, self.finalizing = None, False
        self.stop_event.clear()
        self._set_locked(False)
        self.record_button.configure(text="●  Start recording", state="normal", style="primary.TButton")
        self.pause_button.configure(text="Pause", state="disabled")
        self.badge.configure(text="● READY", fg=GREEN)
        self._update_tray_icon()

    def _poll_events(self):
        if self.hotkeys:
            self.hotkeys.poll()
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "command":
                    {"record": self.toggle_recording, "pause": self.pause_recording, "show": self.show_window,
                     "folder": self.open_output, "quit": self.on_close}[event[1]]()
                elif kind == "started":
                    self.pause_button.configure(state="normal")
                    self._update_tray_icon()
                elif kind == "progress":
                    self.timer_label.configure(text=format_duration(event[1]))
                elif kind == "finishing":
                    self.stop_recording()
                elif kind == "saved":
                    _, output, frames, duration = event
                    self._reset_transport()
                    self.timer_label.configure(text=format_duration(duration))
                    self.add_history(output, duration, "Video")
                    self.status_label.configure(text=f"Saved {output.name}   ·   {format_duration(duration)}   ·   Open it from Captures.")
                    if self.closing:
                        self._destroy()
                        return
                    self.show_window()
                    if self.discord_auto_var.get():
                        self._start_export(output, 0, duration)
                elif kind == "error":
                    self._reset_transport()
                    self.show_window()
                    self.status_label.configure(text="Recording failed. Check the recovery folder and app logs.")
                    messagebox.showerror("Recording could not be saved", event[1], parent=self.root)
                    if self.closing:
                        self._destroy()
                        return
                elif kind == "export_progress":
                    self.status_label.configure(text=f"Creating Discord MP4… {event[1]}%   ·   The original is preserved.")
                    self.badge.configure(text=f"● EXPORTING {event[1]}%", fg=ACCENT)
                elif kind in ("export_saved", "export_error", "export_cancelled"):
                    self.export_worker = None
                    self._reset_transport()
                    self.cancel_export_button.pack_forget()
                    if kind == "export_saved":
                        self.add_history(event[1], event[2], "Video")
                        self.status_label.configure(text=f"Discord copy ready: {event[1].name} · {event[3] / 1_000_000:.2f} MB. Open Captures to attach it in Discord.")
                    elif kind == "export_error":
                        self.show_window()
                        messagebox.showerror("Discord export", event[1], parent=self.root)
                        self.status_label.configure(text="Discord export failed. Your original recording is saved.")
                    else:
                        self.status_label.configure(text="Export cancelled. Your original recording is saved.")
                    if self.closing:
                        self._destroy()
                        return
        except queue.Empty:
            pass
        if not self.closing or self.worker or self.export_worker:
            self.root.after(80, self._poll_events)

    def add_history(self, path, duration, kind):
        self.history.insert(0, {"path": str(path), "duration": duration, "kind": kind,
                                "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
        self.history = self.history[:500]
        self.save_settings()
        self._load_library()

    def _load_library(self):
        self.library.delete(*self.library.get_children())
        self.library_rows = {}
        for i, record in enumerate(self.history):
            path = Path(record["path"])
            if path.is_file():
                key = str(i)
                self.library_rows[key] = record
                self.library.insert("", "end", iid=key, values=(path.name, record.get("created", ""),
                                    format_duration(record.get("duration", 0)) if record.get("kind") == "Video" else "Image",
                                    f"{path.stat().st_size / 1048576:.1f} MB"))
        if self.library.get_children():
            self.library.selection_set(self.library.get_children()[0])

    def _selected_path(self):
        selection = self.library.selection()
        return Path(self.library_rows[selection[0]]["path"]) if selection else None

    def _library_preview(self):
        path = self._selected_path()
        if not path or not path.is_file():
            return
        try:
            if path.suffix.lower() == ".png":
                with Image.open(path) as source:
                    image = source.copy()
            else:
                reader = cv2.VideoCapture(str(path))
                ok, frame = reader.read()
                reader.release()
                if not ok:
                    return
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            image.thumbnail((480, 180), Image.Resampling.LANCZOS)
            self.library_photo = ImageTk.PhotoImage(image)
            self.library_image.configure(image=self.library_photo, text="", height=180)
        except Exception:
            logging.exception("Could not render capture thumbnail")

    def open_selected(self):
        path = self._selected_path()
        if path:
            self._open(path)

    def reveal_selected(self):
        path = self._selected_path()
        if path and path.is_file():
            import subprocess
            subprocess.Popen(["explorer.exe", f"/select,{path}"], creationflags=0x08000000)

    def _open(self, path):
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.show_window()
            messagebox.showerror("Unable to open", str(exc), parent=self.root)

    def open_output(self):
        self._open(self.output_directory)

    def open_recovery(self):
        folder = APP_DIR / "recovery"
        folder.mkdir(parents=True, exist_ok=True)
        self._open(folder)

    def open_logs(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._open(APP_DIR)

    def save_settings(self):
        self.config.update({"output_directory": str(self.output_directory), "fps": self.fps_variable.get(),
                            "quality": self.quality_var.get(), "countdown": self.countdown_var.get(),
                            "auto_stop": self.auto_stop_var.get(), "minimize": self.minimize_var.get(),
                            "pointer": self.pointer_var.get(), "noise_reduction": self.noise_var.get(),
                            "discord_limit": self.discord_limit_var.get(), "discord_resolution": self.discord_resolution_var.get(),
                            "discord_auto": self.discord_auto_var.get(),
                            "prefix": self.prefix_var.get(), "audio_enabled": {n: v.get() for n, v in self.audio_enabled.items()},
                            "gains": {n: v.get() for n, v in self.gain_vars.items()},
                            "devices": {n: v.get() for n, v in self.audio_selectors.items()}, "history": self.history})
        try:
            self.store.save(self.config)
        except OSError:
            logging.exception("Settings could not be saved")
            self.status_label.configure(text="Settings could not be saved. Check permissions in the app data folder.")

    def _start_tray(self):
        def command(name):
            return lambda icon, item: self.events.put(("command", name))
        self.tray = pystray.Icon("SimplyCapture", Image.open(resource_path("icon.png")), "Simply Capture",
                                 pystray.Menu(pystray.MenuItem("Show Simply Capture", command("show"), default=True),
                                              pystray.MenuItem(lambda item: "Stop recording" if self.worker else "Cancel countdown" if self.pending_start else "Start recording", command("record"), enabled=lambda item: not self.finalizing and not self.export_worker),
                                              pystray.MenuItem(lambda item: "Resume" if self.worker and self.worker.clock and self.worker.clock.paused else "Pause", command("pause"), enabled=lambda item: self.recording and self.worker.clock is not None),
                                              pystray.Menu.SEPARATOR,
                                              pystray.MenuItem("Open output folder", command("folder")),
                                              pystray.MenuItem("Quit", command("quit"))))
        try:
            self.tray.run_detached()
        except Exception:
            logging.exception("Tray integration unavailable")
            self.tray = None

    def _update_tray_icon(self, paused=False):
        if self.tray:
            self.tray.icon = Image.open(resource_path("icon_recording.png" if self.recording and not paused else "icon.png"))
            if not self.worker:
                self.tray.title = "Simply Capture"
            self.tray.update_menu()

    def hide_to_tray(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def about(self):
        window = tk.Toplevel(self.root)
        window.title("About Simply Capture")
        window.configure(bg=PANEL)
        window.resizable(False, False)
        window.iconbitmap(str(resource_path("icon.ico")))
        frame = tk.Frame(window, bg=PANEL, padx=28, pady=24)
        frame.pack()
        self._label(frame, "", image=self.logo_image).pack(pady=(0, 10))
        self._label(frame, f"Simply Capture {VERSION}", 20, TEXT, True).pack()
        self._label(frame, "Screen recording, without the fuss.", 11).pack(pady=8)
        self._label(frame, "Select a display or region, choose your audio, and record.\n\nCtrl + Shift + R   Start / stop\nCtrl + Shift + P   Pause / resume\nCtrl + Shift + H   Show app\n\nVolume changes and mute apply while recording.\nNoise reduction is applied to the saved microphone track.\nPaused time is excluded from both video and audio.\n\nAll recordings and settings stay on this PC.\nGNU GPL v3 · Built with Python, FFmpeg, and WASAPI.", 10, TEXT, justify="left").pack(pady=14)
        self._button(frame, "View license", lambda: self._open(resource_path("LICENSE"))).pack(side="left")
        self._button(frame, "Close", window.destroy).pack(side="right")
        window.transient(self.root)

    def on_close(self):
        if self.closing:
            return
        if self.pending_start:
            self.cancel_countdown()
        if self.export_worker:
            self.show_window()
            if not messagebox.askokcancel("Cancel export and quit?", "The original recording is already saved. Cancel the Discord export and close the app?", parent=self.root):
                return
            self.closing = True
            self.export_worker.cancel()
            return
        if self.worker:
            self.show_window()
            if not messagebox.askokcancel("Finish recording and quit?", "Your recording will be saved before Simply Capture closes.", parent=self.root):
                return
            self.closing = True
            self.stop_recording()
            return
        self._destroy()

    def _destroy(self):
        self.save_settings()
        self.closing = True
        if self.selection_window:
            self.selection_window.destroy()
        if self.hotkeys:
            self.hotkeys.close()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def export_dialog(self):
        if self.busy:
            return
        source = self._selected_path()
        if not source or source.suffix.lower() != ".mp4":
            messagebox.showinfo("Choose a video", "Select an MP4 in Captures first.", parent=self.root)
            return
        try:
            duration, width, height, has_audio = probe_media(source)
        except ValueError as exc:
            messagebox.showerror("Discord export", str(exc), parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title("Export for Discord")
        window.configure(bg=PANEL)
        window.iconbitmap(str(resource_path("icon.ico")))
        window.resizable(False, False)
        frame = tk.Frame(window, bg=PANEL, padx=24, pady=22)
        frame.pack()
        self._label(frame, "Ready for the chat.", 20, TEXT, True).pack(anchor="w")
        self._label(frame, source.name, 10, wraplength=450, anchor="w").pack(fill="x", pady=(8, 18))
        start_var, length_var = tk.StringVar(value="0"), tk.StringVar(value=f"{duration:.2f}")
        for title, variable, values in (("File limit (MB)", self.discord_limit_var, ("10", "20", "50", "100", "500")),
                                        ("Resolution", self.discord_resolution_var, ("Auto", "480p", "720p", "1080p")),
                                        ("Trim start (seconds)", start_var, None), ("Clip length (seconds)", length_var, None)):
            row = tk.Frame(frame, bg=PANEL)
            row.pack(fill="x", pady=5)
            self._label(row, title, 10, TEXT, width=24, anchor="w").pack(side="left")
            widget = ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=14) if values else ttk.Entry(row, textvariable=variable, width=16)
            widget.pack(side="right")
        estimate = self._label(frame, "", 10, GREEN, wraplength=440, justify="left")
        estimate.pack(anchor="w", pady=16)
        def update(*args):
            try:
                start, length = float(start_var.get()), float(length_var.get())
                if not 0 <= start < duration or start + length > duration + 0.05:
                    raise ValueError("Keep the trimmed clip inside the video.")
                plan = plan_export(length, self.discord_limit_var.get(), self.discord_resolution_var.get(), has_audio)
                estimate.configure(text=f"Target: under {int(self.discord_limit_var.get())} MB · up to {plan.height}p · 30 FPS\nH.264 MP4 with audio · Original preserved", fg=GREEN)
            except (ValueError, TypeError) as exc:
                estimate.configure(text=str(exc), fg=RED)
        for variable in (start_var, length_var, self.discord_limit_var, self.discord_resolution_var):
            # Traces are removed when this dialog closes, so reopened dialogs do not leak callbacks.
            variable.trace_add("write", update)
        traces = [(v, v.trace_info()[0][1]) for v in (start_var, length_var, self.discord_limit_var, self.discord_resolution_var)]
        def close():
            for variable, trace in traces:
                variable.trace_remove("write", trace)
            window.destroy()
        def export():
            try:
                start, length = float(start_var.get()), float(length_var.get())
                if not 0 <= start < duration or start + length > duration + 0.05:
                    raise ValueError("Keep the trimmed clip inside the video.")
                plan_export(length, self.discord_limit_var.get(), self.discord_resolution_var.get(), has_audio)
            except (ValueError, TypeError) as exc:
                messagebox.showerror("Check export settings", str(exc), parent=window)
                return
            close()
            self._start_export(source, start, length)
        row = tk.Frame(frame, bg=PANEL)
        row.pack(fill="x", pady=(5, 0))
        self._button(row, "Cancel", close).pack(side="left")
        self._button(row, "Export MP4", export, "primary").pack(side="right")
        update()
        window.transient(self.root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", close)

    def import_capture(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title="Add existing captures", parent=self.root,
                                            filetypes=[("Videos and screenshots", "*.mp4 *.png")])
        for value in paths:
            path = Path(value)
            if any(record["path"] == str(path) for record in self.history):
                continue
            try:
                duration = probe_media(path)[0] if path.suffix.lower() == ".mp4" else 0
                self.add_history(path, duration, "Video" if path.suffix.lower() == ".mp4" else "Screenshot")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Unable to import capture", str(exc), parent=self.root)

    def _start_export(self, source, start, duration):
        if self.busy:
            return
        source = Path(source)
        output = source.with_name(source.stem + "-discord.mp4")
        counter = 2
        while output.exists():
            output = source.with_name(source.stem + f"-discord-{counter}.mp4")
            counter += 1
        self.export_worker = DiscordExporter(source, output, self.events, self.discord_limit_var.get(),
                                            self.discord_resolution_var.get(), start, duration)
        self._set_locked(True)
        self.record_button.configure(state="disabled")
        self.badge.configure(text="● EXPORTING", fg=ACCENT)
        self.status_label.configure(text="Preparing a Discord-sized MP4…")
        if not hasattr(self, "cancel_export_button"):
            self.cancel_export_button = self._button(self.status_label.master, "Cancel export", lambda: self.export_worker.cancel() if self.export_worker else None)
        self.cancel_export_button.pack(anchor="w", pady=(4, 0))
        self.save_settings()
        self.export_worker.start()
