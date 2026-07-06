"""
Safety guardrails: high-risk action detection, terminal confirmation
prompts, and a global kill switch.

Design note: both the kill switch and the confirmation prompts need to read
from stdin. Rather than having two different pieces of code call input()
independently (which would race for the same stream), a single background
thread (StdinListener) is the only thing that ever reads stdin. It pushes
every typed line onto a queue; everything else consumes from that queue.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
from typing import Optional

from config import HIGH_RISK_KEYWORDS, KILL_SWITCH_PHRASE


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
        """
        Non-blocking. Drains any lines typed since the last check. Returns
        True if the kill switch phrase appeared among them. Call this once
        per loop iteration, before executing the next action.
        """
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
    """
    Return the first matching keyword found across the given texts, or None.

    Uses word-boundary matching (\\b...\\b), not a plain substring check --
    a plain "format" in combined check would also match "information",
    "formatted", "reformat", etc. Word boundaries require an actual
    word/non-word transition on both sides, so "format" only matches when
    it appears as its own word.
    """
    combined = " ".join(t for t in texts if t).lower()
    for kw in HIGH_RISK_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined):
            return kw
    return None


def confirm_risky_action(listener: StdinListener, action, matched_keyword: str) -> bool:
    """
    Block and print a confirmation prompt for a high-risk action. Returns
    True if the user approved it. Raises KillSwitch if the user types the
    kill switch phrase instead of answering.
    """
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
