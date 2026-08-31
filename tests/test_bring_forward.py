"""
Regression tests for _bring_forward_after_open(), the fix for a real bug: a
weather search opened successfully in a background browser tab while a dev
environment stayed focused. The next screenshot never showed the result, the
model concluded the action failed, and it launched a second redundant
browser window. Root cause: open_url()/launch_app() called webbrowser.open()
/ subprocess.Popen() and trusted the OS to hand over focus, which it does
not always do.

These tests can't exercise the real Windows API (this sandbox has no
Windows box), so they patch platform.system() to report "Windows" and patch
focus_window() itself -- already covered by its own tests -- to verify the
_bring_forward_after_open() control flow: it tries each hint in order,
stops at the first real success, and correctly reports "no confirmation"
when every hint fails, rather than silently claiming success either way.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from actions.executor import ActionExecutor  # noqa: E402


class TestBringForwardAfterOpen(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor()

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_non_windows_short_circuits_to_none(self, mock_system, mock_sleep):
        mock_system.return_value = "Linux"
        result = self.executor._bring_forward_after_open(["chrome"])
        self.assertIsNone(result)

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_first_matching_hint_wins(self, mock_system, mock_sleep):
        with patch.object(
            self.executor, "focus_window", return_value="focused window: 'Google Chrome'"
        ) as mock_focus:
            result = self.executor._bring_forward_after_open(["chrome", "edge", "firefox"])
        self.assertEqual(result, "brought 'chrome' window to the foreground")
        mock_focus.assert_called_once_with("chrome")

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_falls_through_to_second_hint_when_first_fails(self, mock_system, mock_sleep):
        outcomes = iter([
            "no window found matching 'chrome'",
            "focused window: 'Microsoft Edge'",
        ])
        with patch.object(self.executor, "focus_window", side_effect=lambda name: next(outcomes)) as mock_focus:
            result = self.executor._bring_forward_after_open(["chrome", "edge"])
        self.assertEqual(result, "brought 'edge' window to the foreground")
        self.assertEqual(mock_focus.call_count, 2)

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_returns_none_not_a_false_success_when_nothing_matches(self, mock_system, mock_sleep):
        with patch.object(self.executor, "focus_window", return_value="no window found matching 'x'"):
            result = self.executor._bring_forward_after_open(["chrome", "edge", "firefox"])
        # This is the crux of the original bug: nothing confirmed forward,
        # so this must be None, never a string implying it worked.
        self.assertIsNone(result)

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_open_url_appends_bracketed_note_only_on_confirmed_success(self, mock_system, mock_sleep):
        with patch.object(
            self.executor, "focus_window", return_value="focused window: 'Google Chrome'"
        ):
            result = self.executor.open_url("weather today")
        self.assertIn("[brought 'chrome' window to the foreground]", result)

    @patch("actions.executor.time.sleep", return_value=None)
    @patch("actions.executor.platform.system", return_value="Windows")
    def test_open_url_omits_bracket_when_nothing_confirmed(self, mock_system, mock_sleep):
        with patch.object(self.executor, "focus_window", return_value="no window found matching 'x'"):
            result = self.executor.open_url("weather today")
        self.assertNotIn("[", result)


if __name__ == "__main__":
    unittest.main()
