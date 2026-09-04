"""Pure helpers for validating and naming Simply Capture recordings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def as_mss(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def normalize_region(start_x: int, start_y: int, end_x: int, end_y: int) -> CaptureRegion:
    """Normalize a drag selection and make dimensions codec-safe and even."""

    left = min(start_x, end_x)
    top = min(start_y, end_y)
    width = abs(end_x - start_x)
    height = abs(end_y - start_y)
    if width < 2 or height < 2:
        raise ValueError("Select a region at least 2×2 pixels.")
    width -= width % 2
    height -= height % 2
    return CaptureRegion(left, top, width, height)


def validate_fps(fps: float) -> float:
    value = float(fps)
    if not 1 <= value <= 60:
        raise ValueError("Frame rate must be between 1 and 60 FPS.")
    return value


def recording_path(folder: Union[str, Path], now: Optional[datetime] = None) -> Path:
    directory = Path(folder).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("Choose an existing output directory.")
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return directory / f"simply-capture-{timestamp}.mp4"
