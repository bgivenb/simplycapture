"""Windows WASAPI capture and local MP4 audio mixing."""
from pathlib import Path
from time import monotonic
import subprocess
import threading
import wave

import imageio_ffmpeg
import numpy as np
import pyaudiowpatch as pa


class AudioSettings:
    def __init__(self):
        self.devices = {}
        self.gains = {"PC audio": 0.7, "Microphone": 0.7}
        self.levels = {"PC audio": (0, 0), "Microphone": (0, 0)}
        self.lock = threading.Lock()

    def set_gain(self, name, value):
        with self.lock:
            self.gains[name] = max(0, min(150, float(value))) / 100

    def meter(self, name):
        with self.lock:
            level, timestamp = self.levels[name]
            return level if monotonic() - timestamp < 0.3 else 0


def list_devices():
    with pa.PyAudio() as audio:
        pc = list(audio.get_loopback_device_info_generator())
        mic = [d for d in audio.get_device_info_generator()
               if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")
               and d["hostApi"] == audio.get_host_api_info_by_type(pa.paWASAPI)["index"]]
        wasapi = audio.get_host_api_info_by_type(pa.paWASAPI)
        for devices, getter in ((pc, audio.get_default_wasapi_loopback),
                                (mic, lambda: {"index": wasapi["defaultInputDevice"]})):
            try:
                default = getter()["index"]
                devices.sort(key=lambda d: d["index"] != default)
            except OSError:
                pass
        return {"PC audio": pc, "Microphone": mic}


class AudioCapture:
    def __init__(self, settings, directory, clock=None):
        self.settings = settings
        self.directory = Path(directory)
        self.audio = None
        self.streams = []
        self.files = []
        self.paths = []
        self.error = None
        self.clock = clock

    def start(self):
        self.audio = pa.PyAudio()
        try:
            for name, index in self.settings.devices.items():
                device = self.audio.get_device_info_by_index(index)
                channels = min(2, int(device["maxInputChannels"]))
                rate = int(device["defaultSampleRate"])
                path = self.directory / f"{len(self.paths)}.wav"
                wav = wave.open(str(path), "wb")
                wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
                self.files.append(wav)
                self.paths.append(path)
                callback = self._callback(name, wav, channels, rate)
                stream = self.audio.open(format=pa.paInt16, channels=channels,
                                         rate=rate, input=True, input_device_index=index,
                                         frames_per_buffer=1024, stream_callback=callback,
                                         start=False)
                self.streams.append(stream)
            self.epoch = monotonic()
            for stream in self.streams:
                stream.start_stream()
        except Exception:
            self.close()
            raise

    def _callback(self, name, wav, channels, rate):
        written = 0
        def callback(data, count, timing, status):
            nonlocal written
            try:
                if self.clock and self.clock.paused:
                    return (None, pa.paContinue)
                # Keep silent loopback gaps aligned with the video clock.
                elapsed = self.clock.elapsed() if self.clock else monotonic() - self.epoch
                target = max(0, round(elapsed * rate) - count)
                gap = max(0, target - written)
                if gap:
                    wav.writeframesraw(bytes(gap * channels * 2))
                    written += gap
                with self.settings.lock:
                    gain = self.settings.gains[name]
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                with self.settings.lock:
                    self.settings.levels[name] = (min(1.0, float(np.max(np.abs(samples))) * gain / 32768), monotonic())
                wav.writeframesraw(np.clip(samples * gain, -32768, 32767).astype(np.int16).tobytes())
                written += count
                return (None, pa.paContinue)
            except Exception as exc:
                self.error = exc
                return (None, pa.paAbort)
        return callback

    def close(self):
        for stream in self.streams:
            try:
                stream.stop_stream()
            except OSError:
                pass
            finally:
                stream.close()
        self.streams.clear()
        for wav in self.files:
            if hasattr(self, "epoch"):
                elapsed = self.clock.elapsed() if self.clock else monotonic() - self.epoch
                missing = max(0, round(elapsed * wav.getframerate()) - wav.getnframes())
                if missing:
                    wav.writeframesraw(bytes(missing * wav.getnchannels() * 2))
            wav.close()
        self.files.clear()
        if self.audio:
            self.audio.terminate()
            self.audio = None


def mux_audio(video, paths, output, duration, noise_indices=()):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(video)]
    for path in paths:
        command.extend(["-i", str(path)])
    if paths:
        filters = []
        inputs = []
        for i in range(len(paths)):
            if i in noise_indices:
                filters.append(f"[{i + 1}:a]highpass=f=80,afftdn=nf=-25[n{i}]")
                inputs.append(f"[n{i}]")
            else:
                inputs.append(f"[{i + 1}:a]")
        filters.append(f"{''.join(inputs)}amix=inputs={len(paths)}:normalize=0:duration=longest,"
                       "alimiter=limit=0.95:latency=1:level=0,apad[a]")
        command.extend(["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[a]",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
    else:
        command.extend(["-map", "0:v", "-an"])
    command.extend(["-c:v", "copy", "-t", str(duration), "-movflags", "+faststart", str(output)])
    result = subprocess.run(command, capture_output=True, creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-2000:])
