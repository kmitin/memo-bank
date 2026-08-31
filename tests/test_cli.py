import importlib

import pytest

import cli


def test_every_subcommand_resolves_to_a_module_with_main():
    assert set(cli.COMMANDS) == {"serve", "init", "validate", "drift", "coverage", "benchmark"}
    for sub, (module_name, help_text) in cli.COMMANDS.items():
        mod = importlib.import_module(module_name)
        assert callable(getattr(mod, "main", None)), f"{sub} -> {module_name}.main missing"
        assert help_text, f"{sub} has no help text"


def test_unknown_subcommand_is_an_error(capsys):
    assert cli.main(["no-such-command"]) == 2
    assert "unknown subcommand" in capsys.readouterr().err


def test_no_args_prints_usage_and_errors(capsys):
    assert cli.main([]) == 2
    err = capsys.readouterr().err
    for sub in cli.COMMANDS:
        assert sub in err


def test_help_lists_subcommands_and_succeeds(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for sub in cli.COMMANDS:
        assert sub in out


def test_dispatch_forwards_argv_to_the_subcommand(monkeypatch):
    seen = {}

    class FakeMod:
        @staticmethod
        def main():
            import sys
            seen["argv"] = list(sys.argv)
            return 0

    monkeypatch.setattr(cli.importlib, "import_module", lambda name: FakeMod)
    assert cli.main(["drift", "--registry", "x.json"]) == 0
    assert seen["argv"][1:] == ["--registry", "x.json"]
    assert "drift" in seen["argv"][0]


def test_missing_registry_reports_clearly_instead_of_a_traceback(capsys, monkeypatch, tmp_path):
    """Run outside a memo-bank project, a command should explain itself."""
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["drift"])
    err = capsys.readouterr().err
    assert rc == 2
    assert ".island-slices.json" in err
    assert "memobank init" in err          # tells the user how to get one
    assert "Traceback" not in err
