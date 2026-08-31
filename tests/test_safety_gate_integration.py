"""
End-to-end proof that the new risk_level signal actually reaches the
confirmation gate inside run_agent_loop -- not just that
is_self_reported_high_risk() returns the right bool in isolation (see
test_safety.py), but that a real action carrying risk_level="high" and
NO keyword-matching reasoning still triggers callbacks.confirm(), and that
declining it skips execution instead of running the action anyway.

This is the literal scenario that motivated adding risk_level: a real run
described sending an email using only "compose"/"submit", never "send",
and slipped through unconfirmed under the old keyword-only check.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from PIL import Image  # noqa: E402

from actions.executor import Screenshot  # noqa: E402
from backends.base import AgentAction  # noqa: E402
from core.loop import LoopCallbacks, run_agent_loop  # noqa: E402


def _screenshot():
    img = Image.new("RGB", (400, 300), (10, 10, 10))
    return Screenshot(full_image=img, api_bytes=b"", real_size=img.size, resized_size=img.size, scale=1.0)


class _FakeBackend:
    def __init__(self, actions):
        self._actions = list(actions)

    def decide(self, task, screenshot_bytes, history, screen_size):
        return self._actions.pop(0)


class TestRiskLevelReachesConfirmation(unittest.TestCase):
    def test_high_risk_level_with_no_trigger_word_still_confirms(self):
        # Deliberately avoids every word in HIGH_RISK_KEYWORDS ("send",
        # "submit" isn't even in the list either, by design -- this
        # reasoning text alone would sail through contains_high_risk_keyword
        # unconfirmed under the old check.
        risky_action = AgentAction(
            reasoning="I will compose and finalize this message to complete the task",
            action="click",
            coordinates=[50, 50],
            risk_level="high",
        )
        done_action = AgentAction(reasoning="wrapping up", action="done", done_summary="ok", risk_level="low")

        backend = _FakeBackend([risky_action, done_action])
        confirm_calls = []

        def confirm(action, matched_keyword):
            confirm_calls.append(matched_keyword)
            return False  # decline -- the action must NOT execute

        callbacks = LoopCallbacks(
            log=lambda msg: None,
            confirm=confirm,
            should_stop=lambda step: None,
        )

        with tempfile.TemporaryDirectory() as tmp_runs_dir, \
                patch("core.loop.capture_screenshot", return_value=_screenshot()), \
                patch("core.loop.get_backend", return_value=backend), \
                patch("run_logger.RUNS_DIR", tmp_runs_dir):
            status, detail, run_dir = run_agent_loop(
                task="reply to the email",
                backend_name="fake",
                max_iterations=10,
                max_minutes=5,
                callbacks=callbacks,
            )

        # The confirmation gate must have fired exactly once, for the
        # risky click -- and specifically because of risk_level, since the
        # reasoning text alone contains none of the listed keywords.
        self.assertEqual(len(confirm_calls), 1)
        self.assertIn("risk_level", confirm_calls[0])
        self.assertEqual(status, "done")  # loop continued past the declined action to "done"


if __name__ == "__main__":
    unittest.main()
