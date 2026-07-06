"""
Model-agnostic interface for the perceive+decide step of the agent loop.

The loop controller only ever talks to a `VLMBackend`. To swap Claude for
another model later, implement this interface (see `ClaudeBackend` for a
reference implementation) and pass an instance into `Agent(backend=...)`.
"""
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
    "done",
    "fail",
}


@dataclass
class AgentAction:
    """
    A single decision returned by the VLM for one loop iteration.

    Mirrors the JSON action schema from the project spec, plus one
    forward-looking field (`expected_outcome`) that isn't required for the
    MVP but exists so a future Plan -> Act -> Reflect cycle can compare what
    the model expected to happen against the next screenshot, without
    changing the schema again.
    """

    reasoning: str
    action: str
    coordinates: Optional[list] = None
    text: Optional[str] = None
    key: Optional[str] = None
    scroll_amount: Optional[int] = None
    done_summary: Optional[str] = None
    fail_reason: Optional[str] = None
    # Forward-looking / optional: what the model expects to see after this
    # action executes. Used by the (future) reflect step; safe to ignore now.
    expected_outcome: Optional[str] = None
    # Populated by the loop controller after parsing, not by the model.
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
        if self.action == "done" and not self.done_summary:
            raise ValueError("Action 'done' requires 'done_summary'")
        if self.action == "fail" and not self.fail_reason:
            raise ValueError("Action 'fail' requires 'fail_reason'")


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
        """
        Given the task, the current screenshot (encoded image bytes, e.g.
        JPEG/PNG), a condensed history of prior steps, and the real screen
        resolution, return the single next AgentAction to execute.

        Implementations are responsible for calling the underlying model,
        enforcing the structured-output schema, and raising a clear
        exception if the response can't be parsed into a valid AgentAction.
        """
        raise NotImplementedError
