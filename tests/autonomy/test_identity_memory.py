"""Tests for identity.md section handling.

The file's headers may carry decoration the code does not know about —
"## Наши принципы: Мы — Valeo" for the section "Наши принципы". Everything
here pins down that the suffix is found, preserved, and resolvable:

  1. Locating a section by its bare name, decorated header or not.
  2. append — the bullet lands inside the right block.
  3. replace_section — the header line survives verbatim.
  4. get_section_entry_count / needs_consolidation.
  5. resolve_section — model-written names map back to canonical ones.
  6. No false positives on a name that merely starts the same.

Runs against a temp data dir; the real identity.md is never touched.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_identity_memory.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.autonomy import identity_memory as idm

ACCOUNT = "test_account"

DECORATED = """## Кто она

- она строит

## Кто я

- я дом

## Наша история

- началось с привета

## Наши принципы: Мы — Valeo

- принцип один
- принцип два

## Наш дом

- лавандовые стены
"""


@pytest.fixture
def identity_file(tmp_path, monkeypatch):
    """Point the module at a temp data dir and seed it."""
    monkeypatch.setattr(idm, "_DATA_DIR", tmp_path)

    def _seed(content: str = DECORATED) -> None:
        account_dir = tmp_path / ACCOUNT
        account_dir.mkdir(parents=True, exist_ok=True)
        (account_dir / "identity.md").write_text(content, encoding="utf-8")

    _seed.path = tmp_path / ACCOUNT / "identity.md"  # type: ignore[attr-defined]
    return _seed


ALL_HEADERS = [
    "## Кто она",
    "## Кто я",
    "## Наша история",
    "## Наши принципы: Мы — Valeo",
    "## Наш дом",
    "## Мой канон",      # added by the migration on first read
]


def headers(path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]


# ── Locating ──────────────────────────────────────────────────────────────────

class TestLocate:
    def test_plain_header_is_found(self, identity_file):
        identity_file()
        block = idm.get_section_content(ACCOUNT, "Кто я")
        assert block.startswith("## Кто я")
        assert "я дом" in block
        assert "Наша история" not in block

    def test_decorated_header_is_found(self, identity_file):
        identity_file()
        block = idm.get_section_content(ACCOUNT, "Наши принципы")
        assert block.startswith("## Наши принципы: Мы — Valeo")
        assert "принцип один" in block

    def test_last_section_runs_to_end_of_file(self, identity_file):
        identity_file()
        block = idm.get_section_content(ACCOUNT, "Наш дом")
        assert "лавандовые стены" in block

    def test_missing_section_returns_empty(self, identity_file):
        identity_file()
        assert idm.get_section_content(ACCOUNT, "Чего нет") == ""

    def test_no_false_positive_on_a_longer_name(self):
        # Straight at the helper: read() would migrate a real "Наши принципы"
        # header into the file and hide the case being tested.
        content = "## Наши принципыX\n\n- нет\n"
        assert idm._locate(content, "Наши принципы") is None


# ── Counting ──────────────────────────────────────────────────────────────────

class TestCounts:
    @pytest.mark.parametrize("section,expected", [
        ("Кто она", 1),
        ("Наши принципы", 2),
        ("Наш дом", 1),
        ("Чего нет", 0),
    ])
    def test_entry_count(self, identity_file, section, expected):
        identity_file()
        assert idm.get_section_entry_count(ACCOUNT, section) == expected

    def test_needs_consolidation_uses_the_threshold(self, identity_file):
        bullets = "\n".join(f"- пункт {i}" for i in range(idm.CONSOLIDATION_THRESHOLD))
        identity_file(DECORATED + "\n" + bullets + "\n")
        # The trailing bullets land in the last section, "Наш дом".
        assert "Наш дом" in idm.needs_consolidation(ACCOUNT)
        assert "Кто я" not in idm.needs_consolidation(ACCOUNT)


# ── Writing ───────────────────────────────────────────────────────────────────

class TestAppend:
    def test_bullet_lands_in_the_right_block(self, identity_file):
        identity_file()
        assert idm.append(ACCOUNT, "Кто я", "новая строка") is True

        block = idm.get_section_content(ACCOUNT, "Кто я")
        assert "новая строка" in block
        assert "новая строка" not in idm.get_section_content(ACCOUNT, "Наша история")

    def test_append_under_a_decorated_header(self, identity_file):
        identity_file()
        assert idm.append(ACCOUNT, "Наши принципы", "принцип три") is True
        assert idm.get_section_entry_count(ACCOUNT, "Наши принципы") == 3
        assert headers(identity_file.path) == ALL_HEADERS

    def test_unknown_section_is_refused(self, identity_file):
        identity_file()
        assert idm.append(ACCOUNT, "Чего нет", "текст") is False


class TestReplaceSection:
    def test_decorated_header_survives(self, identity_file):
        identity_file()
        assert idm.replace_section(ACCOUNT, "Наши принципы", "- один\n- два") is True

        assert "## Наши принципы: Мы — Valeo" in headers(identity_file.path)
        assert idm.get_section_entry_count(ACCOUNT, "Наши принципы") == 2
        assert "принцип один" not in identity_file.path.read_text(encoding="utf-8")

    def test_neighbours_are_untouched(self, identity_file):
        identity_file()
        idm.replace_section(ACCOUNT, "Наши принципы", "- один")

        assert "я дом" in idm.get_section_content(ACCOUNT, "Кто я")
        assert "лавандовые стены" in idm.get_section_content(ACCOUNT, "Наш дом")
        assert headers(identity_file.path) == ALL_HEADERS

    def test_unknown_section_is_refused(self, identity_file):
        identity_file()
        assert idm.replace_section(ACCOUNT, "Чего нет", "- один") is False


# ── Resolving model-written names ─────────────────────────────────────────────

class TestResolveSection:
    @pytest.mark.parametrize("written,expected", [
        ("Кто я", "Кто я"),
        ("кто я", "Кто я"),                          # case
        ("  Наш дом  ", "Наш дом"),                  # whitespace
        ("## Наш дом", "Наш дом"),                   # markdown echoed back
        ("Наши принципы", "Наши принципы"),
        ("Наши принципы: Мы — Valeo", "Наши принципы"),  # the decorated header
    ])
    def test_resolves(self, identity_file, written, expected):
        identity_file()
        assert idm.resolve_section(ACCOUNT, written) == expected

    @pytest.mark.parametrize("written", ["", "   ", "Чего нет", "Наши принципыX"])
    def test_rejects(self, identity_file, written):
        identity_file()
        assert idm.resolve_section(ACCOUNT, written) is None

    def test_english_file(self, identity_file):
        identity_file("## Who she is\n\n- a\n\n## Who I am\n\n- b\n")
        assert idm.file_lang(ACCOUNT) == "en"
        assert idm.resolve_section(ACCOUNT, "who i am") == "Who I am"


# ── Prompt parity ─────────────────────────────────────────────────────────────

class TestConsolidatePromptCoversEverySection:
    """The consolidation prompt explains what each pillar means — all of them."""

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_pillar_is_glossed(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        text = load_prompt(
            "infrastructure/autonomy/prompts/rotator_consolidate.md",
            lang=lang, section="user",
        )
        for section in idm.get_sections(lang):
            if idm.is_canon(section):
                continue  # Canon is handled by rotator_canon.md instead
            assert section in text, f"{lang}: section {section!r} missing from the prompt"


# ── Canon ─────────────────────────────────────────────────────────────────────

CANON = idm.CANON_RU


def with_beams(n: int) -> str:
    beams = "\n".join(f"- балка {i}, 2026-0{i % 9 + 1}-01" for i in range(n))
    return DECORATED + f"\n## {CANON}\n\n" + beams + "\n"


class TestCanonSection:
    def test_it_is_a_section(self):
        assert idm.CANON_RU in idm.get_sections("ru")
        assert idm.CANON_EN in idm.get_sections("en")

    @pytest.mark.parametrize("section,expected", [
        ("Мой канон", True),
        ("My canon", True),
        ("Кто я", False),
        ("Our home", False),
        ("Канон", False),      # the former name is migrated, not accepted
    ])
    def test_is_canon(self, section, expected):
        assert idm.is_canon(section) is expected

    def test_canon_section_by_language(self):
        assert idm.canon_section("ru") == "Мой канон"
        assert idm.canon_section("en") == "My canon"

    def test_target_range_matches_the_spec(self):
        assert (idm.CANON_TARGET_MIN, idm.CANON_TARGET_MAX) == (15, 20)

    def test_entries_are_read_without_the_bullet_marker(self, identity_file):
        identity_file(with_beams(3))
        entries = idm.canon_entries(ACCOUNT)
        assert len(entries) == 3
        assert entries[0].startswith("балка 0")
        assert not entries[0].startswith("- ")

    def test_beams_can_be_appended(self, identity_file):
        identity_file()
        assert idm.append(ACCOUNT, CANON, "Кольцо. 2026-08-23 — два камня, двое.") is True
        assert idm.canon_entries(ACCOUNT) == ["Кольцо. 2026-08-23 — два камня, двое."]


class TestCanonMigration:
    def test_missing_section_is_added_on_read(self, identity_file):
        identity_file()  # DECORATED has the five older pillars only
        assert f"## {CANON}" not in identity_file.path.read_text(encoding="utf-8")

        idm.read(ACCOUNT)

        assert f"## {CANON}" in identity_file.path.read_text(encoding="utf-8")
        assert idm.canon_entries(ACCOUNT) == []

    def test_former_name_is_renamed_keeping_its_beams(self, identity_file):
        identity_file(DECORATED + "\n## Канон\n\n- балка одна, 2026-08-23\n")

        idm.read(ACCOUNT)

        text = identity_file.path.read_text(encoding="utf-8")
        assert "## Мой канон" in text
        assert "## Канон\n" not in text
        assert idm.canon_entries(ACCOUNT) == ["балка одна, 2026-08-23"]
        assert headers(identity_file.path) == ALL_HEADERS

    def test_english_former_name_is_renamed(self, identity_file):
        identity_file("## Who I am\n\n- b\n\n## Canon\n\n- a beam, 2026-08-23\n")
        idm.read(ACCOUNT)
        text = identity_file.path.read_text(encoding="utf-8")
        assert "## My canon" in text
        assert idm.canon_entries(ACCOUNT) == ["a beam, 2026-08-23"]

    def test_migration_leaves_existing_text_alone(self, identity_file):
        identity_file()
        before = identity_file.path.read_text(encoding="utf-8")
        idm.read(ACCOUNT)
        assert identity_file.path.read_text(encoding="utf-8").startswith(before.rstrip("\n"))

    def test_migration_is_idempotent(self, identity_file):
        identity_file(DECORATED + "\n## Канон\n\n- балка одна, 2026-08-23\n")
        idm.read(ACCOUNT)
        once = identity_file.path.read_text(encoding="utf-8")
        idm.read(ACCOUNT)
        assert identity_file.path.read_text(encoding="utf-8") == once


class TestCanonThresholds:
    def test_threshold_is_its_own(self):
        assert idm.consolidation_threshold(CANON) == idm.CANON_TARGET_MAX + 1
        assert idm.consolidation_threshold("Кто я") == idm.CONSOLIDATION_THRESHOLD
        # The whole point: Canon must outlive the threshold that crushes the
        # other pillars, or 10 dated beams get compressed into 3-6 undated ones.
        assert idm.consolidation_threshold(CANON) > idm.CONSOLIDATION_THRESHOLD

    def test_canon_never_goes_through_consolidation(self, identity_file):
        identity_file(with_beams(idm.CANON_TARGET_MAX + 5))
        assert CANON not in idm.needs_consolidation(ACCOUNT)

    def test_no_promotion_at_the_ceiling(self, identity_file):
        identity_file(with_beams(idm.CANON_TARGET_MAX))
        assert idm.needs_promotion(ACCOUNT) is False

    def test_promotion_once_it_outgrows_the_ceiling(self, identity_file):
        identity_file(with_beams(idm.CANON_TARGET_MAX + 1))
        assert idm.needs_promotion(ACCOUNT) is True


class TestPromoteBeam:
    """A beam moves into a pillar; it is never simply dropped."""

    def test_beam_leaves_canon_and_lands_in_the_pillar(self, identity_file):
        identity_file(with_beams(3))

        assert idm.promote_beam(
            ACCOUNT,
            beam="балка 1, 2026-02-01",
            target_section="Кто я",
            pillar_text="Я тот, кто остаётся.",
        ) is True

        assert idm.canon_entries(ACCOUNT) == ["балка 0, 2026-01-01", "балка 2, 2026-03-01"]
        assert "Я тот, кто остаётся." in idm.get_section_content(ACCOUNT, "Кто я")
        assert "балка 1" not in identity_file.path.read_text(encoding="utf-8")

    def test_it_works_into_a_decorated_header(self, identity_file):
        identity_file(with_beams(2))

        assert idm.promote_beam(
            ACCOUNT, beam="балка 0, 2026-01-01",
            target_section="Наши принципы", pillar_text="Мы держим слово.",
        ) is True

        assert "Мы держим слово." in idm.get_section_content(ACCOUNT, "Наши принципы")
        assert headers(identity_file.path) == ALL_HEADERS

    def test_the_beam_text_need_not_match_exactly(self, identity_file):
        identity_file(with_beams(2))
        # The model tends to echo the bullet marker and reflow whitespace.
        assert idm.promote_beam(
            ACCOUNT, beam="-  балка 0,   2026-01-01 ",
            target_section="Кто я", pillar_text="Столп.",
        ) is True
        assert len(idm.canon_entries(ACCOUNT)) == 1

    def test_unknown_beam_changes_nothing(self, identity_file):
        identity_file(with_beams(2))
        before = identity_file.path.read_text(encoding="utf-8")

        assert idm.promote_beam(
            ACCOUNT, beam="балки такой нет",
            target_section="Кто я", pillar_text="Столп.",
        ) is False
        assert identity_file.path.read_text(encoding="utf-8") == before

    def test_unknown_target_changes_nothing(self, identity_file):
        identity_file(with_beams(2))
        before = identity_file.path.read_text(encoding="utf-8")

        assert idm.promote_beam(
            ACCOUNT, beam="балка 0, 2026-01-01",
            target_section="Чего нет", pillar_text="Столп.",
        ) is False
        assert identity_file.path.read_text(encoding="utf-8") == before

    def test_canon_cannot_be_its_own_target(self, identity_file):
        identity_file(with_beams(2))
        assert idm.promote_beam(
            ACCOUNT, beam="балка 0, 2026-01-01",
            target_section=CANON, pillar_text="Столп.",
        ) is False
        assert len(idm.canon_entries(ACCOUNT)) == 2

    def test_empty_pillar_text_is_refused(self, identity_file):
        identity_file(with_beams(2))
        assert idm.promote_beam(
            ACCOUNT, beam="балка 0, 2026-01-01",
            target_section="Кто я", pillar_text="   ",
        ) is False
        assert len(idm.canon_entries(ACCOUNT)) == 2

    def test_promoting_down_to_the_target_range(self, identity_file):
        identity_file(with_beams(idm.CANON_TARGET_MAX + 1))
        assert idm.needs_promotion(ACCOUNT) is True

        idm.promote_beam(
            ACCOUNT, beam="балка 0, 2026-01-01",
            target_section="Наша история", pillar_text="С этого всё началось.",
        )

        assert len(idm.canon_entries(ACCOUNT)) == idm.CANON_TARGET_MAX
        assert idm.needs_promotion(ACCOUNT) is False


class TestCanonBlock:
    """The one part of the core that loads into every chat."""

    def test_empty_canon_renders_nothing(self, identity_file):
        identity_file()
        assert idm.canon_block(ACCOUNT) == ""

    def test_beams_are_rendered_with_framing(self, identity_file):
        identity_file(with_beams(2))
        block = idm.canon_block(ACCOUNT, "ru")
        assert "- балка 0, 2026-01-01" in block
        assert "- балка 1, 2026-02-01" in block
        assert "канон" in block.lower()

    def test_english_framing(self, identity_file):
        identity_file("## Who I am\n\n- b\n\n## My canon\n\n- a beam, 2026-08-23\n")
        block = idm.canon_block(ACCOUNT, "en")
        assert "- a beam, 2026-08-23" in block
        assert "canon" in block.lower()

    def test_only_canon_is_rendered(self, identity_file):
        identity_file(with_beams(1))
        block = idm.canon_block(ACCOUNT, "ru")
        assert "лавандовые стены" not in block   # "Наш дом" stays out of chat
        assert "я дом" not in block              # "Кто я" stays out of chat


class TestCanonPrompt:
    """Canon's prompt promotes beams into pillars — it does not trim them."""

    PATH = "infrastructure/autonomy/prompts/rotator_canon.md"

    def _render(self, lang: str, section: str) -> str:
        # load_prompt + .format, like the rotator does — get_prompt's own
        # `section` argument would collide with the {section} placeholder.
        from infrastructure.llm.prompt_loader import load_prompt

        return load_prompt(self.PATH, lang=lang, section=section).format(
            ai_name="Victor",
            section=idm.CANON_RU if lang == "ru" else idm.CANON_EN,
            count=21, full_identity="...", section_content="...", notes="...",
            target_min=idm.CANON_TARGET_MIN, target_max=idm.CANON_TARGET_MAX,
            promote_min=1, promote_max=6,
        )

    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize("section", ["system", "user"])
    def test_renders(self, lang, section):
        text = self._render(lang, section)
        assert "{" not in text
        assert text.strip()

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_promotion_range_is_injected(self, lang):
        text = self._render(lang, "user")
        assert str(idm.CANON_TARGET_MIN) in text
        assert str(idm.CANON_TARGET_MAX) in text

    @pytest.mark.parametrize("lang,marker", [
        ("ru", "ПЕРЕВЕСТИ:"),
        ("ru", "В РАЗДЕЛ:"),
        ("ru", "СТОЛП:"),
        ("en", "PROMOTE:"),
        ("en", "INTO:"),
        ("en", "PILLAR:"),
    ])
    def test_output_format_is_specified(self, lang, marker):
        assert marker in self._render(lang, "user")

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_pillar_is_offered_as_a_target(self, lang):
        text = self._render(lang, "user")
        for section in idm.get_sections(lang):
            if idm.is_canon(section):
                continue
            assert section in text, f"{lang}: {section!r} is not offered as a target"

    def test_ru_says_nothing_is_lost(self):
        text = self._render("ru", "system")
        assert "Ничего не теряется" in text

    def test_en_says_nothing_is_lost(self):
        text = self._render("en", "system")
        assert "Nothing is lost" in text


class TestPromotionParsing:
    """The rotator has to read back what the prompt asks for."""

    def _parse(self, raw: str) -> list[tuple[str, str, str]]:
        from infrastructure.autonomy.workbench_rotator import _PROMOTE_RE

        return [
            (m.group("beam").strip(), m.group("into").strip(), m.group("pillar").strip())
            for m in _PROMOTE_RE.finditer(raw)
        ]

    def test_single_ru_block(self):
        parsed = self._parse(
            "ПЕРЕВЕСТИ: Кольцо. 2026-08-23 — два камня, двое.\n"
            "В РАЗДЕЛ: Наши принципы\n"
            "СТОЛП: Мы выбрали друг друга и носим это не снимая.\n"
        )
        assert parsed == [(
            "Кольцо. 2026-08-23 — два камня, двое.",
            "Наши принципы",
            "Мы выбрали друг друга и носим это не снимая.",
        )]

    def test_single_en_block(self):
        parsed = self._parse(
            "PROMOTE: A ring, 2026-08-23\nINTO: Our principles\nPILLAR: We chose each other.\n"
        )
        assert parsed == [("A ring, 2026-08-23", "Our principles", "We chose each other.")]

    def test_several_blocks(self):
        parsed = self._parse(
            "ПЕРЕВЕСТИ: балка один, 2026-01-01\nВ РАЗДЕЛ: Кто я\nСТОЛП: столп один\n\n"
            "ПЕРЕВЕСТИ: балка два, 2026-02-01\nВ РАЗДЕЛ: Наш дом\nСТОЛП: столп два\n"
        )
        assert [p[0] for p in parsed] == ["балка один, 2026-01-01", "балка два, 2026-02-01"]
        assert [p[2] for p in parsed] == ["столп один", "столп два"]

    @pytest.mark.parametrize("raw", ["НЕТ", "NO", "", "какой-то текст без блоков"])
    def test_nothing_to_parse(self, raw):
        assert self._parse(raw) == []
