import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from backends.base import AgentAction  # noqa: E402


class TestAgentActionValidate(unittest.TestCase):
    def test_click_requires_coordinates(self):
        a = AgentAction(reasoning="x", action="click", coordinates=None)
        with self.assertRaises(ValueError):
            a.validate()

    def test_click_with_coordinates_ok(self):
        a = AgentAction(reasoning="x", action="click", coordinates=[10, 20], risk_level="low")
        a.validate()  # should not raise

    def test_type_requires_text(self):
        a = AgentAction(reasoning="x", action="type", text=None)
        with self.assertRaises(ValueError):
            a.validate()

    def test_focus_window_requires_text(self):
        a = AgentAction(reasoning="x", action="focus_window", text=None)
        with self.assertRaises(ValueError):
            a.validate()

    def test_done_requires_summary(self):
        a = AgentAction(reasoning="x", action="done", done_summary=None)
        with self.assertRaises(ValueError):
            a.validate()

    def test_fail_requires_reason(self):
        a = AgentAction(reasoning="x", action="fail", fail_reason=None)
        with self.assertRaises(ValueError):
            a.validate()

    def test_invalid_action_name_rejected(self):
        a = AgentAction(reasoning="x", action="teleport")
        with self.assertRaises(ValueError):
            a.validate()

    def test_risk_level_normalized_lowercase(self):
        a = AgentAction(reasoning="x", action="wait", risk_level="HIGH")
        a.validate()
        self.assertEqual(a.risk_level, "high")

    def test_risk_level_garbage_normalized_to_none(self):
        a = AgentAction(reasoning="x", action="wait", risk_level="super duper risky")
        a.validate()
        self.assertIsNone(a.risk_level)

    def test_risk_level_none_stays_none(self):
        a = AgentAction(reasoning="x", action="wait", risk_level=None)
        a.validate()
        self.assertIsNone(a.risk_level)

    def test_new_optional_fields_default_none(self):
        a = AgentAction(reasoning="x", action="wait")
        self.assertIsNone(a.expectation_met)
        self.assertIsNone(a.target_hint)
        self.assertIsNone(a.risk_level)


if __name__ == "__main__":
    unittest.main()
