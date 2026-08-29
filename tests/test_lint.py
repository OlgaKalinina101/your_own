"""The lint rules that caught real bugs, enforced where they will actually run.

Run:
    python -m pytest tests/test_lint.py -v

A pre-commit hook only protects people who ran ``pre-commit install``. This runs
wherever ``pytest`` runs, which in this project is everywhere.

The rule that earns this file: ``F821`` found ``web_search=web_search`` inside
``LLMClient.stream``, where it turned every 429 from OpenRouter into a
NameError — the chat endpoint caught that, logged it, and sent the client a
normal-looking empty answer. 466 unit tests were green at the time. Ruff took a
second.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _ruff() -> list[str] | None:
    """The ruff command, or None if it is not installed."""
    if shutil.which("ruff"):
        return ["ruff"]
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        capture_output=True, cwd=REPO,
    )
    return [sys.executable, "-m", "ruff"] if probe.returncode == 0 else None


@pytest.mark.skipif(_ruff() is None, reason="ruff is not installed")
def test_the_selected_rules_are_clean():
    """Rule selection lives in ruff.toml, so it is one list, not two."""
    result = subprocess.run(
        [*_ruff(), "check", "--output-format", "concise", "."],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, (
        "ruff found something the rules in ruff.toml say must not be here:\n"
        + (result.stdout or result.stderr)
    )


@pytest.mark.skipif(_ruff() is None, reason="ruff is not installed")
def test_the_config_still_selects_the_rules_that_earned_it():
    """Someone widening or narrowing the set should have to mean it."""
    import tomllib

    config = tomllib.loads((REPO / "ruff.toml").read_text(encoding="utf-8"))
    assert set(config["lint"]["select"]) == {"F401", "F811", "F821", "F841"}, (
        "the rule set changed — if that is intended, update this test and say "
        "in ruff.toml which bug the new rule would have caught"
    )
