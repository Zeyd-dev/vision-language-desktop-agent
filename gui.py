"""Minimal desktop GUI for the Vision-Language Desktop Agent."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

from config import DEFAULT_BACKEND, DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_MINUTES
from core import LoopCallbacks, run_agent_loop


class AgentGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Vision-Language Desktop Agent")
        root.geometry("760x580")
        root.minsize(600, 420)

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.confirm_request: "queue.Queue[tuple]" = queue.Queue()
        self.confirm_answer: "queue.Queue[bool]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_run_dir: str | None = None

        self._build_widgets()
        self.root.after(100, self._poll)

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

    def _log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _confirm(self, action, matched_keyword: str) -> bool:
        """Blocks the worker thread until the main thread shows a popup and answers."""
        self.confirm_request.put((action, matched_keyword))
        return self.confirm_answer.get()

    def _should_stop(self, step: int) -> Optional[str]:
        return "Stopped from the GUI" if self.stop_event.is_set() else None

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
            os.startfile(self.last_run_dir)

    def _run_loop(self, task: str, backend_name: str, max_iterations: int, max_minutes: float) -> None:
        callbacks = LoopCallbacks(
            log=self._log,
            confirm=self._confirm,
            should_stop=self._should_stop,
            on_minimize=lambda: None,
        )
        status, detail, run_dir = run_agent_loop(task, backend_name, max_iterations, max_minutes, callbacks)
        self.last_run_dir = run_dir or self.last_run_dir
        self._finish(status)

    def _finish(self, status: str) -> None:
        def _update() -> None:
            self.run_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.open_runs_btn.configure(state="normal" if self.last_run_dir else "disabled")
            self.status_var.set(status.replace("_", " ").capitalize())
            self.root.deiconify()
            self.root.lift()

        self.root.after(0, _update)


def main() -> None:
    root = tk.Tk()
    AgentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
