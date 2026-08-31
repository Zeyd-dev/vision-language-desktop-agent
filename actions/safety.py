"""Safety guardrails: high-risk action detection, terminal confirmation."""
from __future__ import annotations

import queue
import re
import sys
import threading
from typing import Optional

from config import (
    HIGH_RISK_KEY_COMBOS,
    HIGH_RISK_KEYWORDS,
    KILL_SWITCH_PHRASE,
    RETROSPECTIVE_MARKERS,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


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


def _is_retrospective(sentence: str) -> bool:
    """True if a sentence reads as narrating a past attempt rather than a current action."""
    lowered = sentence.lower()
    return any(marker in lowered for marker in RETROSPECTIVE_MARKERS)


def contains_high_risk_keyword(*texts: str) -> Optional[str]:
    """Return the first matching keyword found across the given texts, or None."""
    kept_sentences = []
    for text in texts:
        if not text:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            if sentence and not _is_retrospective(sentence):
                kept_sentences.append(sentence)
    combined = " ".join(kept_sentences).lower()
    for kw in HIGH_RISK_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined):
            return kw
    return None


def is_self_reported_high_risk(risk_level: Optional[str]) -> bool:
    """True if the model's own structured self-assessment says this action is high-risk."""
    return (risk_level or "").strip().lower() == "high"


def is_high_risk_key_combo(key: Optional[str]) -> Optional[str]:
    """Return the matched combo if a "key" action's key is inherently risky, or None."""
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
