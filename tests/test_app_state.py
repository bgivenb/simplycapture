import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app_state import RecordingClock, StateStore, safe_prefix
from capture_core import recording_path


class StateTests(unittest.TestCase):
    def test_pause_excludes_time_and_resumes(self):
        time = [0.0]
        clock = RecordingClock(now=lambda: time[0])
        time[0] = 3
        self.assertTrue(clock.toggle_pause())
        time[0] = 20
        self.assertEqual(clock.elapsed(), 3)
        self.assertFalse(clock.toggle_pause())
        time[0] = 22
        self.assertEqual(clock.elapsed(), 5)

    def test_settings_survive_restart_and_damaged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StateStore(Path(folder) / "settings.json")
            config = store.load()
            config["gains"]["Microphone"] = 125
            config["discord_limit"] = "50"
            store.save(config)
            self.assertEqual(store.load()["gains"]["Microphone"], 125)
            self.assertEqual(store.load()["discord_limit"], "50")
            store.path.write_text("{unfinished", encoding="utf-8")
            self.assertEqual(store.load()["gains"]["Microphone"], 70)

    def test_output_never_overwrites_existing_recordings(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 10, 4)
            first = recording_path(folder, now, prefix="demo")
            first.touch()
            second = recording_path(folder, now, prefix="demo")
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, "demo-20261004-000000-2.mp4")
            self.assertNotIn("/", safe_prefix("../../example<>"))


if __name__ == "__main__":
    unittest.main()
