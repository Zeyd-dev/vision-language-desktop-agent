import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from backends.base import AgentAction  # noqa: E402
from core.loop import _find_stuck_repeat, _stuck_repeat_nudge, _target_key  # noqa: E402


def _focus_action(name):
    return AgentAction(reasoning="x", action="focus_window", text=name, risk_level="low")


def _click_action(coords=(100, 100)):
    return AgentAction(reasoning="x", action="click", coordinates=list(coords), risk_level="low")


class TestTargetKey(unittest.TestCase):
    def test_focus_window_key_is_name(self):
        a = _focus_action("Untitled")
        self.assertEqual(_target_key(a, None), ("focus_window", "untitled"))

    def test_focus_window_key_case_insensitive(self):
        self.assertEqual(
            _target_key(_focus_action("UNTITLED"), None),
            _target_key(_focus_action("untitled"), None),
        )

    def test_click_key_rounds_nearby_pixels_together(self):
        a = _click_action()
        key1 = _target_key(a, (401, 398))
        key2 = _target_key(a, (410, 405))
        self.assertEqual(key1, key2)

    def test_click_key_differs_for_far_apart_clicks(self):
        a = _click_action()
        key1 = _target_key(a, (100, 100))
        key2 = _target_key(a, (900, 900))
        self.assertNotEqual(key1, key2)

    def test_click_without_xy_is_none(self):
        a = _click_action()
        self.assertIsNone(_target_key(a, None))

    def test_scroll_has_no_target_key(self):
        a = AgentAction(reasoning="x", action="scroll", risk_level="low")
        self.assertIsNone(_target_key(a, None))


class TestFindStuckRepeat(unittest.TestCase):
    def test_two_failed_focus_window_repeats_detected(self):
        history = [
            {"action": "focus_window", "target_key": ("focus_window", "untitled"), "screen_changed": False},
            {"action": "focus_window", "target_key": ("focus_window", "untitled"), "screen_changed": False},
        ]
        self.assertIsNotNone(_find_stuck_repeat(history))

    def test_different_targets_not_flagged(self):
        history = [
            {"action": "focus_window", "target_key": ("focus_window", "untitled"), "screen_changed": False},
            {"action": "focus_window", "target_key": ("focus_window", "notepad"), "screen_changed": False},
        ]
        self.assertIsNone(_find_stuck_repeat(history))

    def test_one_success_breaks_the_repeat(self):
        history = [
            {"action": "focus_window", "target_key": ("focus_window", "untitled"), "screen_changed": False},
            {"action": "focus_window", "target_key": ("focus_window", "untitled"), "screen_changed": True},
        ]
        self.assertIsNone(_find_stuck_repeat(history))

    def test_expectation_met_false_also_counts_as_failed(self):
        # Screen technically changed, but not into what was expected -- the
        # exact gap screen_changed alone can never catch.
        history = [
            {"action": "click", "target_key": ("click", 100, 100), "screen_changed": True, "expectation_met": False},
            {"action": "click", "target_key": ("click", 100, 100), "screen_changed": True, "expectation_met": False},
        ]
        self.assertIsNotNone(_find_stuck_repeat(history))

    def test_needs_at_least_two_entries(self):
        history = [{"action": "click", "target_key": ("click", 100, 100), "screen_changed": False}]
        self.assertIsNone(_find_stuck_repeat(history))

    def test_none_target_key_never_matches(self):
        history = [
            {"action": "scroll", "target_key": None, "screen_changed": False},
            {"action": "scroll", "target_key": None, "screen_changed": False},
        ]
        self.assertIsNone(_find_stuck_repeat(history))


class TestStuckRepeatNudge(unittest.TestCase):
    def test_focus_window_nudge_mentions_clicking_directly(self):
        entry = {"action": "focus_window", "target_key": ("focus_window", "untitled")}
        msg = _stuck_repeat_nudge(entry)
        self.assertIn("click directly", msg.lower())
        self.assertIn("untitled", msg.lower())

    def test_click_nudge_does_not_suggest_open_url(self):
        # Regression guard for the actual gap found in a real run: the old
        # generic nudge only ever suggested open_url/launch_app, which is
        # nonsensical advice for a failing click.
        entry = {"action": "click", "target_key": ("click", 100, 100)}
        msg = _stuck_repeat_nudge(entry)
        self.assertNotIn("open_url", msg)


if __name__ == "__main__":
    unittest.main()
