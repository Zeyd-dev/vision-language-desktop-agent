"""Gemini implementation of VLMBackend."""
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


_TRANSIENT_ERROR_HINTS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded")
_DAILY_QUOTA_HINT = "PerDay"
_MAX_RETRIES_PER_KEY = 3
_RETRY_BACKOFF_SECONDS = 2


class _AgentActionSchema(BaseModel):
    """Mirrors AgentAction as a Pydantic model -- the Gemini equivalent of Claude's ACTION_TOOL dict."""

    reasoning: str
    action: str
    coordinates: Optional[List[int]] = None
    text: Optional[str] = None
    key: Optional[str] = None
    scroll_amount: Optional[int] = None
    done_summary: Optional[str] = None
    fail_reason: Optional[str] = None
    expected_outcome: Optional[str] = None
    expectation_met: Optional[bool] = None
    target_hint: Optional[str] = None
    risk_level: Optional[str] = None


class GeminiBackend(VLMBackend):
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
        width, height = screen_size

        prompt_text = (
            f"TASK: {task}\n\n"
            f"SCREENSHOT SIZE: {width}x{height} pixels. Your \"coordinates\" MUST be "
            f"within 0-{width} horizontally and 0-{height} vertically -- any value "
            f"outside that range will be rejected and the action skipped, wasting a "
            f"step. Do not estimate coordinates from a guess about typical screen "
            f"layouts; read them off this exact image.\n\n"
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
            expectation_met=data.get("expectation_met"),
            target_hint=data.get("target_hint"),
            risk_level=data.get("risk_level"),
            raw_response=data,
        )
        action.validate()
        return action

    def _wait_for_rate_limit(self) -> None:
        """Sleep just enough to keep calls at least _MIN_SECONDS_BETWEEN_CALLS apart."""
        if self._last_call_time is not None:
            elapsed = time.monotonic() - self._last_call_time
            remaining = self._MIN_SECONDS_BETWEEN_CALLS - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_time = time.monotonic()

    def _rotate_key(self) -> bool:
        """Switch to the next API key in the pool. False if none remain."""
        if self._key_index + 1 >= len(self._api_keys):
            return False
        self._key_index += 1
        self.client = genai.Client(api_key=self._api_keys[self._key_index])
        self._last_call_time = None
        return True

    def _generate_with_retry(self, prompt_text: str, screenshot_bytes: bytes):
        """Retry transient errors on the same key; rotate keys on a daily-quota hit; fail fast otherwise."""
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
                except Exception as e:
                    last_error = e
                    if not any(hint in str(e) for hint in _TRANSIENT_ERROR_HINTS):
                        raise
                    if _DAILY_QUOTA_HINT in str(e):
                        break
                    if attempt < _MAX_RETRIES_PER_KEY:
                        time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
            else:
                raise last_error

            if not self._rotate_key():
                raise RuntimeError(
                    f"All {len(self._api_keys)} configured Gemini API key(s) have hit "
                    f"their daily free-tier quota. Add more keys to GEMINI_API_KEYS, "
                    f"switch to --backend claude, or wait for the daily reset. "
                    f"Last error: {last_error}"
                ) from last_error

    @staticmethod
    def _parse_response(response) -> dict:
        """Prefer the SDK's parsed Pydantic object; fall back to parsing response.text as JSON."""
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
            if step.get("expected_outcome"):
                line += f" expected=\"{step['expected_outcome'][:100]}\""
            if step.get("expectation_met") is not None:
                line += f" expectation_met={step['expectation_met']}"
            lines.append(line)
        return "\n".join(lines)
