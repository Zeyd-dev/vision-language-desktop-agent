"""Claude implementation of VLMBackend."""
from __future__ import annotations

import base64

import anthropic

from .base import AgentAction, VLMBackend
from .prompts import SYSTEM_PROMPT
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS

ACTION_TOOL = {
    "name": "agent_action",
    "description": (
        "Report the single next action the agent should take on the desktop, "
        "along with the reasoning behind it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Short explanation of what you see and why this action.",
            },
            "action": {
                "type": "string",
                "enum": [
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
                ],
            },
            "coordinates": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": (
                    "[x, y] pixel position in the screenshot you were shown. Required "
                    "for click/double_click. Also accepted for 'type' -- if given, that "
                    "spot is clicked first to focus it before typing; include it whenever "
                    "you're targeting a different field than your last action focused."
                ),
            },
            "text": {
                "type": "string",
                "description": (
                    "String to type, if action is 'type'. A URL or search query, if "
                    "action is 'open_url'. An application name, if action is 'launch_app'. "
                    "A substring of an already-open window's title, if action is "
                    "'focus_window' (e.g. 'notepad' matches a window titled "
                    "'Untitled - Notepad')."
                ),
            },
            "key": {
                "type": "string",
                "description": "Key name (or 'ctrl+l' style combo), if action is 'key'.",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "Optional scroll magnitude/direction if action is 'scroll'.",
            },
            "done_summary": {
                "type": "string",
                "description": "What was accomplished, if action is 'done'.",
            },
            "fail_reason": {
                "type": "string",
                "description": "Why the task can't continue, if action is 'fail'.",
            },
            "expected_outcome": {
                "type": "string",
                "description": "One sentence: what you expect to change after this action.",
            },
            "expectation_met": {
                "type": ["boolean", "null"],
                "description": (
                    "Reflect step: if the history's last entry has an 'expected_outcome', "
                    "look at the CURRENT screenshot and judge whether that actually "
                    "happened. true if it matched, false if it didn't (even if the screen "
                    "changed -- changing into the WRONG thing still counts as false), null "
                    "on the very first step when there's nothing yet to check."
                ),
            },
            "target_hint": {
                "type": "string",
                "description": (
                    "Optional, for click/double_click/type only: a short label for the "
                    "element you're targeting (e.g. 'Compose button', 'Subject field'), "
                    "read from its visible on-screen text. Used as a cross-check against "
                    "your pixel coordinates -- include it whenever the target has visible "
                    "text or an obvious name, omit it for generic/unlabeled points."
                ),
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Your own honest assessment of this specific action's risk. 'high': "
                    "irreversible or consequential -- sending/submitting something, "
                    "deleting, purchasing, paying, publishing, signing out, unsubscribing, "
                    "or anything with a similar real-world effect, REGARDLESS of which "
                    "words you used in 'reasoning' to describe it. 'medium': moderate, "
                    "recoverable changes -- navigating to an unfamiliar site, closing a "
                    "window, changing a setting. 'low': routine, easily-undone actions -- "
                    "scrolling, clicking a normal link, typing into a search box. Always "
                    "set this field for every action; do not leave it out."
                ),
            },
        },
        "required": ["reasoning", "action", "risk_level"],
    },
}


class ClaudeBackend(VLMBackend):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or ANTHROPIC_API_KEY
        if not key:
            raise RuntimeError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY in your environment "
                "or a .env file (see .env.example)."
            )
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model or CLAUDE_MODEL

    def decide(
        self,
        task: str,
        screenshot_bytes: bytes,
        history: list[dict],
        screen_size: tuple[int, int],
    ) -> AgentAction:
        history_text = self._render_history(history)
        b64_image = base64.standard_b64encode(screenshot_bytes).decode("utf-8")
        width, height = screen_size

        user_content = [
            {
                "type": "text",
                "text": (
                    f"TASK: {task}\n\n"
                    f"SCREENSHOT SIZE: {width}x{height} pixels. Your \"coordinates\" MUST be "
                    f"within 0-{width} horizontally and 0-{height} vertically -- any value "
                    f"outside that range will be rejected and the action skipped, wasting a "
                    f"step. Do not estimate coordinates from a guess about typical screen "
                    f"layouts; read them off this exact image.\n\n"
                    f"HISTORY OF PRIOR STEPS (most recent last):\n{history_text}\n\n"
                    "Here is the current screenshot. Decide the single next action."
                ),
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64_image,
                },
            },
        ]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[ACTION_TOOL],
            tool_choice={"type": "tool", "name": "agent_action"},
            messages=[{"role": "user", "content": user_content}],
        )

        tool_use_block = next(
            (b for b in response.content if b.type == "tool_use"), None
        )
        if tool_use_block is None:
            raise RuntimeError(
                f"Claude did not return a tool_use block. Raw response: {response.content}"
            )

        data = dict(tool_use_block.input)
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

    @staticmethod
    def _render_history(history: list[dict]) -> str:
        if not history:
            return "(no prior steps yet — this is the first action)"
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
