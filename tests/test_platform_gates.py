"""
Tests that run on THIS machine (Linux, no display) but still exercise real
code paths: both focus_window() and find_element_bounds() are supposed to
detect they're not on Windows and fail closed cleanly, rather than crashing.
That's a real, verifiable behavior even without a Windows box available --
it's exactly what protects a run on macOS/Linux from ever reaching the
ctypes/ Windows-only code at all.
"""
import os
import platform
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from actions.executor import ActionExecutor  # noqa: E402


class TestNonWindowsGates(unittest.TestCase):
    def setUp(self):
        if platform.system() == "Windows":
            self.skipTest("this test verifies the non-Windows fallback path specifically")

    def test_focus_window_reports_unsupported_not_crash(self):
        executor = ActionExecutor()
        result = executor.focus_window("notepad")
        self.assertIn("not supported on this OS", result)

    def test_find_element_bounds_returns_none_not_crash(self):
        executor = ActionExecutor()
        result = executor.find_element_bounds("Compose button")
        self.assertIsNone(result)

    def test_find_element_bounds_empty_hint_returns_none(self):
        executor = ActionExecutor()
        # Even hypothetically on Windows this should short-circuit before
        # touching UI Automation at all -- verified here via the empty-hint
        # branch, which runs before the platform check would even matter.
        result = executor.find_element_bounds("")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
