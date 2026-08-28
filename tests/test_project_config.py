import json
from pathlib import Path
import project_config as pc


def _write(tmp_path, obj):
    p = tmp_path / ".island-slices.json"
    p.write_text(json.dumps(obj))
    return p


def test_defaults_for_bare_registry(tmp_path):
    (tmp_path / "frontend").mkdir()
    reg = _write(tmp_path, {"island": "x", "version": 1,
                            "slices": [{"name": "frontend", "root": "frontend"}]})
    cfg = pc.load(reg)
    assert cfg.island == "x"
    assert [s.name for s in cfg.slices] == ["frontend"]
    assert cfg.source_globs == ["src/**"]          # default
    assert cfg.benchmark_tasks_path is None
    assert cfg.gold_queries_path is None
    assert cfg.schema_path is None


def test_reads_extended_fields(tmp_path):
    (tmp_path / "frontend").mkdir()
    reg = _write(tmp_path, {
        "island": "x", "version": 1,
        "slices": [{"name": "frontend", "root": "frontend"}],
        "source_globs": ["app/**", "lib/**"],
        "eval": {"benchmark_tasks": "data/tasks.yaml", "gold_queries": "data/gold.yaml"},
        "schema": "docs/specs/schema.md",
    })
    cfg = pc.load(reg)
    assert cfg.source_globs == ["app/**", "lib/**"]
    assert cfg.benchmark_tasks_path == (tmp_path / "data/tasks.yaml").resolve()
    assert cfg.gold_queries_path == (tmp_path / "data/gold.yaml").resolve()
    assert cfg.schema_path == (tmp_path / "docs/specs/schema.md").resolve()
