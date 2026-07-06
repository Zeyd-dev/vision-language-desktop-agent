#!/usr/bin/env python3
"""
CLI entry point for the Vision-Language Desktop Agent.

Usage:
    python agent.py "Open a browser and go to YouTube"
    python agent.py "Open a browser and go to YouTube" --max-iterations 15 --max-minutes 5

This file is intentionally just the orchestration loop: screenshot ->
backend.decide() -> safety check -> execute -> log -> repeat. All the
"smart" pieces (the model call, the pixel-level actions, the safety
policy) live in backends/ and actions/ so this stays readable end to end.
"""
from __future__ import annotations

import argparse
import sys
import time

import pyautogui

from actions import (
    ActionExecutor,
    KillSwitch,
    StdinListener,
    capture_screenshot,
    confirm_risky_action,
    contains_high_risk_keyword,
    get_monitor_size,
    get_pyautogui_size,
    screens_differ,
)
from backends import get_backend
from config import ACTION_SETTLE_SECONDS, CHECKIN_EVERY_N_ITERATIONS, DEFAULT_BACKEND, DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_MINUTES, HISTORY_WINDOW
from run_logger import RunLogger


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
    backend = get_backend(backend_name)
    executor = ActionExecutor()
    logger = RunLogger(task)
    listener = StdinListener()
    listener.start()

    print(f"Task: {task}")
    print(f"Backend: {backend_name}")
    print(f"Logging to: {logger.run_dir}")
    print(f"Limits: {max_iterations} iterations, {max_minutes} minutes")
    print(f"Kill switch: type 'stop' + Enter at any time to halt the run.")
    print("Move the mouse to a screen corner at any time as a physical failsafe.\n")

    _check_dpi_mismatch(logger)

    history: list[dict] = []
    previous_screenshot = None
    start_time = time.monotonic()
    status, detail, step = "iteration_limit", "Reached the iteration cap", 0

    try:
        for step in range(1, max_iterations + 1):
            elapsed_minutes = (time.monotonic() - start_time) / 60
            if elapsed_minutes >= max_minutes:
                status, detail = "time_limit", f"Reached the {max_minutes}-minute cap"
                break

            if listener.check_kill_switch():
                raise KillSwitch("Kill switch triggered before iteration start")

            if step > 1 and (step - 1) % CHECKIN_EVERY_N_ITERATIONS == 0:
                print(f"\n--- Check-in: {step - 1} iterations so far. Continue? (y/n): ", end="", flush=True)
                answer = listener.read_line_blocking()
                if answer.lower() == "stop" or answer.lower() not in ("y", "yes"):
                    status, detail = "killed", "User stopped the run at a periodic check-in"
                    break

            print(f"[step {step}] capturing screenshot...")
            screenshot = capture_screenshot()

            # Objective ground truth for "did the last action actually do
            # anything" -- computed by comparing images, not by asking the
            # model to judge its own work. Backfilled onto the previous
            # history entry now that we have the "after" screenshot.
            if previous_screenshot is not None and history:
                changed = screens_differ(previous_screenshot.full_image, screenshot.full_image)
                history[-1]["screen_changed"] = changed
                if not changed:
                    print(f"[step {step}] (note: screen looks unchanged since the previous action)")
            previous_screenshot = screenshot

            effective_task = task
            if len(history) >= 3 and all(h.get("screen_changed") is False for h in history[-3:]):
                note = (
                    "Your last 3 actions produced NO visible change on screen "
                    "(confirmed by image comparison, not just your own judgment). "
                    "Whatever approach you were using is not working -- do not repeat "
                    "a similar click/type at a similar location again. If you've been "
                    "trying to open an app or browser via the Start menu, taskbar, or "
                    "keyboard shortcuts, STOP and use action 'open_url' (to go straight "
                    "to a website/search) or 'launch_app' (to launch a named app "
                    "directly) instead -- these bypass the GUI entirely and don't "
                    "depend on menus being focused. Otherwise switch to a genuinely "
                    "different element or method, or report 'fail' if truly stuck."
                )
                effective_task = f"{task}\n\n[SYSTEM NOTE] {note}"
                print(f"[step {step}] STUCK-LOOP WARNING: last 3 actions had no visible effect -- nudging model to change strategy")
                logger.log_note(f"step {step}: stuck-loop nudge sent -- {note}")

            print(f"[step {step}] asking the model for the next action...")
            action = backend.decide(
                task=effective_task,
                screenshot_bytes=screenshot.api_bytes,
                history=history,
                screen_size=screenshot.real_size,
            )

            print(f"[step {step}] reasoning: {action.reasoning}")
            print(f"[step {step}] action: {action.action} "
                  f"{action.coordinates or action.text or action.key or ''}")

            if action.action == "done":
                logger.log_iteration(step, screenshot, action, outcome="task complete")
                status, detail = "done", action.done_summary or "Task reported complete"
                print(f"\nDONE: {detail}")
                break

            if action.action == "fail":
                logger.log_iteration(step, screenshot, action, outcome="agent gave up")
                status, detail = "fail", action.fail_reason or "Agent reported failure"
                print(f"\nFAILED: {detail}")
                break

            matched_keyword = contains_high_risk_keyword(
                action.reasoning, action.done_summary or "", action.text or ""
            )
            confirmed = None
            if matched_keyword:
                confirmed = confirm_risky_action(listener, action, matched_keyword)
                if not confirmed:
                    outcome = "skipped: user declined high-risk confirmation"
                    print(f"[step {step}] {outcome}")
                    logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=False)
                    history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome})
                    history = history[-HISTORY_WINDOW:]
                    continue

            outcome = _execute(executor, action, screenshot)
            print(f"[step {step}] {outcome}")
            logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=confirmed)
            history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome})
            history = history[-HISTORY_WINDOW:]

            # Give the page/app a moment to settle before the next screenshot.
            # Without this, a screenshot taken immediately after a click/
            # navigation can catch a page mid-layout-shift (async-loading
            # panels, images, ads on something like a Google results page),
            # which both misleads the model and can make screens_differ()
            # wrongly report "nothing changed" for an action that did work.
            time.sleep(ACTION_SETTLE_SECONDS)
        else:
            # for/else: loop completed all iterations without break
            pass

    except KillSwitch as e:
        status, detail = "killed", str(e)
        print(f"\nKILL SWITCH: {detail}")
    except pyautogui.FailSafeException:
        status, detail = "killed", "pyautogui failsafe triggered (mouse moved to screen corner)"
        print(f"\n{detail}")
    except Exception as e:  # noqa: BLE001 - top-level guard so a run always finalizes cleanly
        status, detail = "fail", f"Unhandled error: {e}"
        print(f"\nERROR: {detail}")

    logger.finalize(status=status, detail=detail, iterations=step)
    print(f"\nRun finished: status={status} detail={detail}")
    print(f"Full log: {logger.run_dir}")
    return 0 if status == "done" else 1


def _check_dpi_mismatch(logger: RunLogger) -> None:
    """
    Compares the screen size mss reports (what screenshots are captured at,
    and what model coordinates get scaled against) to the screen size
    pyautogui believes it's operating in (what mouse clicks actually use).

    These should always match exactly. When they don't -- typically because
    Windows display scaling wasn't fully picked up by one of the two
    libraries -- every click the agent makes lands off-target by a
    predictable ratio, which looks exactly like "the model keeps clicking
    near the right spot but nothing happens." This check surfaces that
    class of bug immediately instead of leaving it to be diagnosed after
    dozens of failed clicks.
    """
    mss_size = get_monitor_size()
    gui_size = get_pyautogui_size()
    if mss_size == gui_size:
        return
    warning = (
        f"WARNING: screen size mismatch detected. mss (screenshots) reports "
        f"{mss_size[0]}x{mss_size[1]}, but pyautogui (mouse clicks) reports "
        f"{gui_size[0]}x{gui_size[1]}. When these differ, every click/type "
        f"coordinate the model computes from the screenshot gets mapped to "
        f"the WRONG place on the real screen, because we scale coordinates "
        f"assuming mss's resolution while pyautogui moves the mouse using "
        f"its own, different one. This is a very likely cause of repeated "
        f"failed clicks that look like they're near the right target. Fix: "
        f"set Windows display scaling to 100% for this monitor (Settings > "
        f"System > Display > Scale), then restart this script."
    )
    print("\n" + "!" * 70)
    print(warning)
    print("!" * 70 + "\n")
    logger.log_note(warning)


def _execute(executor: ActionExecutor, action, screenshot) -> str:
    """Dispatch one AgentAction to the executor. Returns a short outcome string for logging/history."""
    if action.action in ("click", "double_click"):
        cx, cy = action.coordinates
        if not screenshot.is_within_bounds(cx, cy):
            w, h = screenshot.resized_size
            return (
                f"skipped: coordinates {action.coordinates} fall outside the "
                f"{w}x{h} screenshot shown -- refusing to click blind"
            )
        x, y = screenshot.to_real_coords(cx, cy)
        if action.action == "click":
            executor.click(x, y)
        else:
            executor.double_click(x, y)
        return f"{action.action} at ({x}, {y})"

    if action.action == "type":
        executor.type_text(action.text)
        return f"typed {len(action.text)} characters"

    if action.action == "key":
        executor.press_key(action.key)
        return f"pressed key '{action.key}'"

    if action.action == "scroll":
        executor.scroll(action.scroll_amount)
        return f"scrolled ({action.scroll_amount or 'default'})"

    if action.action == "wait":
        executor.wait()
        return "waited"

    if action.action == "open_url":
        opened = executor.open_url(action.text or "")
        return f"opened in default browser: {opened}"

    if action.action == "launch_app":
        launched = executor.launch_app(action.text or "")
        return f"launched app: {launched}"

    return f"unrecognized action '{action.action}' (no-op)"


def main() -> None:
    args = parse_args()
    exit_code = run(args.task, args.max_iterations, args.max_minutes, backend_name=args.backend)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
