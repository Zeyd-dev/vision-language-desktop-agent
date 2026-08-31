"""
Regression test built directly from a real failed run's log (2026-07-31,
"send an email" task). Steps 1-9 of that run are omitted here since they
never matched a keyword either way; steps 10-17 are the ones that mattered:
step 10 is the model's first real mention of sending, and every step after
it -- typing body text, clicking a close button, clicking Compose again --
also matched 'send' under the OLD contains_high_risk_keyword(), purely
because the model's reasoning kept narrating "clicking 'Send' didn't work"
while explaining its retries. That produced 7 straight unnecessary
confirmations in a row before the run was abandoned.

This test replays the exact reasoning strings from that log and asserts
the fixed behavior: the real send mentions (steps 10 and 14, where the
CURRENT action is actually a click on the Send button) still match, and
the narrated-history steps around them do not.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from actions.safety import contains_high_risk_keyword  # noqa: E402

# (step, reasoning, expected match: True if this step's CURRENT action is a
# genuine send and should still be flagged, False if it's unrelated and
# should no longer be flagged after the fix)
_REAL_RUN_STEPS = [
    (10, "I have successfully entered the recipient's email, the subject, and the body of the email. "
         "Now I need to send the email. I can see a 'Send' button at the bottom of the compose window.",
     True),  # genuine send click
    (11, "My previous attempt to type the email body resulted in 'screen_changed=False', and then "
         "clicking 'Send' did not seem to work. It appears the email body was not correctly entered.",
     False),  # this step just retypes body text
    (12, "The previous attempts to type the email body resulted in 'screen_changed=False' and the text "
         "in the body is incomplete. My last attempt to click 'Send' also didn't close the compose window.",
     False),  # clicking the body field to refocus it
    (13, "Previous attempts to type the email body and click send have failed. The current screenshot "
         "shows the email body is partially filled.",
     False),  # retyping body text
    (14, "The 'To' field and 'Subject' field are filled. My last attempt to click the send button was "
         "not actually on the send button. I will now click the 'Send' button to send the email.",
     True),  # genuine send click, second attempt
    (15, "Previous attempts to type the email body and click 'Send' have consistently resulted in "
         "'screen_changed=False'.",
     False),  # retyping body text
    (16, "Previous attempts to type the email body and click the 'Send' button have consistently "
         "resulted in 'screen_changed=False' for the last three actions, indicating the compose window "
         "is unresponsive. I will close the current unresponsive compose window by clicking its close "
         "button.",
     False),  # closing the window, not sending
    (17, "The previous actions to type an email body and send have failed repeatedly with "
         "'screen_changed=False', and the current screenshot shows I am viewing an existing email, not "
         "composing a new one.",
     False),  # clicking Compose to start over
]


class TestRealRunRegressionNoConfirmationCascade(unittest.TestCase):
    def test_only_genuine_send_steps_still_flagged(self):
        false_positives = []
        false_negatives = []
        for step, reasoning, should_match in _REAL_RUN_STEPS:
            matched = contains_high_risk_keyword(reasoning) is not None
            if should_match and not matched:
                false_negatives.append(step)
            if not should_match and matched:
                false_positives.append(step)

        self.assertEqual(false_positives, [], f"steps still incorrectly flagged: {false_positives}")
        self.assertEqual(false_negatives, [], f"genuine send steps no longer caught: {false_negatives}")

    def test_confirmation_count_drops_from_eight_to_two(self):
        # The original cascade: steps 10-17 (8 steps) all matched. After
        # the fix, only the 2 genuine sends (10, 14) should.
        matched_steps = [s for s, r, _ in _REAL_RUN_STEPS if contains_high_risk_keyword(r) is not None]
        self.assertEqual(matched_steps, [10, 14])


if __name__ == "__main__":
    unittest.main()
