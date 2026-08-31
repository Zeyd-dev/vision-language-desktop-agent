"""
Tests for the benchmark harness's own aggregation/reporting logic
(benchmarks/run_benchmark.py) using a stubbed run function -- no live
backend or real screen involved. Verifies tasks.json itself is well-formed,
and that pass/fail counting and per-difficulty aggregation are correct,
since a benchmark whose own math is wrong is worse than no benchmark.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._fakes import install_fakes

install_fakes()

from benchmarks.run_benchmark import (  # noqa: E402
    TASKS_FILE_DEFAULT,
    load_tasks,
    run_one_task,
    summarize,
)


class TestTasksFile(unittest.TestCase):
    def test_tasks_file_is_valid_json_list(self):
        with open(TASKS_FILE_DEFAULT, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertIsInstance(tasks, list)
        self.assertGreaterEqual(len(tasks), 10)

    def test_every_task_has_required_fields(self):
        with open(TASKS_FILE_DEFAULT, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        for t in tasks:
            self.assertIn("id", t)
            self.assertIn("difficulty", t)
            self.assertIn("task", t)
            self.assertTrue(t["task"].strip())
            self.assertIn(t["difficulty"], ("easy", "medium", "hard"))

    def test_all_task_ids_unique(self):
        with open(TASKS_FILE_DEFAULT, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        ids = [t["id"] for t in tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_difficulty_filter(self):
        easy_only = load_tasks(TASKS_FILE_DEFAULT, difficulties={"easy"})
        self.assertTrue(all(t["difficulty"] == "easy" for t in easy_only))
        self.assertGreater(len(easy_only), 0)


class TestRunOneTask(unittest.TestCase):
    def test_records_status_and_duration(self):
        task = {"id": "t1", "difficulty": "easy", "task": "do a thing"}
        result = run_one_task(task, run_fn=lambda t: ("done", "all good", "/fake/rundir"))
        self.assertEqual(result["id"], "t1")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["detail"], "all good")
        self.assertIn("duration_seconds", result)
        self.assertGreaterEqual(result["duration_seconds"], 0)


class TestSummarize(unittest.TestCase):
    def test_counts_passed_and_total(self):
        results = [
            {"status": "done", "difficulty": "easy"},
            {"status": "fail", "difficulty": "easy"},
            {"status": "done", "difficulty": "hard"},
        ]
        summary = summarize(results)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["passed"], 2)
        self.assertAlmostEqual(summary["pass_rate"], 2 / 3, places=3)

    def test_per_difficulty_breakdown(self):
        results = [
            {"status": "done", "difficulty": "easy"},
            {"status": "done", "difficulty": "easy"},
            {"status": "iteration_limit", "difficulty": "hard"},
        ]
        summary = summarize(results)
        self.assertEqual(summary["by_difficulty"]["easy"]["passed"], 2)
        self.assertEqual(summary["by_difficulty"]["easy"]["total"], 2)
        self.assertEqual(summary["by_difficulty"]["easy"]["pass_rate"], 1.0)
        self.assertEqual(summary["by_difficulty"]["hard"]["passed"], 0)
        self.assertEqual(summary["by_difficulty"]["hard"]["pass_rate"], 0.0)

    def test_non_done_statuses_never_count_as_passed(self):
        for status in ("fail", "iteration_limit", "time_limit", "killed"):
            summary = summarize([{"status": status, "difficulty": "easy"}])
            self.assertEqual(summary["passed"], 0)

    def test_empty_results_no_crash(self):
        summary = summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["pass_rate"])


if __name__ == "__main__":
    unittest.main()
