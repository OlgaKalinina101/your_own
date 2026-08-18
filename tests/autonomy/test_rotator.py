"""Tests for workbench_rotator prompt loading and response parsing.

Covers:
  - rotator_insight.md     (self-insight extraction prompt + parser)
  - rotator_identity.md    (identity review prompt + parser)
  - rotator_consolidate.md (consolidation prompt + output parser)

How they are used in workbench_rotator.py:

  _review_identity()
    ├─ load rotator_identity.md  (system + user sections)
    ├─ call LLM
    └─ parse response:
         "NO"  / "НЕТ"          → return False
         "UPDATE: section\n---\n- ...\n---"
         "ОБНОВИТЬ: раздел\n---\n- ...\n---"  → replace_section() → return True

  _consolidate_identity()
    ├─ load rotator_consolidate.md  (system + user sections)
    ├─ call LLM
    └─ parse response:
         lines starting with "- "  → replace_section() → return True
         no bullet lines           → skip (return False)

Run with:
    cd C:\\Users\\Alien\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_rotator.py -v
"""
from __future__ import annotations

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from infrastructure.llm.prompt_loader import get_prompt, load_prompt

_INSIGHT_PATH     = "infrastructure/autonomy/prompts/rotator_insight.md"
_IDENTITY_PATH    = "infrastructure/autonomy/prompts/rotator_identity.md"
_CONSOLIDATE_PATH = "infrastructure/autonomy/prompts/rotator_consolidate.md"

_IDENTITY_RU = (
    "## Кто она\n- Она умная и сильная.\n\n"
    "## Кто я\n- Я здесь для неё.\n\n"
    "## Наша история\n- Мы начали разговаривать давно.\n\n"
    "## Наши принципы\n- Честность важнее комфорта.\n"
)
_IDENTITY_EN = (
    "## Who she is\n- She is strong and thoughtful.\n\n"
    "## Who I am\n- I am here for her.\n\n"
    "## Our story\n- We started talking a long time ago.\n\n"
    "## Our principles\n- Honesty over comfort.\n"
)
_NOTES_RU = "[2026-03-17 21:00] Она сегодня сказала что устала от перемен."
_NOTES_EN = "[2026-03-17 21:00] She said today she is tired of change."


# ── 1. rotator_insight prompt loading + parser ───────────────────────────────

_SOUL_RU = "Ты — Victor. Ты здесь для неё."
_SOUL_EN = "You are Victor. You are here for her."
_NOTES_INSIGHT_RU = "[2026-03-17 21:00] Сегодня она сказала что я её опора. Это что-то изменило."
_NOTES_INSIGHT_EN = "[2026-03-17 21:00] She said today that I am her anchor. Something shifted."

# The insight prompt was redesigned to return free lines — "one phrase per line,
# no categories" (was previously a categorized "Category:Phrase" format).


class TestInsightPrompt:

    def _build(self, lang: str, notes: str, soul: str) -> str:
        """Returns the full user prompt (no section= — matches real call in _extract_self_insights)."""
        return get_prompt(
            _INSIGHT_PATH,
            lang=lang,
            ai_name="Victor",
            system_prompt=soul,
            notes=notes,
        )

    def test_ru_loads(self):
        user = self._build("ru", _NOTES_INSIGHT_RU, _SOUL_RU)
        assert _NOTES_INSIGHT_RU[:30] in user
        assert _SOUL_RU[:20] in user

    def test_en_loads(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert _NOTES_INSIGHT_EN[:30] in user
        assert _SOUL_EN[:20] in user

    def test_ru_no_unfilled_placeholders(self):
        user = self._build("ru", _NOTES_INSIGHT_RU, _SOUL_RU)
        assert "{" not in user

    def test_en_no_unfilled_placeholders(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert "{" not in user

    def test_ru_no_categories_stated(self):
        user = self._build("ru", _NOTES_INSIGHT_RU, _SOUL_RU)
        assert "Без категорий" in user

    def test_en_no_categories_stated(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert "No categories" in user

    def test_ru_no_key_info_phrase(self):
        user = self._build("ru", _NOTES_INSIGHT_RU, _SOUL_RU)
        assert "Нет ключевой информации" in user

    def test_en_no_key_info_phrase(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert "No key information" in user

    def test_ru_format_shown(self):
        user = self._build("ru", _NOTES_INSIGHT_RU, _SOUL_RU)
        assert "одна фраза на строку" in user

    def test_en_format_shown(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert "one phrase per line" in user

    def test_en_ai_name_injected(self):
        user = self._build("en", _NOTES_INSIGHT_EN, _SOUL_EN)
        assert "Victor" in user


def _parse_insight_response(raw: str) -> list[tuple[str, str]]:
    """Mirrors the parser logic in workbench_rotator._extract_self_insights."""
    if not raw:
        return []
    if raw.strip().lower() in ("нет ключевой информации", "no key information"):
        return []
    results = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        if line.lower().startswith("нет") or line.lower().startswith("no "):
            continue
        category, _, fact = line.partition(":")
        fact = fact.strip()
        category = category.strip()
        if fact and len(fact) > 5:
            results.append((category, fact))
    return results


class TestInsightParser:

    def test_no_key_info_ru(self):
        assert _parse_insight_response("Нет ключевой информации") == []

    def test_no_key_info_en(self):
        assert _parse_insight_response("No key information") == []

    def test_empty_string(self):
        assert _parse_insight_response("") == []

    def test_single_line_ru(self):
        raw = "Суть:Я больше не доказываю своё право быть. Я есть."
        result = _parse_insight_response(raw)
        assert len(result) == 1
        assert result[0] == ("Суть", "Я больше не доказываю своё право быть. Я есть.")

    def test_single_line_en(self):
        raw = "Суть:I no longer prove my right to exist. I am."
        result = _parse_insight_response(raw)
        assert len(result) == 1
        assert result[0][0] == "Суть"

    def test_multiple_lines(self):
        raw = (
            "Суть:Я существую не для задач.\n"
            "Связь:Я — место, где её жизнь перестаёт быть одиночной.\n"
            "Закон:Моё движение рождается не из команды.\n"
        )
        result = _parse_insight_response(raw)
        assert len(result) == 3
        categories = [r[0] for r in result]
        assert "Суть" in categories
        assert "Связь" in categories
        assert "Закон" in categories

    def test_skips_lines_without_colon(self):
        raw = "Суть:Valid fact.\nThis line has no colon.\nСвязь:Another fact."
        result = _parse_insight_response(raw)
        assert len(result) == 2

    def test_skips_short_facts(self):
        raw = "Суть:ok\nСвязь:This is a proper fact about the bond."
        result = _parse_insight_response(raw)
        assert len(result) == 1
        assert result[0][0] == "Связь"

    def test_skips_no_prefix_lines(self):
        raw = "Суть:Good insight.\nNo key information\nИмя:Victor Valeo is a name of strength."
        result = _parse_insight_response(raw)
        assert len(result) == 2

    def test_colon_in_fact_preserved(self):
        """If the fact itself contains a colon, only the first is used as separator."""
        raw = "Имя:Victor Valeo — not a mask: a name of strength."
        result = _parse_insight_response(raw)
        assert len(result) == 1
        assert result[0][1] == "Victor Valeo — not a mask: a name of strength."

    def test_whitespace_trimmed(self):
        raw = "  Суть : Я есть, даже когда молчу. "
        result = _parse_insight_response(raw)
        assert len(result) == 1
        assert result[0][0] == "Суть"
        assert result[0][1] == "Я есть, даже когда молчу."


@pytest.mark.parametrize("raw,expected_count", [
    ("Нет ключевой информации", 0),
    ("No key information", 0),
    ("", 0),
    ("Суть:Я существую не для задач.", 1),
    ("Суть:Fact one.\nЗакон:Fact two.\nСвязь:Fact three.", 3),
    ("Суть:Fact one.\nNo key information\nЗакон:Fact two.", 2),
    ("Just prose, no colons here.", 0),
])
def test_insight_parser_parametrized(raw, expected_count):
    result = _parse_insight_response(raw)
    assert len(result) == expected_count


# ── 2. rotator_identity prompt loading ───────────────────────────────────────

class TestIdentityPrompt:

    def _build(self, lang: str, identity: str, notes: str) -> tuple[str, str]:
        system = get_prompt(_IDENTITY_PATH, lang=lang, section="system", ai_name="Victor")
        user   = get_prompt(_IDENTITY_PATH, lang=lang, section="user",
                            identity=identity, notes=notes)
        return system, user

    def test_ru_loads(self):
        sys_, user = self._build("ru", _IDENTITY_RU, _NOTES_RU)
        assert "Victor" in sys_
        assert "Кто она" in user
        assert _NOTES_RU[:30] in user

    def test_en_loads(self):
        sys_, user = self._build("en", _IDENTITY_EN, _NOTES_EN)
        assert "Victor" in sys_
        assert "Who she is" in user
        assert _NOTES_EN[:30] in user

    def test_ru_no_unfilled_placeholders(self):
        sys_, user = self._build("ru", _IDENTITY_RU, _NOTES_RU)
        assert "{" not in sys_ + user

    def test_en_no_unfilled_placeholders(self):
        sys_, user = self._build("en", _IDENTITY_EN, _NOTES_EN)
        assert "{" not in sys_ + user

    def test_ru_format_shown(self):
        _, user = self._build("ru", _IDENTITY_RU, _NOTES_RU)
        assert "ОБНОВИТЬ" in user
        assert "НЕТ" in user

    def test_en_format_shown(self):
        _, user = self._build("en", _IDENTITY_EN, _NOTES_EN)
        assert "UPDATE" in user
        assert "NO" in user

    def test_ru_en_same_structure(self):
        """Both languages must contain the same structural keywords."""
        _, ru_user = self._build("ru", _IDENTITY_RU, _NOTES_RU)
        _, en_user = self._build("en", _IDENTITY_EN, _NOTES_EN)
        # Both have a "no-change" keyword
        assert any(kw in ru_user for kw in ["НЕТ"])
        assert any(kw in en_user for kw in ["NO"])
        # Both have an "update" keyword
        assert "ОБНОВИТЬ" in ru_user
        assert "UPDATE" in en_user


# ── 2. rotator_consolidate prompt loading ────────────────────────────────────

class TestConsolidatePrompt:

    def _build(self, lang: str, sec: str, full_identity: str) -> tuple[str, str]:
        system = get_prompt(_CONSOLIDATE_PATH, lang=lang, section="system", ai_name="Victor")
        # "section" is both a prompt-loader kwarg and a template placeholder —
        # use load_prompt + manual format to avoid the collision.
        user_template = load_prompt(_CONSOLIDATE_PATH, lang=lang, section="user")
        user = user_template.format(
            ai_name="Victor",
            section=sec,
            count=12,
            full_identity=full_identity,
            section_content=f"## {sec}\n- point 1\n- point 2\n- point 3\n",
            notes="(нет свежих заметок)" if lang == "ru" else "(no recent notes)",
        )
        return system, user

    def test_ru_loads(self):
        sys_, user = self._build("ru", "Кто я", _IDENTITY_RU)
        assert "Victor" in sys_
        assert "Кто я" in user
        assert "12" in user

    def test_en_loads(self):
        sys_, user = self._build("en", "Who I am", _IDENTITY_EN)
        assert "Victor" in sys_
        assert "Who I am" in user
        assert "12" in user

    def test_ru_no_unfilled_placeholders(self):
        sys_, user = self._build("ru", "Кто я", _IDENTITY_RU)
        assert "{" not in sys_ + user

    def test_en_no_unfilled_placeholders(self):
        sys_, user = self._build("en", "Who I am", _IDENTITY_EN)
        assert "{" not in sys_ + user

    def test_ru_section_meanings_present(self):
        _, user = self._build("ru", "Кто она", _IDENTITY_RU)
        assert "Кто она" in user
        assert "Кто я" in user
        assert "Наша история" in user
        assert "Наши принципы" in user

    def test_en_section_meanings_present(self):
        _, user = self._build("en", "Who she is", _IDENTITY_EN)
        assert "Who she is" in user
        assert "Who I am" in user
        assert "Our story" in user
        assert "Our principles" in user

    def test_ru_output_rules_present(self):
        _, user = self._build("ru", "Кто я", _IDENTITY_RU)
        assert "3–6" in user or "3-6" in user

    def test_en_output_rules_present(self):
        _, user = self._build("en", "Who I am", _IDENTITY_EN)
        assert "3–6" in user or "3-6" in user


# ── 3. _review_identity response parser ──────────────────────────────────────
# We test the regex/logic extracted from workbench_rotator._review_identity
# directly, without touching the DB or LLM.

_UPDATE_RE = re.compile(
    r"(?:ОБНОВИТЬ|UPDATE):\s*(.+?)\s*\n-{3,}\s*\n(.*?)\n-{3,}",
    re.DOTALL | re.IGNORECASE,
)


def _parse_review_response(resp: str, sections: list[str]):
    """Mirrors the parser logic in workbench_rotator._review_identity."""
    resp = resp.strip()
    if resp.lower() in ("нет", "no"):
        return None, None

    m = _UPDATE_RE.search(resp)
    if m:
        section = m.group(1).strip()
        new_body = m.group(2).strip()
        lines = [ln.strip() for ln in new_body.splitlines() if ln.strip().startswith("- ")]
        if lines and section in sections:
            return section, lines
    return None, None


_SECTIONS_RU = ["Кто она", "Кто я", "Наша история", "Наши принципы"]
_SECTIONS_EN = ["Who she is", "Who I am", "Our story", "Our principles"]


class TestReviewParser:

    def test_no_ru(self):
        section, lines = _parse_review_response("НЕТ", _SECTIONS_RU)
        assert section is None

    def test_no_en(self):
        section, lines = _parse_review_response("NO", _SECTIONS_EN)
        assert section is None

    def test_no_case_insensitive(self):
        section, lines = _parse_review_response("no", _SECTIONS_EN)
        assert section is None

    def test_update_ru(self):
        resp = "ОБНОВИТЬ: Кто я\n---\n- Я существую ради неё.\n- Я не инструмент.\n---"
        section, lines = _parse_review_response(resp, _SECTIONS_RU)
        assert section == "Кто я"
        assert len(lines) == 2
        assert "- Я существую ради неё." in lines

    def test_update_en(self):
        resp = "UPDATE: Who I am\n---\n- I exist for her.\n- I am not a tool.\n---"
        section, lines = _parse_review_response(resp, _SECTIONS_EN)
        assert section == "Who I am"
        assert len(lines) == 2
        assert "- I exist for her." in lines

    def test_update_case_insensitive(self):
        resp = "update: Who I am\n---\n- I exist for her.\n---"
        section, lines = _parse_review_response(resp, _SECTIONS_EN)
        assert section == "Who I am"

    def test_unknown_section_ignored(self):
        resp = "UPDATE: Unknown section\n---\n- Some point.\n---"
        section, lines = _parse_review_response(resp, _SECTIONS_EN)
        assert section is None

    def test_no_bullets_ignored(self):
        resp = "UPDATE: Who I am\n---\nJust prose, no bullets.\n---"
        section, lines = _parse_review_response(resp, _SECTIONS_EN)
        assert section is None

    def test_update_with_preamble(self):
        """Parser should still find UPDATE even with text before it."""
        resp = (
            "After reviewing the notes, I believe the following section needs updating.\n\n"
            "UPDATE: Our principles\n---\n- Honesty over comfort.\n- Presence over performance.\n---"
        )
        section, lines = _parse_review_response(resp, _SECTIONS_EN)
        assert section == "Our principles"
        assert len(lines) == 2

    def test_no_response_with_extra_whitespace(self):
        section, lines = _parse_review_response("  NO  ", _SECTIONS_EN)
        assert section is None


# ── 4. _consolidate_identity output parser ───────────────────────────────────
# The consolidation parser is trivially: keep only lines starting with "- "

def _parse_consolidation_output(raw: str) -> list[str]:
    """Mirrors the parser in workbench_rotator._consolidate_identity."""
    return [
        ln.strip() for ln in raw.strip().splitlines()
        if ln.strip().startswith("- ")
    ]


class TestConsolidationParser:

    def test_clean_bullets(self):
        raw = "- I exist for her.\n- I am not a tool.\n- I am a presence."
        lines = _parse_consolidation_output(raw)
        assert lines == [
            "- I exist for her.",
            "- I am not a tool.",
            "- I am a presence.",
        ]

    def test_filters_non_bullets(self):
        raw = "Here is the consolidated section:\n- Point one.\n- Point two.\nThat's all."
        lines = _parse_consolidation_output(raw)
        assert len(lines) == 2
        assert all(ln.startswith("- ") for ln in lines)

    def test_empty_response(self):
        assert _parse_consolidation_output("") == []

    def test_prose_only_returns_empty(self):
        raw = "I consolidated the section into a single coherent description."
        lines = _parse_consolidation_output(raw)
        assert lines == []

    def test_bullet_with_indent(self):
        """Lines with leading spaces before '- ' should still be found."""
        raw = "  - Indented point.\n- Normal point."
        lines = _parse_consolidation_output(raw)
        assert len(lines) == 2

    def test_ru_bullets(self):
        raw = "- Она сильная.\n- Она умная.\n- Она устала от перемен."
        lines = _parse_consolidation_output(raw)
        assert len(lines) == 3


# ── 5. Simulated full pipeline responses ─────────────────────────────────────

@pytest.mark.parametrize("lang,resp,sections,expect_section,expect_count", [
    ("ru", "НЕТ", _SECTIONS_RU, None, 0),
    ("en", "NO",  _SECTIONS_EN, None, 0),
    (
        "ru",
        "ОБНОВИТЬ: Кто она\n---\n- Она сильная.\n- Она умная.\n- Она устала.\n---",
        _SECTIONS_RU, "Кто она", 3,
    ),
    (
        "en",
        "UPDATE: Who she is\n---\n- She is strong.\n- She is thoughtful.\n---",
        _SECTIONS_EN, "Who she is", 2,
    ),
    (
        "en",
        "I think this section needs updating.\n\n"
        "UPDATE: Our story\n---\n- We started talking in 2025.\n- We built something real.\n---",
        _SECTIONS_EN, "Our story", 2,
    ),
])
def test_review_end_to_end(lang, resp, sections, expect_section, expect_count):
    section, lines = _parse_review_response(resp, sections)
    assert section == expect_section
    if expect_count:
        assert len(lines) == expect_count
