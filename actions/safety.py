"""
Safety guardrails: high-risk action detection, terminal confirmation
prompts, and a global kill switch.

Design note: both the kill switch and the confirmation prompts need to read
from stdin. A single background thread (StdinListener) is the only thing
that ever reads stdin, pushing every typed line onto a queue that everything
else consumes from -- avoids two independent input() calls racing.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
from typing import Optional

from config import HIGH_RISK_KEY_COMBOS, HIGH_RISK_KEYWORDS, KILL_SWITCH_PHRASE


class KillSwitch(Exception):
    """Raised to unwind the loop immediately when the user triggers the kill switch."""


class StdinListener:
    def __init__(self):
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def _run(self) -> None:
        for line in sys.stdin:
            self._queue.put(line.strip())

    def check_kill_switch(self) -> bool:
        """Non-blocking. True if the kill switch phrase was typed since the last check."""
        triggered = False
        while True:
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                break
            if line.lower() == KILL_SWITCH_PHRASE:
                triggered = True
        return triggered

    def read_line_blocking(self) -> str:
        """Blocking. Used only while a confirmation prompt is on screen."""
        return self._queue.get()


def contains_high_risk_keyword(*texts: str) -> Optional[str]:
    """Return the first matching keyword found across the given texts, or None.

    Uses word-boundary matching so "format" doesn't also match "information"."""
    combined = " ".join(t for t in texts if t).lower()
    for kw in HIGH_RISK_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined):
            return kw
    return None


def is_high_risk_key_combo(key: Optional[str]) -> Optional[str]:
    """Return the matched combo if a "key" action's key is inherently risky, or None.

    This checks what the action IS (e.g. alt+f4 closes the focused window),
    independent of how the model's "reasoning" happens to describe it --
    contains_high_risk_keyword() above only catches risk if the model's own
    wording matches a listed word, and a real run once pressed alt+f4 with
    zero confirmation because its reasoning never used any listed word."""
    if not key:
        return None
    normalized = "+".join(p.strip() for p in key.lower().split("+") if p.strip())
    return normalized if normalized in HIGH_RISK_KEY_COMBOS else None


def confirm_risky_action(listener: StdinListener, action, matched_keyword: str) -> bool:
    """Block on a confirmation prompt. Returns True if approved, raises KillSwitch on the kill phrase."""
    print("\n" + "=" * 60)
    print(f"HIGH-RISK ACTION DETECTED (matched keyword: '{matched_keyword}')")
    print(f"  Action:    {action.action}")
    print(f"  Reasoning: {action.reasoning}")
    if action.coordinates:
        print(f"  Coordinates: {action.coordinates}")
    if action.text:
        print(f"  Text to type: {action.text!r}")
    if action.key:
        print(f"  Key: {action.key}")
    print("=" * 60)
    print(
        f"Type 'y' + Enter to proceed, anything else to skip this action "
        f"(or '{KILL_SWITCH_PHRASE}' + Enter to halt the whole run): ",
        end="",
        flush=True,
    )

    line = listener.read_line_blocking()
    if line.lower() == KILL_SWITCH_PHRASE:
        raise KillSwitch("Kill switch triggered during a confirmation prompt")
    return line.lower() in ("y", "yes")
