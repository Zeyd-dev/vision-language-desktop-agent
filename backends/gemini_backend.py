"""
Gemini implementation of VLMBackend.

Free-tier alternative to ClaudeBackend. Uses Gemini's structured-output
feature (response_schema) to force the model's answer into the same
AgentAction shape Claude gives us via tool-use -- different mechanism on
Google's side, same guarantee we rely on: the API enforces the shape, we
never parse free text and hope.

Deliberately uses a general-purpose Gemini model (not the browser-only
"Computer Use" preview model) so it reuses our own action schema and
system prompt unmodified, and stays usable on any desktop app instead of
being scoped to browser control only.

Supports a POOL of API keys (config.GEMINI_API_KEYS). Each free-tier
Google Cloud project has its own independent daily quota, so when the
current key's daily cap is hit, this backend rotates to the next key
automatically instead of just failing the run.
"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from .base import AgentAction, VLMBackend
from .prompts import SYSTEM_PROMPT
from config import GEMINI_API_KEYS, GEMINI_MODEL


# Substrings that show up in Gemini's error messages for *temporary*
# problems (server overload, rate limiting) as opposed to real bugs (bad
# API key, malformed request). Only these get retried at all.
_TRANSIENT_ERROR_HINTS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded")
# Google's own quotaId naming (e.g. "GenerateRequestsPerDayPerProjectPerModel-
# FreeTier") tells us WHICH kind of limit was hit. A per-day exhaustion means
# waiting a few seconds is pointless -- only rotating to a different key
# helps. Anything else transient (per-minute limits, server overload) is
# worth a short backoff-and-retry on the SAME key instead.
_DAILY_QUOTA_HINT = "PerDay"
_MAX_RETRIES_PER_KEY = 3
_RETRY_BACKOFF_SECONDS = 2  # doubles each retry: 2s, 4s, 8s


class _AgentActionSchema(BaseModel):
    """
    Mirrors AgentAction's fields, in the shape google-genai's response_schema
    expects (a Pydantic model). This is the Gemini equivalent of Claude's
    ACTION_TOOL dict in claude_backend.py -- same job, different API.
    """

    reasoning: str
    action: str
    coordinates: Optional[List[int]] = None
    text: Optional[str] = None
    key: Optional[str] = None
    scroll_amount: Optional[int] = None
    done_summary: Optional[str] = None
    fail_reason: Optional[str] = None
    expected_outcome: Optional[str] = None


class GeminiBackend(VLMBackend):
    # Free tier for gemini-2.5-flash allows 5 requests/minute. Spacing calls
    # at least this far apart means we approach the limit deliberately
    # instead of hitting it after the fact and relying on retries to save
    # us -- avoiding the wall beats recovering from it.
    _MIN_SECONDS_BETWEEN_CALLS = 13

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        model: str | None = None,
    ):
        if api_keys:
            keys = list(api_keys)
        elif api_key:
            keys = [api_key]
        else:
            keys = GEMINI_API_KEYS
        if not keys:
            raise RuntimeError(
                "No Gemini API key found. Set GEMINI_API_KEY (one key) or "
                "GEMINI_API_KEYS (comma-separated, for automatic fallback) in "
                "your environment or a .env file (see .env.example)."
            )
        self._api_keys = keys
        self._key_index = 0
        self.client = genai.Client(api_key=self._api_keys[0])
        self.model = model or GEMINI_MODEL
        self._last_call_time: float | None = None

    def decide(
        self,
        task: str,
        screenshot_bytes: bytes,
        history: list[dict],
        screen_size: tuple[int, int],
    ) -> AgentAction:
        history_text = self._render_history(history)

        prompt_text = (
            f"TASK: {task}\n\n"
            f"HISTORY OF PRIOR STEPS (most recent last):\n{history_text}\n\n"
            "Here is the current screenshot. Decide the single next action."
        )

        response = self._generate_with_retry(prompt_text, screenshot_bytes)

        data = self._parse_response(response)
        action = AgentAction(
            reasoning=data.get("reasoning", ""),
            action=data.get("action", ""),
            coordinates=data.get("coordinates"),
            text=data.get("text"),
            key=data.get("key"),
            scroll_amount=data.get("scroll_amount"),
            done_summary=data.get("done_summary"),
            fail_reason=data.get("fail_reason"),
            expected_outcome=data.get("expected_outcome"),
            raw_response=data,
        )
        action.validate()
        return action

    def _wait_for_rate_limit(self) -> None:
        """
        Blocks just long enough to keep at least _MIN_SECONDS_BETWEEN_CALLS
        between requests, so the free tier's per-minute quota is approached
        deliberately instead of tripped and then recovered from.
        """
        if self._last_call_time is not None:
            elapsed = time.monotonic() - self._last_call_time
            remaining = self._MIN_SECONDS_BETWEEN_CALLS - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_time = time.monotonic()

    def _rotate_key(self) -> bool:
        """
        Switch to the next API key in the pool. Returns False if there isn't
        one (caller should give up at that point, not loop forever).
        """
        if self._key_index + 1 >= len(self._api_keys):
            return False
        self._key_index += 1
        self.client = genai.Client(api_key=self._api_keys[self._key_index])
        self._last_call_time = None  # fresh key has no shared pacing history
        return True

    def _generate_with_retry(self, prompt_text: str, screenshot_bytes: bytes):
        """
        Calls the Gemini API. Two different failure responses depending on
        WHAT kind of transient error comes back:
          - per-minute limit / server overload -> short backoff, retry the
            SAME key (waiting actually helps here)
          - per-day quota exhausted -> waiting is pointless, rotate to the
            next configured key instead and retry immediately
        A non-transient error (bad key, malformed request) is raised right
        away in either case -- retrying or rotating won't fix a real bug.
        """
        last_error: Exception | None = None
        while True:
            for attempt in range(_MAX_RETRIES_PER_KEY + 1):
                self._wait_for_rate_limit()
                try:
                    return self.client.models.generate_content(
                        model=self.model,
                        contents=[
                            prompt_text,
                            types.Part.from_bytes(data=screenshot_bytes, mime_type="image/jpeg"),
                        ],
                        config={
                            "system_instruction": SYSTEM_PROMPT,
                            "response_mime_type": "application/json",
                            "response_schema": _AgentActionSchema,
                        },
                    )
                except Exception as e:  # noqa: BLE001 - inspecting message text, not a specific SDK exception type
                    last_error = e
                    if not any(hint in str(e) for hint in _TRANSIENT_ERROR_HINTS):
                        raise  # not transient at all -- fail fast, don't waste retries on a real bug
                    if _DAILY_QUOTA_HINT in str(e):
                        break  # stop retrying THIS key; try rotating below instead
                    if attempt < _MAX_RETRIES_PER_KEY:
                        time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
            else:
                # exhausted per-key retries without ever hitting a daily-quota break
                raise last_error

            # only reached via the daily-quota `break` above
            if not self._rotate_key():
                raise RuntimeError(
                    f"All {len(self._api_keys)} configured Gemini API key(s) have hit "
                    f"their daily free-tier quota. Add more keys to GEMINI_API_KEYS, "
                    f"switch to --backend claude, or wait for the daily reset. "
                    f"Last error: {last_error}"
                ) from last_error

    @staticmethod
    def _parse_response(response) -> dict:
        """
        Prefer the SDK's already-parsed Pydantic object (response.parsed);
        fall back to parsing response.text as JSON if that's not populated.
        Keeps this working across minor SDK version differences instead of
        depending on one exact attribute always being set.
        """
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed.model_dump()
        return json.loads(response.text)

    @staticmethod
    def _render_history(history: list[dict]) -> str:
        if not history:
            return "(no prior steps yet -- this is the first action)"
        lines = []
        for i, step in enumerate(history, start=1):
            line = (
                f"{i}. action={step.get('action')} "
                f"reasoning=\"{step.get('reasoning', '')[:160]}\" "
                f"outcome={step.get('outcome', 'unknown')}"
            )
            if "screen_changed" in step:
                line += f" screen_changed={step['screen_changed']}"
            lines.append(line)
        return "\n".join(lines)
