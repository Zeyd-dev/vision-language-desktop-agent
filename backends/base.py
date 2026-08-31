"""Model-agnostic interface for the perceive+decide step of the agent loop."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


VALID_ACTIONS = {
    "click",
    "double_click",
    "type",
    "key",
    "scroll",
    "wait",
    "open_url",
    "launch_app",
    "focus_window",
    "done",
    "fail",
}


@dataclass
class AgentAction:
    """A single decision returned by the VLM for one loop iteration."""

    reasoning: str
    action: str
    coordinates: Optional[list] = None
    text: Optional[str] = None
    key: Optional[str] = None
    scroll_amount: Optional[int] = None
    done_summary: Optional[str] = None
    fail_reason: Optional[str] = None
    expected_outcome: Optional[str] = None
    expectation_met: Optional[bool] = None
    target_hint: Optional[str] = None
    risk_level: Optional[str] = None
    raw_response: Optional[dict] = field(default=None, repr=False)

    def validate(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{self.action}'. Must be one of {sorted(VALID_ACTIONS)}"
            )
        if self.action in ("click", "double_click") and not self.coordinates:
            raise ValueError(f"Action '{self.action}' requires 'coordinates'")
        if self.action == "type" and not self.text:
            raise ValueError("Action 'type' requires 'text'")
        if self.action == "key" and not self.key:
            raise ValueError("Action 'key' requires 'key'")
        if self.action == "open_url" and not self.text:
            raise ValueError("Action 'open_url' requires 'text' (a URL or search query)")
        if self.action == "launch_app" and not self.text:
            raise ValueError("Action 'launch_app' requires 'text' (an application name)")
        if self.action == "focus_window" and not self.text:
            raise ValueError("Action 'focus_window' requires 'text' (a substring of the window's title)")
        if self.action == "done" and not self.done_summary:
            raise ValueError("Action 'done' requires 'done_summary'")
        if self.action == "fail" and not self.fail_reason:
            raise ValueError("Action 'fail' requires 'fail_reason'")

        if self.risk_level is not None:
            normalized = self.risk_level.strip().lower()
            self.risk_level = normalized if normalized in ("low", "medium", "high") else None


class VLMBackend(ABC):
    """Abstract interface for a vision-language decision engine."""

    @abstractmethod
    def decide(
        self,
        task: str,
        screenshot_bytes: bytes,
        history: list[dict],
        screen_size: tuple[int, int],
    ) -> AgentAction:
        """Given the task, current screenshot, and condensed history, return the single next AgentAction."""
        raise NotImplementedError
