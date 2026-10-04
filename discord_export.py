"""Size-budgeted, two-pass H.264 exports suitable for Discord attachments."""
import math
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import imageio_ffmpeg


def probe_media(path):
    reader = cv2.VideoCapture(str(path))
    try:
        fps = reader.get(cv2.CAP_PROP_FPS)
        frames = reader.get(cv2.CAP_PROP_FRAME_COUNT)
        width, height = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH)), int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        reader.release()
    duration = frames / fps if fps else 0
    if not math.isfinite(duration) or duration <= 0 or width < 2 or height < 2:
        raise ValueError("This file does not contain a readable video.")
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                            capture_output=True, creationflags=0x08000000)
    return duration, width, height, "Audio:" in result.stderr.decode(errors="replace")


@dataclass(frozen=True)
class ExportPlan:
    duration: float
    limit_bytes: int
    video_kbps: int
    audio_kbps: int
    height: int


def plan_export(duration, limit_mb=20, resolution="Auto", has_audio=True):
    duration, limit_mb = float(duration), float(limit_mb)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Choose a positive clip length.")
    if not math.isfinite(limit_mb) or not 1 <= limit_mb <= 500:
        raise ValueError("Choose a size limit between 1 and 500 MB.")
    limit_bytes = int(limit_mb * 1_000_000)
    # Reserve 8% for MP4 overhead and encoder variance, then verify actual bytes.
    total_kbps = int(limit_bytes * 8 * 0.92 / duration / 1000)
    audio_kbps = (96 if total_kbps >= 500 else 48) if has_audio else 0
    video_kbps = total_kbps - audio_kbps
    if video_kbps < 100:
        raise ValueError("This clip is too long for the size limit. Trim it or choose a larger limit.")
    height = (1080 if video_kbps >= 3500 else 720 if video_kbps >= 1100 else 480 if video_kbps >= 400 else 360)
    if resolution != "Auto":
        height = int(str(resolution).rstrip("p"))
        if height not in (360, 480, 720, 1080):
            raise ValueError("Choose a supported export resolution.")
    return ExportPlan(duration, limit_bytes, video_kbps, audio_kbps, height)


class ExportCancelled(Exception):
    pass


class DiscordExporter(threading.Thread):
    def __init__(self, source, output, events, limit_mb=20, resolution="Auto", start=0, duration=None):
        super().__init__(name="discord-export", daemon=True)
        self.source, self.output, self.events = Path(source), Path(output), events
        self.limit_mb, self.resolution, self.trim_start, self.duration = limit_mb, resolution, float(start), duration
        self.cancel_event = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()

    def cancel(self):
        self.cancel_event.set()
        with self.process_lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def _run_pass(self, command, log, phase, duration):
        if self.cancel_event.is_set():
            raise ExportCancelled()
        command.extend(["-progress", "pipe:1", "-nostats"])
        with self.process_lock:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True,
                                            creationflags=0x08000000)
        process = self.process
        for line in process.stdout:
            if self.cancel_event.is_set():
                process.terminate()
                break
            if line.startswith("out_time_us="):
                try:
                    elapsed = int(line.split("=", 1)[1]) / 1_000_000
                    self.events.put(("export_progress", min(99, int((phase + elapsed / duration) * 50))))
                except ValueError:
                    pass
        process.wait()
        process.stdout.close()
        if self.cancel_event.is_set():
            raise ExportCancelled()
        if process.returncode:
            raise RuntimeError("Video export failed. Check the app log or try another clip.")

    def run(self):
        try:
            full_duration, width, height, has_audio = probe_media(self.source)
            duration = full_duration - self.trim_start if self.duration is None else float(self.duration)
            if not math.isfinite(self.trim_start) or self.trim_start < 0 or self.trim_start >= full_duration:
                raise ValueError("The trim start must be inside the video.")
            if duration <= 0 or self.trim_start + duration > full_duration + 0.05:
                raise ValueError("The clip must end inside the video.")
            plan = plan_export(duration, self.limit_mb, self.resolution, has_audio)
            if self.source.resolve() == self.output.resolve() or self.output.exists():
                raise ValueError("Choose a new filename for the export.")
            with tempfile.TemporaryDirectory(prefix="simply-discord-", dir=self.output.parent) as directory:
                directory = Path(directory)
                staging, passlog = directory / "export.mp4", directory / "pass"
                # Cap both dimensions, preserve aspect ratio, avoid upscaling, and ensure even dimensions.
                cap_height = min(height, plan.height)
                cap_width = min(width, round(plan.height * 16 / 9))
                scale = f"scale={cap_width}:{cap_height}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30"
                bitrate = plan.video_kbps
                for attempt in range(2):
                    for phase in range(2):
                        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss", str(self.trim_start), "-i", str(self.source),
                                   "-t", str(duration), "-vf", scale, "-c:v", "libx264", "-preset", "fast",
                                   "-pix_fmt", "yuv420p", "-b:v", f"{bitrate}k", "-pass", str(phase + 1),
                                   "-passlogfile", str(passlog)]
                        if phase == 0:
                            command.extend(["-an", "-f", "null", os_null()])
                        else:
                            command.extend(["-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{max(48, plan.audio_kbps)}k",
                                            "-ac", "2", "-ar", "48000", "-movflags", "+faststart", str(staging)])
                        with (directory / "export.log").open("w") as log:
                            self._run_pass(command, log, phase, duration)
                    size = staging.stat().st_size
                    if size < plan.limit_bytes:
                        staging.replace(self.output)
                        self.events.put(("export_saved", self.output, duration, size))
                        return
                    bitrate = max(50, int(bitrate * plan.limit_bytes / size * 0.88))
                raise ValueError("The export could not fit the limit. Choose a shorter clip or larger limit.")
        except ExportCancelled:
            self.events.put(("export_cancelled",))
        except Exception as exc:
            import logging
            logging.exception("Discord export failed")
            self.events.put(("export_error", str(exc)))


def os_null():
    import os
    return os.devnull
