import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from PIL import Image  # noqa: E402

from actions.executor import crop_region, screens_differ  # noqa: E402


def _solid(color, size=(200, 200)):
    return Image.new("RGB", size, color)


class TestScreensDiffer(unittest.TestCase):
    def test_identical_images_not_different(self):
        a = _solid((100, 100, 100))
        b = _solid((100, 100, 100))
        self.assertFalse(screens_differ(a, b))

    def test_very_different_images_are_different(self):
        a = _solid((0, 0, 0))
        b = _solid((255, 255, 255))
        self.assertTrue(screens_differ(a, b))

    def test_small_change_below_threshold_not_flagged_on_whole_image(self):
        # A tiny patch on a big image barely moves the mean -- this is the
        # exact "coarse whole-screen diff" limitation crop_region() exists
        # to work around, demonstrated directly.
        a = _solid((10, 10, 10), size=(400, 400))
        b = a.copy()
        patch = Image.new("RGB", (6, 6), (250, 250, 250))
        b.paste(patch, (5, 5))
        self.assertFalse(screens_differ(a, b))

    def test_localized_diff_catches_the_same_small_change(self):
        a = _solid((10, 10, 10), size=(400, 400))
        b = a.copy()
        patch = Image.new("RGB", (60, 60), (250, 250, 250))
        b.paste(patch, (170, 170))
        region_a = crop_region(a, 200, 200)
        region_b = crop_region(b, 200, 200)
        self.assertTrue(screens_differ(region_a, region_b))


class TestCropRegion(unittest.TestCase):
    def test_crop_size_normal_case(self):
        img = _solid((0, 0, 0), size=(400, 400))
        cropped = crop_region(img, 200, 200, box=100)
        self.assertEqual(cropped.size, (100, 100))

    def test_crop_clamped_near_edge(self):
        img = _solid((0, 0, 0), size=(400, 400))
        cropped = crop_region(img, 5, 5, box=100)
        # Still full box size -- clamped to stay inside the image, not cut off.
        self.assertEqual(cropped.size, (100, 100))

    def test_crop_larger_than_image_falls_back_to_full(self):
        img = _solid((0, 0, 0), size=(50, 50))
        cropped = crop_region(img, 25, 25, box=100)
        self.assertEqual(cropped.size, (50, 50))


if __name__ == "__main__":
    unittest.main()
