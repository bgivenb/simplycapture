import queue
import tempfile
import unittest
import wave
from pathlib import Path

import cv2
import numpy as np

from discord_export import DiscordExporter, plan_export, probe_media
from audio_capture import mux_audio


class DiscordTests(unittest.TestCase):
    def test_size_budget_accounts_for_audio(self):
        plan = plan_export(120, 20, has_audio=True)
        estimated = (plan.video_kbps + plan.audio_kbps) * 1000 * 120 / 8
        self.assertLess(estimated, plan.limit_bytes)
        self.assertEqual(plan.height, 720)
        self.assertLess(plan.video_kbps, plan_export(120, 20, has_audio=False).video_kbps)

    def test_unusable_duration_requests_trimming(self):
        for value in (0, float("nan"), float("inf"), 10000):
            with self.assertRaises(ValueError):
                plan_export(value, 10)

    def test_export_compresses_and_trims_without_changing_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            raw_video = Path(folder) / "video.mp4"
            output = Path(folder) / "discord.mp4"
            writer = cv2.VideoWriter(str(raw_video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (512, 288))
            rng = np.random.default_rng(42)
            for _ in range(90):
                writer.write(rng.integers(0, 256, (288, 512, 3), dtype=np.uint8))
            writer.release()
            audio = Path(folder) / "tone.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 48000, 0, "NONE", "none"))
                tone = (np.sin(np.arange(144000) * 2 * np.pi * 440 / 48000) * 4000).astype(np.int16)
                wav.writeframes(tone.tobytes())
            mux_audio(raw_video, [audio], source, 3)
            before = source.read_bytes()
            events = queue.Queue()
            exporter = DiscordExporter(source, output, events, limit_mb=1, start=0.5, duration=2)
            exporter.start()
            exporter.join(60)
            self.assertFalse(exporter.is_alive())
            results = []
            while not events.empty():
                results.append(events.get())
            self.assertTrue(any(event[0] == "export_saved" for event in results), results)
            self.assertLess(output.stat().st_size, 1_000_000)
            self.assertEqual(source.read_bytes(), before)
            duration, width, height, has_audio = probe_media(output)
            self.assertAlmostEqual(duration, 2, delta=0.1)
            self.assertEqual(width % 2, 0)
            self.assertEqual(height % 2, 0)
            self.assertTrue(has_audio)

    def test_cancel_leaves_no_export(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 64))
            for _ in range(30):
                writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
            writer.release()
            output = Path(folder) / "cancelled.mp4"
            events = queue.Queue()
            worker = DiscordExporter(source, output, events)
            worker.cancel()
            worker.start()
            worker.join(10)
            self.assertFalse(output.exists())
            self.assertTrue(any(e[0] == "export_cancelled" for e in list(events.queue)))


if __name__ == "__main__":
    unittest.main()
