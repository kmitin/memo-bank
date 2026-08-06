"""`memobank` — one entry point for the engine's commands.

Packaging exists to stop every adopter forking the engine and ageing
independently (the version drift that bit a second project). Installed, the
engine is reachable as `memobank <subcommand>` from any repo; the flat modules
remain directly runnable (`python .../drift_check.py`) so the git hook and
copy-mode adopters keep working.

Each subcommand delegates to its module's own `main()`, which owns its flags —
this dispatcher only routes, so there is one place to learn what exists.
"""
from __future__ import annotations

import importlib
import sys

# subcommand -> (module, one-line help)
COMMANDS: dict[str, tuple[str, str]] = {
    "serve": ("memo_bank", "run the read-only MCP server over the corpus/federation"),
    "init": ("scaffold", "prepare a project to use the memo-bank (config + docs, no engine copy)"),
    "validate": ("doc_validator", "frontmatter + cross-ref validator; --index regenerates docs/index.json"),
    "drift": ("drift_check", "stale-spec check: governed code changed since last_reviewed"),
    "coverage": ("coverage_loop", "demand-driven coverage loop: uncovered changed code"),
    "benchmark": ("benchmark_time_to_context", "time-to-context / coverage benchmark"),
}


def _usage(stream) -> None:
    print("usage: memobank <command> [options]\n\ncommands:", file=stream)
    width = max(len(c) for c in COMMANDS)
    for sub, (_mod, help_text) in COMMANDS.items():
        print(f"  {sub.ljust(width)}  {help_text}", file=stream)
    print("\nRun `memobank <command> --help` for a command's own options.", file=stream)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    if not argv:
        _usage(sys.stderr)
        return 2

    sub, rest = argv[0], argv[1:]
    if sub not in COMMANDS:
        print(f"memobank: unknown subcommand {sub!r}\n", file=sys.stderr)
        _usage(sys.stderr)
        return 2

    module_name = COMMANDS[sub][0]
    mod = importlib.import_module(module_name)
    # Hand the subcommand its own argv so its argparse usage line reads correctly.
    sys.argv = [f"memobank {sub}", *rest]
    return mod.main()


if __name__ == "__main__":
    raise SystemExit(main())
