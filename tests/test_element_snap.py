import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from core.loop import _maybe_snap_to_element  # noqa: E402


class _FakeExecutor:
    """Stands in for ActionExecutor -- returns a canned bounding box instead
    of actually querying Windows UI Automation, so this cross-check logic
    can be verified without a real Windows machine."""

    def __init__(self, bounds):
        self._bounds = bounds
        self.calls = []

    def find_element_bounds(self, name_hint):
        self.calls.append(name_hint)
        return self._bounds


class TestMaybeSnapToElement(unittest.TestCase):
    def test_no_hint_returns_original_unchanged(self):
        executor = _FakeExecutor(bounds=(10, 10, 50, 50))
        xy, note = _maybe_snap_to_element(executor, None, (500, 500))
        self.assertEqual(xy, (500, 500))
        self.assertIsNone(note)
        self.assertEqual(executor.calls, [])  # never even looked it up

    def test_no_match_found_falls_back_to_original(self):
        executor = _FakeExecutor(bounds=None)
        xy, note = _maybe_snap_to_element(executor, "Compose button", (500, 500))
        self.assertEqual(xy, (500, 500))
        self.assertIsNone(note)

    def test_close_match_snaps_to_element_center(self):
        # Model guessed (505, 505); the real element is a 40x40 box
        # centered at (520, 520) -- close enough to trust.
        executor = _FakeExecutor(bounds=(500, 500, 540, 540))
        xy, note = _maybe_snap_to_element(executor, "Compose button", (505, 505))
        self.assertEqual(xy, (520, 520))
        self.assertIsNotNone(note)
        self.assertIn("Compose button", note)

    def test_far_match_is_not_trusted(self):
        # A same-named element exists, but it's 800px away from the model's
        # guess -- more likely a different, similarly-labeled control than
        # a correction, so the original guess is kept.
        executor = _FakeExecutor(bounds=(1300, 900, 1340, 940))
        xy, note = _maybe_snap_to_element(executor, "Send", (100, 100))
        self.assertEqual(xy, (100, 100))
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
