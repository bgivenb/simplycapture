"""Background screen capture, H.264 encoding and recoverable finalization."""
import ctypes
from ctypes import wintypes
import logging
import math
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from time import monotonic

import cv2
import imageio_ffmpeg
import mss
import numpy as np

from app_state import APP_DIR, RecordingClock
from audio_capture import AudioCapture, mux_audio

QUALITY = {"Balanced": 23, "Sharp text": 18, "Smaller files": 28}


class CaptureWorker(threading.Thread):
    def __init__(self, region, output_file, fps, stop_event, events, audio_settings,
                 quality="Balanced", pointer=False, auto_stop=0, noise_reduction=False,
                 recovery_root=None):
        super().__init__(name="capture-worker", daemon=True)
        self.region, self.output_file, self.fps = region, Path(output_file), fps
        self.stop_event, self.events, self.audio_settings = stop_event, events, audio_settings
        self.quality, self.pointer = quality, pointer
        self.auto_stop, self.noise_reduction = auto_stop, noise_reduction
        self.recovery_root = Path(recovery_root) if recovery_root else APP_DIR / "recovery"
        self.clock = None

    def pause(self):
        if self.clock and not self.stop_event.is_set():
            return self.clock.toggle_pause()
        return False

    def run(self):
        folder = self.recovery_root / uuid.uuid4().hex
        video = folder / "video.mkv"
        staging = self.output_file.with_name(self.output_file.stem + ".partial.mp4")
        encoder, audio, error_log = None, None, None
        result = None
        frames = 0
        try:
            folder.mkdir(parents=True, exist_ok=True)
            error_log = (folder / "encoder.log").open("wb")
            command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "rawvideo",
                       "-pix_fmt", "bgr24", "-s", f"{self.region.width}x{self.region.height}",
                       "-r", str(self.fps), "-i", "-", "-an", "-c:v", "libx264",
                       "-preset", "veryfast", "-crf", str(QUALITY.get(self.quality, 23)),
                       "-pix_fmt", "yuv420p", str(video)]
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=error_log, creationflags=0x08000000)
            self.clock = RecordingClock()
            if self.audio_settings.devices:
                audio = AudioCapture(self.audio_settings, folder, clock=self.clock)
                audio.start()
            self.events.put(("started",))
            last_progress = -1
            with mss.MSS() as screen:
                while not self.stop_event.is_set():
                    if audio and audio.error:
                        raise RuntimeError(f"Audio input was interrupted: {audio.error}")
                    if self.clock.paused:
                        self.stop_event.wait(0.03)
                        continue
                    elapsed = self.clock.elapsed()
                    if self.auto_stop and elapsed >= self.auto_stop:
                        self.stop_event.set()
                        break
                    frame = cv2.cvtColor(np.asarray(screen.grab(self.region.as_mss())), cv2.COLOR_BGRA2BGR)
                    if self.pointer:
                        point = wintypes.POINT()
                        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                            x, y = point.x - self.region.left, point.y - self.region.top
                            if 0 <= x < self.region.width and 0 <= y < self.region.height:
                                cv2.circle(frame, (x, y), 14, (185, 120, 250), 2, cv2.LINE_AA)
                                cv2.circle(frame, (x, y), 3, (255, 255, 255), -1, cv2.LINE_AA)
                    target = max(frames + 1, math.floor(self.clock.elapsed() * self.fps) + 1)
                    while frames < target:
                        encoder.stdin.write(frame.tobytes())
                        frames += 1
                    if elapsed - last_progress >= 0.25:
                        self.events.put(("progress", frames / self.fps, frames, video.stat().st_size if video.exists() else 0))
                        last_progress = elapsed
                    self.stop_event.wait(max(0, frames / self.fps - self.clock.elapsed()))
            if audio:
                audio.close()
                if audio.error:
                    raise RuntimeError(f"Audio input was interrupted: {audio.error}")
            encoder.stdin.close()
            if encoder.wait(timeout=60):
                error_log.flush()
                raise RuntimeError((folder / "encoder.log").read_text(errors="replace")[-1200:])
            if not frames:
                raise RuntimeError("The recording stopped before a frame was captured.")
            self.events.put(("finishing",))
            paths = audio.paths if audio else []
            noise_indices = [i for i, name in enumerate(self.audio_settings.devices)
                             if name == "Microphone" and self.noise_reduction]
            mux_audio(video, paths, staging, frames / self.fps, noise_indices=noise_indices)
            staging.replace(self.output_file)
            result = ("saved", self.output_file, frames, frames / self.fps)
        except Exception as exc:
            logging.exception("Recording failed")
            result = ("error", f"{exc}\n\nRecoverable files: {folder}", folder)
        finally:
            if audio:
                try:
                    audio.close()
                except Exception:
                    logging.exception("Audio cleanup failed")
            if encoder:
                if encoder.stdin and not encoder.stdin.closed:
                    try:
                        encoder.stdin.close()
                    except OSError:
                        pass
                if encoder.poll() is None:
                    try:
                        encoder.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        encoder.kill()
                        encoder.wait()
            if error_log:
                error_log.close()
            if result and result[0] == "saved":
                shutil.rmtree(folder, ignore_errors=True)
            # Terminal events are published only after all files and devices close.
            self.events.put(result or ("error", "Recording could not be completed.", folder))
