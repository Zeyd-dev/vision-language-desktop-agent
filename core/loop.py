"""
Shared agent loop core.

agent.py (CLI), gui.py (Tkinter), and webapp/app.py (browser/phone) all call
run_agent_loop() below, parameterized by a LoopCallbacks object that adapts
logging, confirmation prompts, and stop-checking to whichever frontend is
driving it. This is the one place the loop's actual logic (screenshot ->
decide -> safety-check -> execute -> log -> repeat) lives, so a fix only
needs to be made once instead of three times.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import pyautogui

from actions import (
    ActionExecutor,
    KillSwitch,
    capture_screenshot,
    contains_high_risk_keyword,
    crop_region,
    get_monitor_size,
    get_pyautogui_size,
    is_high_risk_key_combo,
    screens_differ,
)
from backends import get_backend
from config import ACTION_SETTLE_SECONDS, HISTORY_WINDOW
from run_logger import RunLogger


@dataclass
class LoopCallbacks:
    log: Callable[[str], None]
    confirm: Callable[[object, str], bool]  # (action, matched_keyword) -> approved?
    should_stop: Callable[[int], Optional[str]]  # (step) -> stop reason, or None to continue
    on_minimize: Optional[Callable[[], None]] = None  # GUI-only: minimize the control window first


def check_dpi_mismatch(logger: RunLogger, log: Callable[[str], None]) -> None:
    """Warn if mss and pyautogui disagree on screen size (Windows display-scaling trap)."""
    mss_size = get_monitor_size()
    gui_size = get_pyautogui_size()
    if mss_size == gui_size:
        return
    warning = (
        f"WARNING: screen size mismatch. mss (screenshots) reports "
        f"{mss_size[0]}x{mss_size[1]}, pyautogui (clicks) reports "
        f"{gui_size[0]}x{gui_size[1]}. Coordinates get scaled assuming "
        f"mss's resolution, so clicks will land in the wrong place. Fix: "
        f"set Windows display scaling to 100% for this monitor, then restart."
    )
    log(warning)
    logger.log_note(warning)


def _guarded_real_xy(screenshot, cx, cy):
    """Validate bounds + staleness for a model-given coordinate pair.

    Returns the real (x, y) pixel position to act on, or a string outcome
    explaining why the action was skipped instead. Shared by click/
    double_click and by "type" actions that include coordinates, so both
    get the same out-of-bounds and stale-screenshot protection.
    """
    if not screenshot.is_within_bounds(cx, cy):
        w, h = screenshot.resized_size
        return (
            f"skipped: coordinates {[cx, cy]} fall outside the "
            f"{w}x{h} screenshot shown -- refusing to click blind"
        )

    # Guard against acting on a screen that's no longer what the model
    # decided on. The gap between "screenshot taken" and "action executed"
    # is the full decide() round-trip -- easily several seconds, and much
    # longer on Gemini's paced free tier (~13s minimum between calls). A
    # dynamic page can reflow, a spinner resolve, or a banner appear in
    # that window, so the coordinates are still valid for the OLD layout
    # but not necessarily the current one. Re-screenshot right before
    # acting (cheap, <100ms) and bail if it moved on, rather than
    # confidently clicking the wrong thing.
    fresh = capture_screenshot()
    if screens_differ(screenshot.full_image, fresh.full_image):
        return (
            "skipped: the screen changed since this decision was made "
            "(stale click target) -- re-observing instead of clicking blind"
        )

    return screenshot.to_real_coords(cx, cy)


def execute_action(executor: ActionExecutor, action, screenshot):
    """Dispatch one AgentAction to the executor.

    Returns (outcome, acted_xy): outcome is a short string for logging/
    history, and acted_xy is the real (x, y) pixel position clicked, if
    any -- the caller uses it to run a localized screen-diff check around
    that spot (see run_agent_loop), since a small UI change can be too
    subtle to move a whole-screen diff even though the click worked.
    """
    if action.action in ("click", "double_click"):
        cx, cy = action.coordinates
        result = _guarded_real_xy(screenshot, cx, cy)
        if isinstance(result, str):
            return result, None
        x, y = result
        if action.action == "click":
            executor.click(x, y)
        else:
            executor.double_click(x, y)
        # Report the outcome using the coordinates the MODEL gave (image
        # space), not the real screen pixels we scaled them to. This string
        # is fed straight back into the model's own history next turn --
        # if it shows real pixel values while the model is told to always
        # give coordinates in image space, the model has no way to tell
        # the two numbers apart and can pick up a stale real-pixel value
        # as if it were a fresh image-space guess (observed in a real run:
        # step N's proposed coordinates were an exact match for step N-1's
        # real executed pixel position). acted_xy stays in real screen
        # space since the region-diff check crops the real screenshot.
        return f"{action.action} at ({cx}, {cy})", (x, y)

    if action.action == "type":
        # If the model gave coordinates with a "type" action, click there
        # first to actually focus that field before typing. Previously
        # these coordinates were silently ignored and type_text() just
        # typed into whatever already had focus -- in a real run this
        # concatenated a recipient address and an email subject into the
        # same "To" field, because nothing ever moved focus to Subject
        # between the two "type" actions.
        acted_xy = None
        display_xy = None
        if action.coordinates:
            cx, cy = action.coordinates
            result = _guarded_real_xy(screenshot, cx, cy)
            if isinstance(result, str):
                return result, None
            acted_xy = result
            display_xy = (cx, cy)  # image-space, for the reason above
            executor.click(*acted_xy)
        executor.type_text(action.text)
        prefix = f"clicked ({display_xy[0]}, {display_xy[1]}) then " if display_xy else ""
        return f"{prefix}typed {len(action.text)} characters", acted_xy

    if action.action == "key":
        executor.press_key(action.key)
        return f"pressed key '{action.key}'", None

    if action.action == "scroll":
        executor.scroll(action.scroll_amount)
        return f"scrolled ({action.scroll_amount or 'default'})", None

    if action.action == "wait":
        executor.wait()
        return "waited", None

    if action.action == "open_url":
        opened = executor.open_url(action.text or "")
        return f"opened in default browser: {opened}", None

    if action.action == "launch_app":
        launched = executor.launch_app(action.text or "")
        return f"launched app: {launched}", None

    if action.action == "focus_window":
        return executor.focus_window(action.text or ""), None

    return f"unrecognized action '{action.action}' (no-op)", None


def run_agent_loop(
    task: str,
    backend_name: str,
    max_iterations: int,
    max_minutes: float,
    callbacks: LoopCallbacks,
) -> tuple[str, str, str]:
    """Runs the full loop to completion. Returns (status, detail, run_dir)."""
    log = callbacks.log

    try:
        backend = get_backend(backend_name)
    except Exception as e:
        log(f"ERROR creating backend: {e}")
        return "fail", str(e), ""

    executor = ActionExecutor()
    logger = RunLogger(task)

    log(f"Task: {task}")
    log(f"Backend: {backend_name}")
    log(f"Logging to: {logger.run_dir}")

    if callbacks.on_minimize:
        callbacks.on_minimize()
        time.sleep(0.4)  # let a minimize animation finish before the first screenshot

    check_dpi_mismatch(logger, log)

    history: list[dict] = []
    previous_screenshot = None
    start_time = time.monotonic()
    status, detail, step = "iteration_limit", "Reached the iteration cap", 0

    try:
        for step in range(1, max_iterations + 1):
            stop_reason = callbacks.should_stop(step)
            if stop_reason is not None:
                status, detail = "killed", stop_reason
                break

            elapsed_minutes = (time.monotonic() - start_time) / 60
            if elapsed_minutes >= max_minutes:
                status, detail = "time_limit", f"Reached the {max_minutes}-minute cap"
                break

            log(f"[step {step}] capturing screenshot...")
            screenshot = capture_screenshot()

            # Backfill: did the last action actually change anything on screen?
            if previous_screenshot is not None and history:
                changed = screens_differ(previous_screenshot.full_image, screenshot.full_image)
                if not changed:
                    # A whole-screen diff can miss a real, small change (a
                    # popup closing, one field updating) on a large monitor --
                    # the rest of the screen swamps it. If the last action
                    # clicked/typed at a specific spot, also check just that
                    # local area before concluding nothing happened.
                    xy = history[-1].get("xy")
                    if xy:
                        region_before = crop_region(previous_screenshot.full_image, *xy)
                        region_after = crop_region(screenshot.full_image, *xy)
                        changed = screens_differ(region_before, region_after)
                history[-1]["screen_changed"] = changed
                if not changed:
                    log(f"[step {step}] (note: screen looks unchanged since the previous action)")
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
                log(f"[step {step}] STUCK-LOOP WARNING: last 3 actions had no visible effect -- nudging model to change strategy")
                logger.log_note(f"step {step}: stuck-loop nudge sent -- {note}")

            log(f"[step {step}] asking the model for the next action...")
            action = backend.decide(
                task=effective_task,
                screenshot_bytes=screenshot.api_bytes,
                history=history,
                # The *resized* dimensions -- i.e. the size of the image actually
                # encoded in screenshot.api_bytes, not the real monitor resolution.
                # Coordinates the model returns are in this space (see
                # Screenshot.to_real_coords()), so this is the number that must
                # reach the prompt for "stay within bounds" to mean anything.
                screen_size=screenshot.resized_size,
            )
            log(f"[step {step}] reasoning: {action.reasoning}")
            log(f"[step {step}] action: {action.action} "
                f"{action.coordinates or action.text or action.key or ''}")

            if action.action == "done":
                logger.log_iteration(step, screenshot, action, outcome="task complete")
                status, detail = "done", action.done_summary or "Task reported complete"
                log(f"\nDONE: {detail}")
                break

            if action.action == "fail":
                logger.log_iteration(step, screenshot, action, outcome="agent gave up")
                status, detail = "fail", action.fail_reason or "Agent reported failure"
                log(f"\nFAILED: {detail}")
                break

            matched_keyword = contains_high_risk_keyword(
                action.reasoning, action.done_summary or "", action.text or ""
            )
            if not matched_keyword and action.action == "key":
                combo = is_high_risk_key_combo(action.key)
                if combo:
                    matched_keyword = f"key:{combo}"
            confirmed = None
            if matched_keyword:
                confirmed = callbacks.confirm(action, matched_keyword)
                if not confirmed:
                    outcome = "skipped: user declined high-risk confirmation"
                    log(f"[step {step}] {outcome}")
                    logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=False)
                    history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome, "xy": None})
                    history = history[-HISTORY_WINDOW:]
                    continue

            outcome, acted_xy = execute_action(executor, action, screenshot)
            log(f"[step {step}] {outcome}")
            logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=confirmed)
            history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome, "xy": acted_xy})
            history = history[-HISTORY_WINDOW:]

            # Let the page/app settle before the next screenshot.
            time.sleep(ACTION_SETTLE_SECONDS)

    except KillSwitch as e:
        status, detail = "killed", str(e)
        log(f"\nKILL SWITCH: {detail}")
    except pyautogui.FailSafeException:
        status, detail = "killed", "pyautogui failsafe triggered (mouse moved to screen corner)"
        log(detail)
    except Exception as e:  # noqa: BLE001 - top-level guard so a run always finalizes cleanly
        status, detail = "fail", f"Unhandled error: {e}"
        log(f"ERROR: {detail}")

    logger.finalize(status=status, detail=detail, iterations=step)
    log(f"\nRun finished: status={status} detail={detail}")
    return status, detail, logger.run_dir
