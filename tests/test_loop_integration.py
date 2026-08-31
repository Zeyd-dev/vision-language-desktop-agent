"""
End-to-end test of run_agent_loop() itself, not just the helper functions in
isolation -- proves the wiring actually connects: screenshot backfill ->
target_key -> _find_stuck_repeat -> nudge text -> the exact string handed to
the backend's decide() call. Only two things are faked: the backend (so no
real API call happens) and the screenshot source (so no real screen is
needed) -- execute_action(), the screen-diff backfill, and the stuck-repeat
detector all run for real.

Scenario: focus_window on the same window fails twice with no visible
change (exactly the real "open Notepad" run that motivated this), then a
click succeeds, then the task reports done. Asserts the nudge appears on
exactly the call right after the second failure, and nowhere else.
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


def _screenshot(image):
    return Screenshot(
        full_image=image,
        api_bytes=b"",
        real_size=image.size,
        resized_size=image.size,
        scale=1.0,
    )


class _FakeBackend:
    def __init__(self, actions):
        self._actions = list(actions)
        self.calls = []  # list of (task_arg) for each decide() call

    def decide(self, task, screenshot_bytes, history, screen_size):
        self.calls.append(task)
        return self._actions.pop(0)


class TestStuckRepeatIntegration(unittest.TestCase):
    def test_nudge_appears_exactly_once_right_after_second_failure(self):
        img_same = Image.new("RGB", (400, 300), (10, 10, 10))
        img_changed = Image.new("RGB", (400, 300), (240, 240, 240))

        # 5 capture_screenshot calls total across this scenario:
        #  step1 top, step2 top, step3 top, step3's click staleness-recheck, step4 top
        screenshots = [
            _screenshot(img_same),   # step1 top
            _screenshot(img_same),   # step2 top (unchanged vs step1 -> entry1 screen_changed=False)
            _screenshot(img_same),   # step3 top (unchanged vs step2 -> entry2 screen_changed=False)
            _screenshot(img_same),   # click staleness recheck (matches step3 top -> not stale)
            _screenshot(img_changed),  # step4 top (changed vs step3 top -> entry3 screen_changed=True)
        ]

        actions = [
            AgentAction(reasoning="try focus_window", action="focus_window", text="untitled", risk_level="low"),
            AgentAction(reasoning="try focus_window again", action="focus_window", text="untitled", risk_level="low"),
            AgentAction(reasoning="click directly instead", action="click", coordinates=[100, 100], risk_level="low"),
            AgentAction(reasoning="all done", action="done", done_summary="Task complete", risk_level="low"),
        ]

        backend = _FakeBackend(actions)
        callbacks = LoopCallbacks(
            log=lambda msg: None,
            confirm=lambda action, keyword: True,
            should_stop=lambda step: None,
        )

        with tempfile.TemporaryDirectory() as tmp_runs_dir, \
                patch("core.loop.capture_screenshot", side_effect=screenshots), \
                patch("core.loop.get_backend", return_value=backend), \
                patch("run_logger.RUNS_DIR", tmp_runs_dir):
            status, detail, run_dir = run_agent_loop(
                task="open Notepad and type a note",
                backend_name="fake",
                max_iterations=10,
                max_minutes=5,
                callbacks=callbacks,
            )

        self.assertEqual(status, "done")
        self.assertEqual(len(backend.calls), 4)

        # Calls 1 and 2 (indices 0, 1): not enough history yet for a repeat
        # to exist -- no nudge.
        self.assertNotIn("[SYSTEM NOTE]", backend.calls[0])
        self.assertNotIn("[SYSTEM NOTE]", backend.calls[1])

        # Call 3 (index 2): both prior focus_window attempts are now known
        # to have produced no visible change -- the nudge must appear here,
        # and must be specific to focus_window, not the generic message.
        self.assertIn("[SYSTEM NOTE]", backend.calls[2])
        self.assertIn("focus_window", backend.calls[2])
        self.assertIn("untitled", backend.calls[2])
        self.assertIn("click directly", backend.calls[2])
        self.assertNotIn("open_url", backend.calls[2])  # regression guard, see test_stuck_detection.py

        # Call 4 (index 3): the click worked (screen changed) -- the streak
        # is broken, no nudge should carry over.
        self.assertNotIn("[SYSTEM NOTE]", backend.calls[3])


if __name__ == "__main__":
    unittest.main()
