"""CLI entry point for the Vision-Language Desktop Agent."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from actions import KillSwitch, StdinListener, confirm_risky_action
from config import CHECKIN_EVERY_N_ITERATIONS, DEFAULT_BACKEND, DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_MINUTES
from core import LoopCallbacks, run_agent_loop


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vision-Language Desktop Agent")
    parser.add_argument("task", help="Natural-language instruction for the agent to complete")
    parser.add_argument(
        "--backend",
        choices=["claude", "gemini"],
        default=DEFAULT_BACKEND,
        help=f"Which VLM backend to use (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Hard cap on loop iterations (default: {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=DEFAULT_MAX_MINUTES,
        help=f"Hard cap on wall-clock run time in minutes (default: {DEFAULT_MAX_MINUTES})",
    )
    return parser.parse_args()


def run(task: str, max_iterations: int, max_minutes: float, backend_name: str = DEFAULT_BACKEND) -> int:
    listener = StdinListener()
    listener.start()

    print(f"Backend: {backend_name}")
    print(f"Limits: {max_iterations} iterations, {max_minutes} minutes")
    print(f"Kill switch: type 'stop' + Enter at any time to halt the run.")
    print("Move the mouse to a screen corner at any time as a physical failsafe.\n")

    def _should_stop(step: int) -> Optional[str]:
        if listener.check_kill_switch():
            raise KillSwitch("Kill switch triggered before iteration start")
        if step > 1 and (step - 1) % CHECKIN_EVERY_N_ITERATIONS == 0:
            print(f"\n--- Check-in: {step - 1} iterations so far. Continue? (y/n): ", end="", flush=True)
            answer = listener.read_line_blocking()
            if answer.lower() == "stop" or answer.lower() not in ("y", "yes"):
                return "User stopped the run at a periodic check-in"
        return None

    callbacks = LoopCallbacks(
        log=print,
        confirm=lambda action, kw: confirm_risky_action(listener, action, kw),
        should_stop=_should_stop,
    )

    status, detail, run_dir = run_agent_loop(task, backend_name, max_iterations, max_minutes, callbacks)
    if run_dir:
        print(f"Full log: {run_dir}")
    return 0 if status == "done" else 1


def main() -> None:
    args = parse_args()
    exit_code = run(args.task, args.max_iterations, args.max_minutes, backend_name=args.backend)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
