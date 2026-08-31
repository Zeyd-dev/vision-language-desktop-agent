"""Repeatable task benchmark."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

TASKS_FILE_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.json")
RESULTS_DIR_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def load_tasks(tasks_file: str, difficulties: Optional[set] = None) -> list:
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    if difficulties:
        tasks = [t for t in tasks if t.get("difficulty") in difficulties]
    return tasks


def run_one_task(task: dict, run_fn: Callable[[dict], tuple]) -> dict:
    """run_fn(task) -> (status, detail, run_dir), same shape run_agent_loop."""
    start = time.monotonic()
    status, detail, run_dir = run_fn(task)
    duration = time.monotonic() - start
    return {
        "id": task["id"],
        "difficulty": task.get("difficulty", "unknown"),
        "task": task["task"],
        "status": status,
        "detail": detail,
        "duration_seconds": round(duration, 1),
        "run_dir": run_dir,
    }


def summarize(results: list) -> dict:
    """Pass/fail counts overall and per difficulty tier."""
    summary = {"total": len(results), "passed": 0, "by_difficulty": {}}
    for r in results:
        tier = r.get("difficulty", "unknown")
        bucket = summary["by_difficulty"].setdefault(tier, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if r["status"] == "done":
            summary["passed"] += 1
            bucket["passed"] += 1
    summary["pass_rate"] = round(summary["passed"] / summary["total"], 3) if summary["total"] else None
    for bucket in summary["by_difficulty"].values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 3) if bucket["total"] else None
    return summary


def print_summary_table(results: list, summary: dict) -> None:
    print("\n" + "=" * 72)
    print(f"{'ID':<34} {'Difficulty':<10} {'Status':<16} {'Time (s)':<10}")
    print("-" * 72)
    for r in results:
        print(f"{r['id']:<34} {r['difficulty']:<10} {r['status']:<16} {r['duration_seconds']:<10}")
    print("-" * 72)
    if summary["pass_rate"] is not None:
        print(f"Overall: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.0%})")
    else:
        print("Overall: no tasks run")
    for tier, bucket in summary["by_difficulty"].items():
        rate = f"{bucket['pass_rate']:.0%}" if bucket["pass_rate"] is not None else "n/a"
        print(f"  {tier:<10} {bucket['passed']}/{bucket['total']} passed ({rate})")
    print("=" * 72 + "\n")


def save_results(results: list, summary: dict, results_dir: str) -> str:
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(results_dir, f"{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "summary": summary, "results": results},
            f,
            indent=2,
        )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the repeatable task benchmark against a live agent.")
    parser.add_argument("--backend", default="claude", choices=["claude", "gemini"])
    parser.add_argument("--difficulty", default=None, help="Comma-separated subset: easy,medium,hard. Default: all.")
    parser.add_argument("--tasks-file", default=TASKS_FILE_DEFAULT)
    parser.add_argument("--results-dir", default=RESULTS_DIR_DEFAULT)
    parser.add_argument("--max-iterations", type=int, default=25)
    parser.add_argument("--max-minutes", type=float, default=10)
    parser.add_argument(
        "--unsafe-auto-approve-high-risk",
        action="store_true",
        help=(
            "DANGEROUS: automatically approve high-risk actions (send, delete, "
            "purchase, etc.) instead of the safe default, which automatically "
            "DECLINES them. Only use this for tasks you've specifically built "
            "to be safe to fully automate -- never against a real account "
            "unattended."
        ),
    )
    args = parser.parse_args()

    difficulties = {d.strip() for d in args.difficulty.split(",")} if args.difficulty else None
    tasks = load_tasks(args.tasks_file, difficulties)
    if not tasks:
        print("No tasks matched the given difficulty filter.")
        return

    from core.loop import LoopCallbacks, run_agent_loop

    def confirm(action, matched_keyword) -> bool:
        if args.unsafe_auto_approve_high_risk:
            print(f"    [AUTO-APPROVED, unsafe flag set] high-risk action matched '{matched_keyword}'")
            return True
        print(
            f"    [AUTO-DECLINED, safe default] high-risk action matched "
            f"'{matched_keyword}' -- pass --unsafe-auto-approve-high-risk to change this"
        )
        return False

    def run_fn(task: dict):
        return run_agent_loop(
            task=task["task"],
            backend_name=args.backend,
            max_iterations=args.max_iterations,
            max_minutes=args.max_minutes,
            callbacks=LoopCallbacks(
                log=lambda msg: print(f"    {msg}"),
                confirm=confirm,
                should_stop=lambda step: None,
            ),
        )

    results = []
    for task in tasks:
        print(f"\n--- Running: {task['id']} ({task.get('difficulty', '?')}) ---")
        print(f"    Task: {task['task']}")
        result = run_one_task(task, run_fn)
        results.append(result)
        print(f"    -> {result['status']} ({result['duration_seconds']}s)")

    summary = summarize(results)
    print_summary_table(results, summary)
    saved_path = save_results(results, summary, args.results_dir)
    print(f"Results saved to: {saved_path}")


if __name__ == "__main__":
    main()
