"""
Claude implementation of VLMBackend.

Uses Anthropic's tool-use (function calling) to force the model's response
into the strict action schema, rather than asking it to emit raw JSON and
hoping it's well-formed. tool_choice pins the model to a single tool, so
`response.content` will contain exactly one tool_use block whose `input`
already matches our schema.
"""
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
                    "done",
                    "fail",
                ],
            },
            "coordinates": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "[x, y] pixel position in the screenshot you were shown.",
            },
            "text": {
                "type": "string",
                "description": (
                    "String to type, if action is 'type'. A URL or search query, if "
                    "action is 'open_url'. An application name, if action is 'launch_app'."
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
        },
        "required": ["reasoning", "action"],
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

        user_content = [
            {
                "type": "text",
                "text": (
                    f"TASK: {task}\n\n"
                    f"SCREENSHOT SIZE: {screenshot_bytes and 'see image'} "
                    f"(coordinates you give should be pixels within this image)\n\n"
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
            lines.append(line)
        return "\n".join(lines)
