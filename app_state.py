"""Durable user settings and a shared, pause-aware recording clock."""
import json
import os
import re
import threading
from pathlib import Path
from time import monotonic

VERSION = "2.0.0"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SimplyCapture"
DEFAULTS = {"fps": "30", "quality": "Balanced", "countdown": "3",
            "auto_stop": "Off", "minimize": True, "pointer": False,
            "noise_reduction": False, "prefix": "simply-capture",
            "audio_enabled": {"PC audio": False, "Microphone": False},
            "gains": {"PC audio": 70, "Microphone": 70}, "devices": {}, "history": []}


class StateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else APP_DIR / "settings.json"

    def load(self):
        defaults = json.loads(json.dumps(DEFAULTS))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, ValueError):
            pass
        # A hand-edited or interrupted settings file must not prevent startup.
        for name in ("audio_enabled", "gains", "devices"):
            if not isinstance(defaults.get(name), dict):
                defaults[name] = DEFAULTS[name].copy()
        if not isinstance(defaults.get("history"), list):
            defaults["history"] = []
        return defaults

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class RecordingClock:
    def __init__(self, now=monotonic):
        self.now = now
        self.lock = threading.Lock()
        self.started = now()
        self.paused_at = None
        self.paused_total = 0.0

    def elapsed(self):
        with self.lock:
            return max(0.0, (self.paused_at if self.paused_at is not None else self.now())
                       - self.started - self.paused_total)

    @property
    def paused(self):
        with self.lock:
            return self.paused_at is not None

    def toggle_pause(self):
        with self.lock:
            if self.paused_at is None:
                self.paused_at = self.now()
            else:
                self.paused_total += self.now() - self.paused_at
                self.paused_at = None
            return self.paused_at is not None


def safe_prefix(value):
    return re.sub(r"[^A-Za-z0-9 _-]", "", str(value)).strip(" .")[:60] or "simply-capture"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
