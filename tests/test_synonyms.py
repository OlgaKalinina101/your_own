"""Russian synonym expansion — the layer that goes missing without a sound.

Run:
    python -m pytest tests/test_synonyms.py -v

Two separate things have to hold, and each failed silently on its own:

1. ``ruwordnet`` declares its ORM with pre-2.0 annotations, so under SQLAlchemy
   2.0 the package fails at *import*. Dropping the `sqlalchemy<2.0` pin made the
   whole project uninstallable and turned this layer off; the only sign was one
   warning line in a log nobody reads during a working day.
2. Its 100 MB database is published as a GitHub release, not on PyPI. Without
   it the package imports fine and answers nothing.

In both cases retrieval keeps working — Russian queries just match on lemmas
alone and quietly find less. That is why this is pinned down rather than left to
be noticed.
"""
from __future__ import annotations

import pytest

from infrastructure.memory.focus_point import (
    RUWORDNET_DB,
    FocusPointPipeline,
    _get_ruwordnet,
)

needs_db = pytest.mark.skipif(
    not RUWORDNET_DB.exists(),
    reason=f"{RUWORDNET_DB} is absent — run scripts/setup.js to fetch it",
)


class TestThePackageLoadsAtAll:
    def test_the_shim_gets_it_past_sqlalchemy_2(self):
        from infrastructure.memory.focus_point import _import_ruwordnet

        assert _import_ruwordnet() is not None

    def test_the_shim_puts_sqlalchemy_back_as_it_found_it(self):
        import sqlalchemy.ext.declarative as declarative

        from infrastructure.memory.focus_point import _import_ruwordnet

        before = declarative.declarative_base
        _import_ruwordnet()

        assert declarative.declarative_base is before, (
            "a patched base left in place would change how every other "
            "declarative model in the process is built"
        )

    def test_the_project_still_runs_on_sqlalchemy_2(self):
        import sqlalchemy

        # The shim exists so this stays true. If someone "fixes" the conflict by
        # pinning back below 2.0, the engine's 2.0-only calls go with it.
        assert sqlalchemy.__version__.startswith("2.")


@needs_db
class TestSynonymsActuallyExpand:
    def test_a_word_brings_its_synonyms(self):
        wordnet = _get_ruwordnet()
        assert wordnet is not None

        senses = wordnet.get_senses("фотография")
        names = {
            other.name.lower()
            for sense in senses
            for other in sense.synset.senses
        }

        assert len(names) > 1, "the database answered, but with nothing in it"

    def test_the_pipeline_returns_more_than_the_lemmas(self):
        # Without RuWordNet this question yields two tokens. With it, the same
        # question opens into the words the corpus might actually have used.
        tokens = FocusPointPipeline(language="ru", expand_synonyms=True).extract(
            "что мы говорили про фотографии"
        )

        assert len(set(tokens)) > 10, (
            f"only {len(set(tokens))} tokens — synonym expansion is off"
        )

    def test_expansion_can_be_asked_not_to_happen(self):
        plain = FocusPointPipeline(language="ru", expand_synonyms=False).extract(
            "что мы говорили про фотографии"
        )
        expanded = FocusPointPipeline(language="ru", expand_synonyms=True).extract(
            "что мы говорили про фотографии"
        )

        assert len(set(plain)) < len(set(expanded))


class TestItStaysInstallable:
    """The failure that actually stopped the app: `npm run electron:dev` died at
    pip, before anything ran."""

    def test_ruwordnet_is_not_in_the_resolved_requirements(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        main = (root / "requirements.txt").read_text(encoding="utf-8")
        nodeps = (root / "requirements-nodeps.txt").read_text(encoding="utf-8")

        # In requirements.txt it makes the whole file unresolvable: its metadata
        # says sqlalchemy<2.0 and this project runs on 2.0. pip does not fail
        # partially — nothing installs, and setup exits before the first line of
        # the backend runs.
        lines = [
            ln.strip() for ln in main.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert not any(ln.startswith("ruwordnet") for ln in lines), (
            "ruwordnet belongs in requirements-nodeps.txt — in requirements.txt "
            "it makes `pip install -r` fail outright"
        )
        assert "ruwordnet" in nodeps

    def test_setup_installs_it_without_its_dependencies(self):
        import pathlib

        setup = (
            pathlib.Path(__file__).resolve().parents[1] / "scripts" / "setup.js"
        ).read_text(encoding="utf-8")

        # On the command itself, not just somewhere in the file: the comment
        # above it explains the flag, and a substring test passes on the prose
        # while the install quietly loses it.
        command = [
            line for line in setup.splitlines()
            if "pip install" in line and "requirements-nodeps.txt" in line
        ]
        assert command, "nothing installs requirements-nodeps.txt"
        assert all("--no-deps" in line for line in command), (
            "without --no-deps pip pulls sqlalchemy<2.0 and breaks the backend"
        )
