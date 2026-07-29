#!/usr/bin/env python3
"""
Local web UI for the Vision-Language Desktop Agent.

Same shared loop as agent.py (CLI) and gui.py (Tkinter), just fronted by a
browser instead of a terminal or a desktop window -- which means it also
works from a phone on the same wifi network, since it's just a web page.

Run it with:
    python webapp/app.py

The console prints a scannable QR code, and the page itself (when viewed on
the PC) also shows a "Connect your phone" card with the same QR code and
the direct link, so you don't need to go find the terminal window either.
After the first phone visit, use the browser's "Add to Home Screen" option
for a one-tap icon next time -- no retyping or re-scanning.

One run at a time, same as gui.py -- there's a single global run state, not
one per browser tab/device.
"""
from __future__ import annotations

import glob
import io
import json
import os
import queue
import re
import socket
import sys
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, Response, jsonify, render_template, request, send_file

from config import DEFAULT_BACKEND, DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_MINUTES, RUNS_DIR
from core import LoopCallbacks, run_agent_loop

app = Flask(__name__)

EXAMPLE_TASKS = [
    "Open a browser and go to YouTube",
    "Open a browser and search for today's weather",
    "Open Notepad and type a short note",
]


def _lan_ip() -> str:
    """Best-effort local network IP, for the phone-accessible URL."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


_PHONE_URL = f"http://{_lan_ip()}:5000"


class RunState:
    """All state for the single active (or most recent) run. One run at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.confirm_request: "queue.Queue[tuple]" = queue.Queue()
        self.confirm_answer: "queue.Queue[bool]" = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.last_run_dir: Optional[str] = None
        self.current_run_dir: Optional[str] = None
        self.pending_confirm: Optional[dict] = None
        self.history: list[str] = []  # so a client connecting mid-run still sees prior lines
        self.friendly_status = "Idle -- type a task and press Run."
        self.step_count = 0
        # The actual outcome of the most recently finished run, kept separate
        # from friendly_status (which is transient, step-by-step commentary).
        # result_kind is one of "done" / "fail" / "killed" / "iteration_limit"
        # / "time_limit", or None if nothing has finished yet / a new run
        # just started. This is what the prominent result banner reads --
        # it survives a page reload, so coming back later still shows the
        # answer instead of just the raw log.
        self.result_kind: Optional[str] = None
        self.result_summary: Optional[str] = None

    def log(self, msg: str) -> None:
        self.history.append(msg)
        self.history = self.history[-500:]
        self.log_queue.put(msg)
        self._update_friendly_status(msg)

    def _update_friendly_status(self, msg: str) -> None:
        # core/loop.py sends the DONE/FAILED/"Run finished" lines with a
        # leading "\n" (for readability in a raw log/terminal). A plain
        # msg.startswith("DONE:") is False for "\nDONE: ...", so those three
        # status lines silently never matched here -- the friendly headline
        # stayed stuck on "Finishing up" forever, even though the task
        # actually completed and the real answer was sitting in the raw
        # technical log the whole time. Strip before matching so the
        # headline actually reflects the outcome.
        stripped = msg.strip()

        m = re.search(r"^\[step (\d+)\] capturing screenshot", stripped)
        if m:
            self.step_count = int(m.group(1))
            self.friendly_status = f"Step {self.step_count}: looking at the screen..."
            return
        m = re.search(r"^\[step \d+\] reasoning: (.+)", stripped)
        if m:
            self.friendly_status = f"Thinking: {m.group(1)}"
            return
        m = re.search(r"^\[step \d+\] action: (\S+)", stripped)
        if m:
            friendly_verbs = {
                "click": "Clicking",
                "double_click": "Double-clicking",
                "type": "Typing",
                "key": "Pressing a key",
                "scroll": "Scrolling",
                "wait": "Waiting a moment",
                "open_url": "Opening a website",
                "launch_app": "Opening an application",
                "done": "Finishing up",
                "fail": "Giving up on this task",
            }
            self.friendly_status = friendly_verbs.get(m.group(1), f"Doing: {m.group(1)}")
            return
        # The single line core/loop.py always sends exactly once at the very
        # end of every run, regardless of how it ended -- the one reliable
        # place to capture the final outcome for the result banner, including
        # paths (killed, iteration_limit, time_limit) that have no earlier
        # DONE:/FAILED: line to hook into.
        m = re.match(r"^Run finished: status=(\S+) detail=(.*)$", stripped)
        if m:
            status_val, detail_val = m.group(1), m.group(2)
            self.result_kind = status_val
            self.result_summary = detail_val or None
            if status_val == "killed":
                self.friendly_status = "Stopped by request."
            elif status_val in ("iteration_limit", "time_limit"):
                self.friendly_status = f"Stopped -- {detail_val}"
            return

        if stripped.startswith("DONE:"):
            detail_val = stripped[len("DONE:"):].strip()
            self.friendly_status = f"Done -- {detail_val}"
            self.result_kind = "done"
            self.result_summary = detail_val
        elif stripped.startswith("FAILED:"):
            detail_val = stripped[len("FAILED:"):].strip()
            self.friendly_status = f"Stopped -- {detail_val}"
            self.result_kind = "fail"
            self.result_summary = detail_val
        elif "confirmation needed" in stripped:
            self.friendly_status = "Waiting for your approval on a sensitive action..."
        elif stripped.startswith("Logging to:"):
            self.current_run_dir = stripped.split("Logging to:", 1)[1].strip()
            self.step_count = 0


state = RunState()


def _confirm(action, matched_keyword: str) -> bool:
    state.pending_confirm = {
        "matched_keyword": matched_keyword,
        "action": action.action,
        "reasoning": action.reasoning,
        "coordinates": action.coordinates,
        "text": action.text,
    }
    state.log(f"[confirmation needed] matched keyword '{matched_keyword}' -- waiting for approval...")
    approved = state.confirm_answer.get()  # blocks the worker thread until answered
    state.pending_confirm = None
    return approved


def _should_stop(step: int) -> Optional[str]:
    return "Stopped from the web UI" if state.stop_event.is_set() else None


def _run_loop(task: str, backend_name: str, max_iterations: int, max_minutes: float) -> None:
    callbacks = LoopCallbacks(log=state.log, confirm=_confirm, should_stop=_should_stop)
    status, detail, run_dir = run_agent_loop(task, backend_name, max_iterations, max_minutes, callbacks)
    state.last_run_dir = run_dir or state.last_run_dir
    state.current_run_dir = None
    state.running = False


@app.route("/")
def index():
    return render_template(
        "index.html",
        default_backend=DEFAULT_BACKEND,
        default_max_iter=DEFAULT_MAX_ITERATIONS,
        default_max_min=DEFAULT_MAX_MINUTES,
        example_tasks=EXAMPLE_TASKS,
    )


@app.route("/api/run", methods=["POST"])
def api_run():
    with state.lock:
        if state.running:
            return jsonify({"error": "A run is already in progress. Stop it first."}), 409

        data = request.get_json(force=True) or {}
        task = (data.get("task") or "").strip()
        if not task:
            return jsonify({"error": "task is required"}), 400
        backend_name = data.get("backend", DEFAULT_BACKEND)
        max_iterations = int(data.get("max_iterations", DEFAULT_MAX_ITERATIONS))
        max_minutes = float(data.get("max_minutes", DEFAULT_MAX_MINUTES))

        state.stop_event.clear()
        state.history = []
        state.pending_confirm = None
        state.friendly_status = "Starting up..."
        state.step_count = 0
        state.result_kind = None
        state.result_summary = None
        while not state.log_queue.empty():
            state.log_queue.get_nowait()
        state.running = True
        state.thread = threading.Thread(
            target=_run_loop, args=(task, backend_name, max_iterations, max_minutes), daemon=True
        )
        state.thread.start()

    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state.stop_event.set()
    state.log("Stop requested -- halting after the current step...")
    if state.pending_confirm is not None:
        state.confirm_answer.put(False)
    return jsonify({"ok": True})


@app.route("/api/confirm", methods=["POST"])
def api_confirm():
    data = request.get_json(force=True) or {}
    approved = bool(data.get("approved", False))
    state.confirm_answer.put(approved)
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    return jsonify({
        "running": state.running,
        "pending_confirm": state.pending_confirm,
        "last_run_dir": state.last_run_dir,
        "friendly_status": state.friendly_status,
        "step_count": state.step_count,
        "result_kind": state.result_kind,
        "result_summary": state.result_summary,
    })


@app.route("/api/stream")
def api_stream():
    def generate():
        for line in list(state.history):
            yield f"data: {json.dumps(line)}\n\n"
        while True:
            try:
                msg = state.log_queue.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/screenshot")
def api_screenshot():
    """Latest screenshot from the active (or most recent) run, so the page can show what the agent sees."""
    run_dir = state.current_run_dir or state.last_run_dir
    if not run_dir:
        return jsonify({"error": "no run yet"}), 404
    shots_dir = os.path.join(run_dir, "screenshots")
    files = sorted(glob.glob(os.path.join(shots_dir, "step_*.jpg")))
    if not files:
        return jsonify({"error": "no screenshots yet"}), 404
    return send_file(files[-1], mimetype="image/jpeg", max_age=0)


@app.route("/api/history")
def api_history():
    """Recent runs (task, status, when) pulled from runs/*/summary.json -- most recent first."""
    if not os.path.isdir(RUNS_DIR):
        return jsonify({"runs": []})
    run_folders = sorted(
        [d for d in glob.glob(os.path.join(RUNS_DIR, "*")) if os.path.isdir(d)],
        reverse=True,
    )[:10]
    runs = []
    for folder in run_folders:
        summary_path = os.path.join(folder, "summary.json")
        if not os.path.isfile(summary_path):
            continue
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            runs.append({
                "task": summary.get("task", ""),
                "status": summary.get("status", "unknown"),
                "finished": summary.get("finished", ""),
                "iterations": summary.get("iterations", 0),
            })
        except Exception:
            continue
    return jsonify({"runs": runs})


@app.route("/api/connect-info")
def api_connect_info():
    """URLs for the 'connect your phone' card -- phone_url is also what /api/qr.png encodes."""
    return jsonify({"local_url": "http://localhost:5000", "phone_url": _PHONE_URL})


@app.route("/api/qr.png")
def api_qr():
    """QR code image pointing at the phone-accessible URL, so the page itself can show it."""
    try:
        import qrcode

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(_PHONE_URL)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#141B49", back_color="#FFFFFF").convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png", max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _print_qr(url: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as e:
        print(f"  (couldn't render a QR code: {e} -- just open the URL above manually)")


def main() -> None:
    print("Vision-Language Desktop Agent -- web UI")
    print(f"  On this PC:      http://localhost:5000")
    print(f"  From your phone: {_PHONE_URL}  (same wifi network required)\n")
    print("Scan this with your phone's camera (or open the 'Connect your phone'")
    print("card on the page itself once it loads on this PC):\n")
    _print_qr(_PHONE_URL)
    print("\nCtrl+C to stop the server.\n")
    app.run(host="0.0.0.0", port=5000, threaded=True)


if __name__ == "__main__":
    main()
