"""Per-run logging: every iteration's reasoning, action, and a screenshot."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from config import RUNS_DIR


class RunLogger:
    def __init__(self, task: str, runs_dir: str = RUNS_DIR):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = os.path.join(runs_dir, timestamp)
        self.screenshots_dir = os.path.join(self.run_dir, "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)

        self.task = task
        self._jsonl_path = os.path.join(self.run_dir, "log.jsonl")
        self._txt_path = os.path.join(self.run_dir, "log.txt")

        with open(self._txt_path, "w", encoding="utf-8") as f:
            f.write(f"Task: {task}\nStarted: {datetime.now().isoformat()}\n\n")

    def log_iteration(
        self,
        step: int,
        screenshot,
        action,
        outcome: str,
        confirmed: Optional[bool] = None,
    ) -> None:
        screenshot_path = os.path.join(self.screenshots_dir, f"step_{step:03d}.jpg")
        screenshot.full_image.save(screenshot_path, format="JPEG", quality=80)

        record = {
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reasoning": action.reasoning,
            "action": action.action,
            "coordinates": action.coordinates,
            "text": action.text,
            "key": action.key,
            "scroll_amount": action.scroll_amount,
            "done_summary": action.done_summary,
            "fail_reason": action.fail_reason,
            "expected_outcome": action.expected_outcome,
            "expectation_met": getattr(action, "expectation_met", None),
            "target_hint": getattr(action, "target_hint", None),
            "risk_level": getattr(action, "risk_level", None),
            "outcome": outcome,
            "confirmed_high_risk": confirmed,
            "screenshot": os.path.relpath(screenshot_path, self.run_dir),
        }

        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        with open(self._txt_path, "a", encoding="utf-8") as f:
            f.write(f"[step {step}] action={action.action}\n")
            f.write(f"  reasoning: {action.reasoning}\n")
            if action.coordinates:
                f.write(f"  coordinates: {action.coordinates}\n")
            if confirmed is not None:
                f.write(f"  high-risk confirmed by user: {confirmed}\n")
            f.write(f"  outcome: {outcome}\n\n")

    def log_note(self, text: str) -> None:
        """Append a free-form note to the human-readable log, not tied to a specific step."""
        with open(self._txt_path, "a", encoding="utf-8") as f:
            f.write(f"NOTE: {text}\n\n")

    def finalize(self, status: str, detail: str, iterations: int) -> None:
        summary = {
            "task": self.task,
            "status": status,
            "detail": detail,
            "iterations": iterations,
            "finished": datetime.now(timezone.utc).isoformat(),
        }
        with open(os.path.join(self.run_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        with open(self._txt_path, "a", encoding="utf-8") as f:
            f.write(f"FINISHED: status={status} detail={detail} iterations={iterations}\n")
