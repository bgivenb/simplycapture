import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event
from queue import Queue
from unittest.mock import patch

import cv2
import imageio_ffmpeg
import numpy as np

from audio_capture import AudioCapture, AudioSettings, mux_audio
from app_state import RecordingClock


class AudioTests(unittest.TestCase):
    def test_paused_microphone_samples_are_excluded(self):
        now = [0.0]
        clock = RecordingClock(now=lambda: now[0])
        capture = AudioCapture(AudioSettings(), ".", clock=clock)
        capture.epoch = 0
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paused.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 1000, 0, "NONE", "none"))
                callback = capture._callback("Microphone", wav, 1, 1000)
                active = np.full(100, 10000, dtype=np.int16).tobytes()
                paused = np.full(100, 20000, dtype=np.int16).tobytes()
                now[0] = 0.1
                callback(active, 100, {}, 0)
                clock.toggle_pause()
                now[0] = 10
                callback(paused, 100, {}, 0)
                clock.toggle_pause()
                now[0] = 10.1
                callback(active, 100, {}, 0)
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.getnframes(), 200)
                np.testing.assert_array_equal(np.frombuffer(wav.readframes(200), dtype=np.int16), 7000)

    def test_live_gain_and_mute(self):
        settings = AudioSettings()
        capture = AudioCapture(settings, ".")
        capture.epoch = 0
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gain.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 1000, 0, "NONE", "none"))
                callback = capture._callback("Microphone", wav, 1, 1000)
                data = np.full(100, 10000, dtype=np.int16).tobytes()
                settings.set_gain("Microphone", 50)
                with patch("audio_capture.monotonic", return_value=0.1):
                    callback(data, 100, {}, 0)
                settings.set_gain("Microphone", 0)
                with patch("audio_capture.monotonic", return_value=0.2):
                    callback(data, 100, {}, 0)
            with wave.open(str(path)) as wav:
                samples = np.frombuffer(wav.readframes(200), dtype=np.int16)
            np.testing.assert_array_equal(samples[:100], 5000)
            np.testing.assert_array_equal(samples[100:], 0)

    def test_two_source_mix_produces_video_and_audible_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "video.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 20, (64, 64))
            for _ in range(20):
                writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
            writer.release()
            paths = []
            for i, channels in enumerate((1, 2)):
                path = folder / f"{i}.wav"
                tone = (np.sin(np.arange(48000) * 2 * np.pi * (440 + 220*i) / 48000) * 6000).astype(np.int16)
                with wave.open(str(path), "wb") as wav:
                    wav.setparams((channels, 2, 48000, 0, "NONE", "none"))
                    wav.writeframes(np.repeat(tone[:, None], channels, axis=1).tobytes())
                paths.append(path)
            output = folder / "mixed.mp4"
            mux_audio(video, paths, output, 1)
            decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(output),
                                      "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                                     capture_output=True, check=True, creationflags=0x08000000)
            samples = np.frombuffer(decoded.stdout, dtype=np.int16)
            self.assertGreater(np.sqrt(np.mean(samples.astype(float)**2)), 1000)
            self.assertAlmostEqual(len(samples) / 48000, 1, delta=0.05)
            reader = cv2.VideoCapture(str(output))
            self.assertEqual(reader.get(cv2.CAP_PROP_FRAME_COUNT), 20)
            reader.release()


if __name__ == "__main__":
    unittest.main()
