import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from actions.safety import (  # noqa: E402
    contains_high_risk_keyword,
    is_high_risk_key_combo,
    is_self_reported_high_risk,
)


class TestContainsHighRiskKeyword(unittest.TestCase):
    def test_matches_whole_word(self):
        self.assertEqual(contains_high_risk_keyword("I will send the email now"), "send")

    def test_word_boundary_not_substring(self):
        # "format" must not match inside a word like "information".
        self.assertIsNone(contains_high_risk_keyword("reading some information"))

    def test_no_match_returns_none(self):
        self.assertIsNone(contains_high_risk_keyword("clicking the compose button"))

    def test_case_insensitive(self):
        self.assertEqual(contains_high_risk_keyword("I will DELETE this"), "delete")

    def test_checks_multiple_texts(self):
        self.assertEqual(contains_high_risk_keyword("looking around", "", "purchase this item"), "purchase")

    def test_real_run_gap_compose_wording_not_caught(self):
        # Documents the exact real gap risk_level (see below) was added to
        # close -- not a bug in this function, just a reminder of its limit.
        self.assertIsNone(contains_high_risk_keyword(
            "I will compose and submit this message to complete the task"
        ))

    def test_retrospective_mention_does_not_flag_unrelated_action(self):
        # The confirmation-fatigue fix: a past-tense reference to a prior
        # attempt shouldn't flag THIS action just because it shares a word.
        self.assertIsNone(contains_high_risk_keyword(
            "My previous attempt to click 'Send' did not seem to work. "
            "I will retype the email body instead."
        ))

    def test_current_action_mention_still_flags(self):
        self.assertEqual(contains_high_risk_keyword(
            "I can see a 'Send' button. I will now click it to send the email."
        ), "send")

    def test_known_limitation_mixed_clause_can_still_be_missed(self):
        # Honest limit of the sentence-level heuristic: a single sentence
        # that mixes a past-tense reference with a genuinely current risky
        # action can still slip through, since the whole sentence gets
        # excluded once a retrospective marker appears anywhere in it.
        # risk_level (checked independently in core/loop.py) is the
        # intended backstop for exactly this case.
        missed = contains_high_risk_keyword(
            "Since deleting it last time failed, I will delete it again now."
        )
        self.assertIsNone(missed)  # documents the gap, not a passing assertion of correctness


class TestIsHighRiskKeyCombo(unittest.TestCase):
    def test_known_combo(self):
        self.assertEqual(is_high_risk_key_combo("alt+f4"), "alt+f4")

    def test_normalizes_spacing_and_case(self):
        self.assertEqual(is_high_risk_key_combo(" Alt + F4 "), "alt+f4")

    def test_safe_combo_returns_none(self):
        self.assertIsNone(is_high_risk_key_combo("ctrl+l"))

    def test_none_key_returns_none(self):
        self.assertIsNone(is_high_risk_key_combo(None))


class TestIsSelfReportedHighRisk(unittest.TestCase):
    def test_high_is_true(self):
        self.assertTrue(is_self_reported_high_risk("high"))

    def test_case_insensitive(self):
        self.assertTrue(is_self_reported_high_risk("HIGH"))

    def test_low_and_medium_are_false(self):
        self.assertFalse(is_self_reported_high_risk("low"))
        self.assertFalse(is_self_reported_high_risk("medium"))

    def test_none_is_false(self):
        self.assertFalse(is_self_reported_high_risk(None))

    def test_closes_the_compose_wording_gap(self):
        # The exact scenario contains_high_risk_keyword misses (above) IS
        # caught here, as long as the model set risk_level honestly.
        reasoning = "I will compose and submit this message to complete the task"
        self.assertIsNone(contains_high_risk_keyword(reasoning))
        self.assertTrue(is_self_reported_high_risk("high"))


if __name__ == "__main__":
    unittest.main()
