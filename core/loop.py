"""Shared agent loop core."""
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
    is_self_reported_high_risk,
    screens_differ,
)
from backends import get_backend
from config import ACTION_SETTLE_SECONDS, HISTORY_WINDOW
from run_logger import RunLogger


@dataclass
class LoopCallbacks:
    log: Callable[[str], None]
    confirm: Callable[[object, str], bool]
    should_stop: Callable[[int], Optional[str]]
    on_minimize: Optional[Callable[[], None]] = None


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
    """Validate bounds + staleness for a model-given coordinate pair."""
    if not screenshot.is_within_bounds(cx, cy):
        w, h = screenshot.resized_size
        return (
            f"skipped: coordinates {[cx, cy]} fall outside the "
            f"{w}x{h} screenshot shown -- refusing to click blind"
        )

    fresh = capture_screenshot()
    if screens_differ(screenshot.full_image, fresh.full_image):
        return (
            "skipped: the screen changed since this decision was made "
            "(stale click target) -- re-observing instead of clicking blind"
        )

    return screenshot.to_real_coords(cx, cy)


_ELEMENT_SNAP_MAX_DISTANCE = 150


def _maybe_snap_to_element(executor: ActionExecutor, target_hint, real_xy):
    """Cross-check the model's guessed click point against the real UI."""
    if not target_hint:
        return real_xy, None
    bounds = executor.find_element_bounds(target_hint)
    if not bounds:
        return real_xy, None
    left, top, right, bottom = bounds
    element_x, element_y = (left + right) / 2, (top + bottom) / 2
    real_x, real_y = real_xy
    distance = ((element_x - real_x) ** 2 + (element_y - real_y) ** 2) ** 0.5
    if distance > _ELEMENT_SNAP_MAX_DISTANCE:
        return real_xy, None
    return (int(round(element_x)), int(round(element_y))), f"snapped to element '{target_hint}'"


def execute_action(executor: ActionExecutor, action, screenshot):
    """Dispatch one AgentAction to the executor."""
    if action.action in ("click", "double_click"):
        cx, cy = action.coordinates
        result = _guarded_real_xy(screenshot, cx, cy)
        if isinstance(result, str):
            return result, None
        x, y = result
        (x, y), snap_note = _maybe_snap_to_element(executor, action.target_hint, (x, y))
        if action.action == "click":
            executor.click(x, y)
        else:
            executor.double_click(x, y)
        suffix = f" [{snap_note}]" if snap_note else ""
        return f"{action.action} at ({cx}, {cy}){suffix}", (x, y)

    if action.action == "type":
        acted_xy = None
        display_xy = None
        snap_note = None
        if action.coordinates:
            cx, cy = action.coordinates
            result = _guarded_real_xy(screenshot, cx, cy)
            if isinstance(result, str):
                return result, None
            acted_xy, snap_note = _maybe_snap_to_element(executor, action.target_hint, result)
            display_xy = (cx, cy)
            executor.click(*acted_xy)
        executor.type_text(action.text)
        prefix = f"clicked ({display_xy[0]}, {display_xy[1]}) then " if display_xy else ""
        suffix = f" [{snap_note}]" if snap_note else ""
        return f"{prefix}typed {len(action.text)} characters{suffix}", acted_xy

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


def _target_key(action, acted_xy):
    """A key identifying WHAT an action was aimed at, coarse enough that two."""
    if action.action in ("click", "double_click"):
        if not acted_xy:
            return None
        x, y = acted_xy
        return (action.action, round(x / 25) * 25, round(y / 25) * 25)
    if action.action == "type":
        if not acted_xy:
            return ("type", None)
        x, y = acted_xy
        return ("type", round(x / 25) * 25, round(y / 25) * 25)
    if action.action == "focus_window":
        return ("focus_window", (action.text or "").strip().lower())
    if action.action == "key":
        return ("key", (action.key or "").strip().lower())
    if action.action in ("open_url", "launch_app"):
        return (action.action, (action.text or "").strip().lower())
    return None


def _find_stuck_repeat(history: list[dict]):
    """Stricter, faster companion to the generic 3-in-a-row stuck-detection check."""
    if len(history) < 2:
        return None
    a, b = history[-2], history[-1]
    key_a, key_b = a.get("target_key"), b.get("target_key")
    if key_a is None or key_a != key_b:
        return None
    if a.get("action") != b.get("action"):
        return None

    def _failed(entry: dict) -> bool:
        return entry.get("screen_changed") is False or entry.get("expectation_met") is False

    if _failed(a) and _failed(b):
        return b
    return None


def _stuck_repeat_nudge(entry: dict) -> str:
    """Build a nudge specific to WHAT kept failing, not a generic one-size."""
    action = entry.get("action")
    key = entry.get("target_key")
    if action == "focus_window":
        name = key[1] if isinstance(key, tuple) and len(key) > 1 else "this window"
        return (
            f"'focus_window' targeting \"{name}\" has now failed twice in a row with "
            f"no real effect. Windows is very likely blocking the foreground switch "
            f"outright for this window right now. Do NOT try focus_window on it again, "
            f"even with a differently-worded substring -- click directly on the visible "
            f"window instead."
        )
    if action in ("click", "double_click"):
        return (
            "The exact same click target has now failed twice in a row with no real "
            "effect (confirmed by screen comparison and/or your own expectation check). "
            "Do not try a third click at this location, even at a slightly different "
            "pixel -- scroll it into view, pick a genuinely different element, or use a "
            "different method entirely."
        )
    if action == "type":
        return (
            "Typing into this same spot has now failed twice in a row with no real "
            "effect. The field was likely never actually focused. Use 'key' with 'tab' "
            "from the last field you successfully filled, instead of guessing new "
            "coordinates for this one again."
        )
    return (
        "The exact same action and target have now failed twice in a row with no real "
        "effect. Switch to a genuinely different approach instead of repeating it."
    )


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
        time.sleep(0.4)

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

            if previous_screenshot is not None and history:
                changed = screens_differ(previous_screenshot.full_image, screenshot.full_image)
                if not changed:
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
            stuck_repeat = _find_stuck_repeat(history)
            if stuck_repeat is not None:
                note = _stuck_repeat_nudge(stuck_repeat)
                effective_task = f"{task}\n\n[SYSTEM NOTE] {note}"
                log(f"[step {step}] STUCK-REPEAT WARNING: same action+target failed twice -- nudging model to change strategy")
                logger.log_note(f"step {step}: stuck-repeat nudge sent -- {note}")
            elif len(history) >= 3 and all(h.get("screen_changed") is False for h in history[-3:]):
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
                screen_size=screenshot.resized_size,
            )
            if history:
                history[-1]["expectation_met"] = action.expectation_met
                if action.expectation_met is False:
                    log(f"[step {step}] (note: model's own reflect check says the previous action did NOT produce the expected result)")

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
            if not matched_keyword and is_self_reported_high_risk(action.risk_level):
                matched_keyword = "self-reported risk_level=high"
            confirmed = None
            if matched_keyword:
                confirmed = callbacks.confirm(action, matched_keyword)
                if not confirmed:
                    outcome = "skipped: user declined high-risk confirmation"
                    log(f"[step {step}] {outcome}")
                    logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=False)
                    history.append({
                        "action": action.action,
                        "reasoning": action.reasoning,
                        "outcome": outcome,
                        "xy": None,
                        "target_key": None,
                        "expected_outcome": action.expected_outcome,
                    })
                    history = history[-HISTORY_WINDOW:]
                    continue

            outcome, acted_xy = execute_action(executor, action, screenshot)
            log(f"[step {step}] {outcome}")
            logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=confirmed)
            history.append({
                "action": action.action,
                "reasoning": action.reasoning,
                "outcome": outcome,
                "xy": acted_xy,
                "target_key": _target_key(action, acted_xy),
                "expected_outcome": action.expected_outcome,
            })
            history = history[-HISTORY_WINDOW:]

            time.sleep(ACTION_SETTLE_SECONDS)

    except KillSwitch as e:
        status, detail = "killed", str(e)
        log(f"\nKILL SWITCH: {detail}")
    except pyautogui.FailSafeException:
        status, detail = "killed", "pyautogui failsafe triggered (mouse moved to screen corner)"
        log(detail)
    except Exception as e:
        status, detail = "fail", f"Unhandled error: {e}"
        log(f"ERROR: {detail}")

    logger.finalize(status=status, detail=detail, iterations=step)
    log(f"\nRun finished: status={status} detail={detail}")
    return status, detail, logger.run_dir
