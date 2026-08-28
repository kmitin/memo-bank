"""`memobank init` scaffolds a PROJECT, never an engine copy.

The defining property after the engine/instance split: a prepared project holds
only its own config, corpus and templates — nothing engine-shaped — so it cannot
carry a forked engine that ages independently.
"""
import json
from pathlib import Path

import scaffold as sc

ENGINE = Path(__file__).resolve().parents[1]   # repo root


def test_plan_writes_is_config_and_docs_only():
    rels = sc.plan_writes([("umbrella", "."), ("api", "services/api")])
    assert ".island-slices.json" in rels and "AGENTS.md" in rels and "Makefile" in rels
    assert "docs/_templates/spec-template.md" in rels
    assert "docs/specs/schema-frontmatter-v1.md" in rels
    assert "services/api/docs/specs/" in rels
    # nothing engine-shaped is ever written into a project
    assert not any(r.endswith(".py") for r in rels), rels


def test_render_registry_defaults():
    reg = json.loads(sc.render_registry("myproj", [("umbrella", ".")]))
    assert reg["island"] == "myproj"
    assert reg["slices"] == [{"name": "umbrella", "root": "."}]
    assert reg["source_globs"] == ["src/**"]
    assert reg["schema"] == "docs/specs/schema-frontmatter-v1.md"


def test_render_agents_fills_placeholders_and_keeps_both_loops():
    tmpl = (ENGINE / "templates" / "AGENTS.template.md").read_text()
    out = sc.render_agents(tmpl, "MyProj", [("umbrella", "."), ("api", "services/api")])
    assert "REPLACE" not in out
    assert "MyProj" in out and "services/api" in out
    assert "## START HERE — load context before you edit" in out
    assert "Keep docs in sync — drift check" in out


def test_render_mcp_merges_and_uses_the_installed_console_script():
    existing = json.dumps({"mcpServers": {"other": {"command": "x"}}})
    out = json.loads(sc.render_mcp(existing, Path("/t")))
    assert "other" in out["mcpServers"]                       # never clobbered
    assert out["mcpServers"]["memo-bank"]["command"] == "memobank"   # no engine path


def test_makefile_targets_call_the_installed_engine():
    mk = sc.render_makefile()
    for target in ("validate:", "coverage-loop:", "drift-check:", "serve:"):
        assert target in mk
    assert "memobank " in mk
    assert "tools/memo-bank" not in mk                        # no vendored engine path


def test_dry_run_writes_nothing(tmp_path):
    assert sc.run(tmp_path, "p", [("umbrella", ".")], dry_run=True) == 0
    assert list(tmp_path.iterdir()) == []


def test_run_prepares_a_project_without_any_engine_code(tmp_path):
    assert sc.run(tmp_path, "p", [("umbrella", ".")]) == 0
    assert json.loads((tmp_path / ".island-slices.json").read_text())["island"] == "p"
    assert "REPLACE" not in (tmp_path / "AGENTS.md").read_text()
    assert (tmp_path / "docs/specs").is_dir()
    assert (tmp_path / "docs/_templates/spec-template.md").exists()
    assert (tmp_path / "docs/specs/schema-frontmatter-v1.md").exists()
    # the whole point: no engine module lands in the project
    assert not (tmp_path / "memo_bank.py").exists()
    assert not (tmp_path / "tools").exists()
    assert list(tmp_path.rglob("*.py")) == []
