import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from PIL import Image  # noqa: E402

from actions.executor import Screenshot  # noqa: E402


def _make_screenshot(scale=1.5, resized_size=(1280, 800)):
    img = Image.new("RGB", resized_size, (0, 0, 0))
    real_size = (int(resized_size[0] * scale), int(resized_size[1] * scale))
    return Screenshot(
        full_image=img,
        api_bytes=b"",
        real_size=real_size,
        resized_size=resized_size,
        scale=scale,
    )


class TestScreenshotCoords(unittest.TestCase):
    def test_to_real_coords_scales_up(self):
        s = _make_screenshot(scale=2.0)
        self.assertEqual(s.to_real_coords(100, 50), (200, 100))

    def test_to_real_coords_rounds(self):
        s = _make_screenshot(scale=1.5)
        self.assertEqual(s.to_real_coords(10, 10), (15, 15))

    def test_within_bounds_true_inside(self):
        s = _make_screenshot(resized_size=(1280, 800))
        self.assertTrue(s.is_within_bounds(640, 400))

    def test_within_bounds_false_outside(self):
        s = _make_screenshot(resized_size=(1280, 800))
        self.assertFalse(s.is_within_bounds(2000, 400))
        self.assertFalse(s.is_within_bounds(-10, 400))

    def test_within_bounds_edges_inclusive(self):
        s = _make_screenshot(resized_size=(1280, 800))
        self.assertTrue(s.is_within_bounds(0, 0))
        self.assertTrue(s.is_within_bounds(1280, 800))


if __name__ == "__main__":
    unittest.main()
