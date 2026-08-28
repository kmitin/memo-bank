# tools/memo-bank/test_benchmark_tasks.py
from pathlib import Path
import benchmark_time_to_context as b


def test_load_tasks_reads_yaml(tmp_path):
    f = tmp_path / "tasks.yaml"
    f.write_text("tasks:\n  - {subproject: server, path: src/A.java, intent: edit A}\n")
    assert b.load_tasks(f) == [("server", "src/A.java", "edit A")]


def test_load_tasks_none_is_empty():
    assert b.load_tasks(None) == []


def test_run_empty_tasks_does_not_crash():
    # empty task list (e.g. registry without eval.benchmark_tasks) must skip, not divide by zero
    result = b.run(fed=None, tasks=[])
    assert result["total_tasks"] == 0
    assert result["coverage_rate"] == 0.0
    assert result["rows"] == []
