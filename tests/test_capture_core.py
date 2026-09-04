from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from capture_core import normalize_region, recording_path, validate_fps


class CaptureCoreTests(unittest.TestCase):
    def test_region_normalizes_reverse_drag(self):
        region = normalize_region(401, 305, 100, 101)
        self.assertEqual((region.left, region.top), (100, 101))
        self.assertEqual((region.width, region.height), (300, 204))

    def test_region_dimensions_are_even_for_video_codec(self):
        region = normalize_region(0, 0, 101, 99)
        self.assertEqual((region.width, region.height), (100, 98))

    def test_tiny_region_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_region(1, 1, 2, 2)

    def test_fps_bounds(self):
        self.assertEqual(validate_fps("20"), 20)
        for value in (0, 61, "not-a-number"):
            with self.assertRaises((ValueError, TypeError)):
                validate_fps(value)

    def test_recording_path_is_predictable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = recording_path(directory, datetime(2026, 9, 4, 12, 30, 45))
            self.assertEqual(result, Path(directory).resolve() / "simply-capture-20260904-123045.mp4")


if __name__ == "__main__":
    unittest.main()
