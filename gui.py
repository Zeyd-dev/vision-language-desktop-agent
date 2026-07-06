#!/usr/bin/env python3
"""
Minimal desktop GUI for the Vision-Language Desktop Agent.

This is a thin wrapper around the same building blocks agent.py's CLI
uses (get_backend, ActionExecutor, capture_screenshot, RunLogger, the
safety keyword check) -- it's the same loop, just with Tkinter widgets
standing in for the terminal:

  - print()                  -> a line appended to the on-screen log box
  - the stdin kill switch     -> a "Stop" button setting a threading.Event
  - the stdin y/n confirmation-> a messagebox.askyesno popup

The loop runs on a background thread so the window stays responsive while
the agent is working. Tkinter widgets can only be touched from the main
thread, so the worker thread never updates the UI directly -- it puts
messages on thread-safe queues, and a periodic `root.after(...)` poll on
the main thread drains those queues and updates the widgets.

Usage:
    python gui.py
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import pyautogui

from actions import (
    ActionExecutor,
    capture_screenshot,
    contains_high_risk_keyword,
    get_monitor_size,
    get_pyautogui_size,
    screens_differ,
)
from backends import get_backend
from config import ACTION_SETTLE_SECONDS, DEFAULT_BACKEND, DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_MINUTES, HISTORY_WINDOW
from run_logger import RunLogger


class AgentGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Vision-Language Desktop Agent")
        root.geometry("760x580")
        root.minsize(600, 420)

        # Thread-safe channels between the worker thread (running the loop)
        # and the main thread (the only thread allowed to touch widgets).
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.confirm_request: "queue.Queue[tuple]" = queue.Queue()
        self.confirm_answer: "queue.Queue[bool]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_run_dir: str | None = None

        self._build_widgets()
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------
    # Widget layout
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Task:").grid(row=0, column=0, sticky="w")
        self.task_entry = ttk.Entry(top)
        self.task_entry.insert(0, "Open a browser and go to YouTube")
        self.task_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)

        ttk.Label(top, text="Backend:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.backend_var = tk.StringVar(value=DEFAULT_BACKEND)
        ttk.Combobox(
            top, textvariable=self.backend_var, values=["claude", "gemini"],
            width=10, state="readonly",
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(top, text="Max steps:").grid(row=1, column=2, sticky="e", pady=(6, 0))
        self.max_iter_var = tk.IntVar(value=DEFAULT_MAX_ITERATIONS)
        ttk.Spinbox(top, from_=1, to=200, textvariable=self.max_iter_var, width=6).grid(
            row=1, column=3, sticky="w", pady=(6, 0)
        )

        ttk.Label(top, text="Max minutes:").grid(row=2, column=2, sticky="e")
        self.max_min_var = tk.DoubleVar(value=DEFAULT_MAX_MINUTES)
        ttk.Spinbox(top, from_=1, to=120, textvariable=self.max_min_var, width=6).grid(
            row=2, column=3, sticky="w"
        )

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.run_btn = ttk.Button(btns, text="Run", command=self._on_run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_runs_btn = ttk.Button(
            btns, text="Open last run folder", command=self._open_last_run, state="disabled"
        )
        self.open_runs_btn.pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(btns, textvariable=self.status_var, foreground="#555").pack(side="right")

        self.output = scrolledtext.ScrolledText(self.root, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True, **pad)

        hint = "Move the mouse to a screen corner at any time as a physical failsafe."
        ttk.Label(self.root, text=hint, foreground="#888").pack(anchor="w", padx=10, pady=(0, 6))

    # ------------------------------------------------------------------
    # Called FROM the worker thread -- only ever touches thread-safe queues,
    # never a widget directly.
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _ask_confirm(self, action, matched_keyword: str) -> bool:
        """Blocks the worker thread until the main thread shows a popup and answers."""
        self.confirm_request.put((action, matched_keyword))
        return self.confirm_answer.get()

    # ------------------------------------------------------------------
    # Runs on the main thread, on a timer -- the only place widgets are touched.
    # ------------------------------------------------------------------
    def _poll(self) -> None:
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.output.configure(state="normal")
            self.output.insert("end", msg + "\n")
            self.output.see("end")
            self.output.configure(state="disabled")

        try:
            action, matched_keyword = self.confirm_request.get_nowait()
        except queue.Empty:
            pass
        else:
            details = f"Matched keyword: '{matched_keyword}'\n\nAction: {action.action}\nReasoning: {action.reasoning}"
            if action.coordinates:
                details += f"\nCoordinates: {action.coordinates}"
            if action.text:
                details += f"\nText to type: {action.text!r}"
            proceed = messagebox.askyesno("Confirm high-risk action", details)
            self.confirm_answer.put(proceed)

        self.root.after(150, self._poll)

    # ------------------------------------------------------------------
    # Button handlers (main thread)
    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        task = self.task_entry.get().strip()
        if not task:
            messagebox.showwarning("Missing task", "Type a task first.")
            return

        self.stop_event.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set("Running...")
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

        # Minimize this window before the loop starts capturing screenshots.
        # Without this, the agent's first screenshot shows our own control
        # panel, and the model tries to click OUR "Run" button instead of
        # anything on the real desktop. update() forces Windows to actually
        # process the minimize before we hand off to the worker thread.
        self.root.iconify()
        self.root.update()

        self.worker = threading.Thread(
            target=self._run_loop,
            args=(task, self.backend_var.get(), self.max_iter_var.get(), self.max_min_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _on_stop(self) -> None:
        self.stop_event.set()
        self._log("Stop requested -- halting after the current step...")

    def _open_last_run(self) -> None:
        if self.last_run_dir and os.path.isdir(self.last_run_dir):
            os.startfile(self.last_run_dir)  # Windows-only, matches this project's target OS

    # ------------------------------------------------------------------
    # The loop itself (worker thread) -- same structure as agent.run() in
    # agent.py, with GUI-native stand-ins for stdin/stdout. If you've read
    # agent.py already, this should look very familiar.
    # ------------------------------------------------------------------
    def _run_loop(self, task: str, backend_name: str, max_iterations: int, max_minutes: float) -> None:
        try:
            backend = get_backend(backend_name)
        except Exception as e:
            self._log(f"ERROR creating backend: {e}")
            self._finish("fail")
            return

        executor = ActionExecutor()
        logger = RunLogger(task)
        self.last_run_dir = logger.run_dir

        self._log(f"Task: {task}")
        self._log(f"Backend: {backend_name}")
        self._log(f"Logging to: {logger.run_dir}\n")

        # Small buffer so Windows finishes the minimize animation from
        # iconify() before we take the first screenshot.
        time.sleep(0.4)

        self._check_dpi_mismatch(logger)

        history: list[dict] = []
        previous_screenshot = None
        start_time = time.monotonic()
        status, detail, step = "iteration_limit", "Reached the iteration cap", 0

        try:
            for step in range(1, max_iterations + 1):
                if self.stop_event.is_set():
                    status, detail = "killed", "Stopped from the GUI"
                    break

                elapsed_minutes = (time.monotonic() - start_time) / 60
                if elapsed_minutes >= max_minutes:
                    status, detail = "time_limit", f"Reached the {max_minutes}-minute cap"
                    break

                self._log(f"[step {step}] capturing screenshot...")
                screenshot = capture_screenshot()

                if previous_screenshot is not None and history:
                    changed = screens_differ(previous_screenshot.full_image, screenshot.full_image)
                    history[-1]["screen_changed"] = changed
                    if not changed:
                        self._log(f"[step {step}] (note: screen looks unchanged since the previous action)")
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
                    self._log(f"[step {step}] STUCK-LOOP WARNING: last 3 actions had no visible effect -- nudging model to change strategy")
                    logger.log_note(f"step {step}: stuck-loop nudge sent -- {note}")

                self._log(f"[step {step}] asking the model for the next action...")
                action = backend.decide(
                    task=effective_task,
                    screenshot_bytes=screenshot.api_bytes,
                    history=history,
                    screen_size=screenshot.real_size,
                )
                self._log(f"[step {step}] reasoning: {action.reasoning}")
                self._log(
                    f"[step {step}] action: {action.action} "
                    f"{action.coordinates or action.text or action.key or ''}"
                )

                if action.action == "done":
                    logger.log_iteration(step, screenshot, action, outcome="task complete")
                    status, detail = "done", action.done_summary or "Task reported complete"
                    self._log(f"\nDONE: {detail}")
                    break

                if action.action == "fail":
                    logger.log_iteration(step, screenshot, action, outcome="agent gave up")
                    status, detail = "fail", action.fail_reason or "Agent reported failure"
                    self._log(f"\nFAILED: {detail}")
                    break

                matched_keyword = contains_high_risk_keyword(
                    action.reasoning, action.done_summary or "", action.text or ""
                )
                confirmed = None
                if matched_keyword:
                    confirmed = self._ask_confirm(action, matched_keyword)
                    if not confirmed:
                        outcome = "skipped: user declined high-risk confirmation"
                        self._log(f"[step {step}] {outcome}")
                        logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=False)
                        history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome})
                        history = history[-HISTORY_WINDOW:]
                        continue

                outcome = self._execute(executor, action, screenshot)
                self._log(f"[step {step}] {outcome}")
                logger.log_iteration(step, screenshot, action, outcome=outcome, confirmed=confirmed)
                history.append({"action": action.action, "reasoning": action.reasoning, "outcome": outcome})
                history = history[-HISTORY_WINDOW:]

                # Let the page/app settle before the next screenshot -- see
                # the matching comment in agent.py's loop for why.
                time.sleep(ACTION_SETTLE_SECONDS)

        except pyautogui.FailSafeException:
            status, detail = "killed", "pyautogui failsafe triggered (mouse moved to screen corner)"
            self._log(detail)
        except Exception as e:  # noqa: BLE001 - keep the GUI alive and report the error instead of crashing
            status, detail = "fail", f"Unhandled error: {e}"
            self._log(f"ERROR: {detail}")

        logger.finalize(status=status, detail=detail, iterations=step)
        self._log(f"\nRun finished: status={status} detail={detail}")
        self._finish(status)

    def _check_dpi_mismatch(self, logger: RunLogger) -> None:
        """
        Same check as agent.py's _check_dpi_mismatch: if mss (screenshots)
        and pyautogui (clicks) disagree about the screen resolution, every
        click the agent makes lands off-target by a predictable ratio --
        one of the most likely explanations for clicks that look close but
        never seem to register.
        """
        mss_size = get_monitor_size()
        gui_size = get_pyautogui_size()
        if mss_size == gui_size:
            return
        warning = (
            f"WARNING: screen size mismatch. mss (screenshots) reports "
            f"{mss_size[0]}x{mss_size[1]}, pyautogui (clicks) reports "
            f"{gui_size[0]}x{gui_size[1]}. Coordinates get scaled assuming "
            f"mss's resolution, so clicks will land in the wrong place. Fix: "
            f"set Windows display scaling to 100% for this monitor, then "
            f"restart the app."
        )
        self._log(warning)
        logger.log_note(warning)

    @staticmethod
    def _execute(executor: ActionExecutor, action, screenshot) -> str:
        """Identical dispatch logic to agent.py's _execute()."""
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

    def _finish(self, status: str) -> None:
        def _update() -> None:
            self.run_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.open_runs_btn.configure(state="normal" if self.last_run_dir else "disabled")
            self.status_var.set(status.replace("_", " ").capitalize())
            self.root.deiconify()  # bring the window