"""Tests for the modular skills system.

Covers:
  1. Registry — auto-discovery, get_all, get_enabled, build_prompt, build_open_re.
  2. Prompt assembly — header + per-skill fragments + footer resolve correctly.
  3. Per-skill parsing — each skill's regex detects its commands.
  4. strip_skills — registry version extracts all skills from a mixed response.
  5. Prompt ↔ code parity — every skill in the registry is present in the assembled prompt.
  6. Section loading — continuation prompts, error messages, trailing hint.
  7. Settings-driven enablement — disabling a skill excludes it.

No database, no LLM, no network — pure Python.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_chat_skills.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.skills import registry
from infrastructure.skills.base import SkillBase


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reload_registry():
    """Ensure a clean registry for every test."""
    registry.reload()
    yield
    registry.reload()


@pytest.fixture()
def all_skills() -> list[SkillBase]:
    return registry.get_all()


# ── 1. Registry discovery ─────────────────────────────────────────────────────

class TestRegistryDiscovery:

    def test_discovers_all_five_skills(self, all_skills):
        ids = {s.id for s in all_skills}
        expected = {"search_dialogue", "web_search", "save_memory", "generate_image", "schedule_message"}
        assert ids == expected

    def test_get_skill_by_id(self):
        s = registry.get_skill("generate_image")
        assert s is not None
        assert s.cmd_name == "GENERATE_IMAGE"

    def test_get_skill_nonexistent(self):
        assert registry.get_skill("nonexistent") is None

    def test_each_skill_has_required_attrs(self, all_skills):
        for s in all_skills:
            assert s.id, f"{s} missing id"
            assert s.cmd_name, f"{s.id} missing cmd_name"
            assert s.display.get("en"), f"{s.id} missing display.en"
            assert s.display.get("ru"), f"{s.id} missing display.ru"
            assert s.action_type in ("agentic", "inline", "post"), f"{s.id} bad action_type"
            assert s.parse_re is not None, f"{s.id} missing parse_re"


# ── 2. Prompt assembly ────────────────────────────────────────────────────────

class TestPromptAssembly:

    def _build(self, lang: str) -> str:
        return registry.build_prompt(
            lang=lang,
            now_str="2026-03-18 11:00",
            workbench_block="",
            timezone_label="Asia/Yerevan",
        )

    def test_ru_loads_without_unresolved_placeholders(self):
        text = self._build("ru")
        assert "2026-03-18 11:00" in text
        assert "{" not in text, "Unresolved placeholder in RU assembled prompt"

    def test_en_loads_without_unresolved_placeholders(self):
        text = self._build("en")
        assert "2026-03-18 11:00" in text
        assert "{" not in text, "Unresolved placeholder in EN assembled prompt"

    def test_ru_contains_all_commands(self):
        text = self._build("ru")
        for cmd in ["SEARCH_DIALOGUE", "WEB_SEARCH", "SAVE_MEMORY",
                    "GENERATE_IMAGE", "SCHEDULE_MESSAGE"]:
            assert cmd in text, f"Command {cmd} missing from assembled RU prompt"

    def test_en_contains_all_commands(self):
        text = self._build("en")
        for cmd in ["SEARCH_DIALOGUE", "WEB_SEARCH", "SAVE_MEMORY",
                    "GENERATE_IMAGE", "SCHEDULE_MESSAGE"]:
            assert cmd in text, f"Command {cmd} missing from assembled EN prompt"

    def test_workbench_block_injected(self):
        wb = "Your recent entries from the inner journal:\n[2026-03-18 10:00] She's sleeping.\n\n"
        text = registry.build_prompt(
            lang="en",
            now_str="2026-03-18 11:00",
            workbench_block=wb,
            timezone_label="Asia/Yerevan",
        )
        assert "She's sleeping." in text

    def test_workbench_block_empty(self):
        text = registry.build_prompt(
            lang="ru",
            now_str="2026-03-18 11:00",
            workbench_block="",
            timezone_label="Asia/Yerevan",
        )
        assert "<skills>" in text

    def test_footer_note_present_ru(self):
        text = self._build("ru")
        assert "пометка" in text

    def test_footer_note_present_en(self):
        text = self._build("en")
        assert "note" in text.lower() or "normal" in text.lower()

    def test_disabled_skill_excluded(self):
        """When a skill is excluded from the list, its commands should not appear."""
        all_skills = registry.get_all()
        without_image = [s for s in all_skills if s.id != "generate_image"]
        text = registry.build_prompt(
            lang="en",
            skills=without_image,
            now_str="2026-03-18 11:00",
            workbench_block="",
            timezone_label="Asia/Yerevan",
        )
        assert "GENERATE_IMAGE" not in text
        assert "SEARCH_DIALOGUE" in text


# ── 3. Per-skill regex parsing ────────────────────────────────────────────────

class TestPerSkillParsing:

    @pytest.mark.parametrize("skill_id,sample", [
        ("search_dialogue", "[SEARCH_DIALOGUE: бывший, расставание]"),
        ("web_search", "[WEB_SEARCH: weather Yerevan today]"),
        ("save_memory", "[SAVE_MEMORY: She loves coffee]"),
        ("generate_image", "[GENERATE_IMAGE: gpt5 | a sunrise]"),
        ("schedule_message", "[SCHEDULE_MESSAGE: 2026-03-19 09:00 | Hello]"),
    ])
    def test_skill_regex_matches(self, skill_id, sample):
        s = registry.get_skill(skill_id)
        assert s is not None
        m = s.parse_re.search(sample)
        assert m is not None, f"{skill_id} regex did not match: {sample}"
        assert m.group(1).strip(), f"{skill_id} regex captured empty group"

    @pytest.mark.parametrize("skill_id,variant", [
        ("search_dialogue", "[SEARCH DIALOGUE: test]"),
        ("web_search", "[WEB SEARCH: test]"),
        ("save_memory", "[SAVE MEMORY: test]"),
        ("generate_image", "[GENERATE IMAGE: gpt5 | test]"),
        ("schedule_message", "[SCHEDULE MESSAGE: 2026-03-19 09:00 | test]"),
    ])
    def test_space_variant(self, skill_id, variant):
        s = registry.get_skill(skill_id)
        assert s is not None
        assert s.parse_re.search(variant), f"{skill_id} missed space variant"

    @pytest.mark.parametrize("skill_id,variant", [
        ("save_memory", "[save_memory: lowercase fact]"),
        ("search_dialogue", "[search_dialogue: test]"),
    ])
    def test_case_insensitive(self, skill_id, variant):
        s = registry.get_skill(skill_id)
        assert s is not None
        assert s.parse_re.search(variant), f"{skill_id} missed lowercase variant"

    @pytest.mark.parametrize("legacy", [
        "[SEARCH_MEMORIES: бывший, расставание]",
        "[SEARCH MEMORIES: test]",
        "[search_memories: test]",
    ])
    def test_legacy_search_memories_still_parses(self, legacy):
        """Replies already in the database carry the old command name."""
        s = registry.get_skill("search_dialogue")
        m = s.parse_re.search(legacy)
        assert m is not None
        assert m.group(1).strip()

    def test_legacy_name_triggers_buffering(self):
        """The open regex must catch the old name too, or the raw command leaks."""
        open_re = registry.build_open_re([registry.get_skill("search_dialogue")])
        assert open_re.search("[SEARCH_MEMORIES: test")
        assert open_re.search("[SEARCH_DIALOGUE: test")

    def test_legacy_name_resolves_to_the_dialogue_skill(self):
        """One name, one meaning: SEARCH_MEMORIES is the dialogue store now."""
        matches = registry.parse_all("текст\n[SEARCH_MEMORIES: q]")
        assert [s.id for s, _ in matches] == ["search_dialogue"]


# ── 4. strip_skills (registry version) ───────────────────────────────────────

class TestStripSkills:

    def test_no_skills(self):
        text = "Просто текст без команд."
        clean, matches = registry.strip_skills(text)
        assert clean == text
        assert matches == []

    def test_single_save(self):
        text = "Отвечаю.\n[SAVE_MEMORY: Она боится темноты]"
        clean, matches = registry.strip_skills(text)
        assert clean == "Отвечаю."
        assert len(matches) == 1
        s, m = matches[0]
        assert s.id == "save_memory"
        assert "темноты" in m.group(1)

    def test_multiple_skills(self):
        text = (
            "Текст.\n"
            "[SEARCH_DIALOGUE: test]\n"
            "[SAVE_MEMORY: fact]"
        )
        clean, matches = registry.strip_skills(text)
        assert clean == "Текст."
        ids = [s.id for s, _ in matches]
        assert "search_dialogue" in ids
        assert "save_memory" in ids

    def test_all_five_skills(self):
        text = (
            "Всё сразу.\n"
            "[SEARCH_DIALOGUE: test]\n"
            "[WEB_SEARCH: Yerevan weather]\n"
            "[SAVE_MEMORY: fact]\n"
            "[GENERATE_IMAGE: gpt5 | prompt]\n"
            "[SCHEDULE_MESSAGE: 2026-03-19 09:00 | msg]"
        )
        clean, matches = registry.strip_skills(text)
        assert clean == "Всё сразу."
        ids = {s.id for s, _ in matches}
        assert ids == {"search_dialogue", "web_search", "save_memory", "generate_image", "schedule_message"}

    def test_matches_ordered_by_position(self):
        text = (
            "Text.\n"
            "[SAVE_MEMORY: first]\n"
            "[SEARCH_DIALOGUE: second]\n"
            "[WEB_SEARCH: third]"
        )
        _, matches = registry.strip_skills(text)
        positions = [m.start() for _, m in matches]
        assert positions == sorted(positions)

    def test_multiline_fact(self):
        text = "ok.\n[SAVE_MEMORY: First line\nSecond line]"
        _, matches = registry.strip_skills(text)
        assert len(matches) == 1
        assert "Second line" in matches[0][1].group(1)


# ── 5. build_open_re (buffering trigger) ──────────────────────────────────────

class TestBuildOpenRe:

    @pytest.mark.parametrize("fragment", [
        "[SEARCH_DIALOGUE:",
        "[SEARCH DIALOGUE:",
        "[WEB_SEARCH:",
        "[WEB SEARCH:",
        "[SAVE_MEMORY:",
        "[SAVE MEMORY:",
        "[GENERATE_IMAGE:",
        "[GENERATE IMAGE:",
        "[SCHEDULE_MESSAGE:",
        "[SCHEDULE MESSAGE:",
    ])
    def test_detects_open_bracket(self, fragment):
        open_re = registry.build_open_re()
        assert open_re.search(fragment), f"Not detected: {fragment}"

    def test_does_not_fire_on_plain_text(self):
        open_re = registry.build_open_re()
        assert not open_re.search("Just a regular sentence.")

    def test_does_not_fire_on_result_marker(self):
        open_re = registry.build_open_re()
        assert not open_re.search("[GENERATED_IMAGE: /path/to/img.png]")

    def test_limited_to_enabled_skills(self):
        only_search = [s for s in registry.get_all() if s.id == "search_dialogue"]
        open_re = registry.build_open_re(only_search)
        assert open_re.search("[SEARCH_DIALOGUE: test")
        assert not open_re.search("[WEB_SEARCH: test")


# ── 6. Section loading from per-skill prompts ────────────────────────────────

class TestSectionLoading:

    def test_search_continuation_ru(self):
        s = registry.get_skill("search_dialogue")
        text = s.get_section(
            "search_continuation", "ru",
            brief="Вы говорили об этом вчера.",
            results_block="[вчера] Она: привет",
        )
        assert "вчера" in text
        assert "Говорили" in text or "говорили" in text
        assert "{" not in text

    def test_search_continuation_en(self):
        s = registry.get_skill("search_dialogue")
        text = s.get_section(
            "search_continuation", "en",
            brief="You talked about it yesterday.",
            results_block="[yesterday] Them: hi",
        )
        assert "yesterday" in text
        assert "You talked about it" in text
        assert "{" not in text

    def test_search_empty_ru(self):
        s = registry.get_skill("search_dialogue")
        text = s.get_section("search_empty", "ru", query="бывший парень")
        assert "бывший парень" in text

    def test_search_empty_en(self):
        s = registry.get_skill("search_dialogue")
        text = s.get_section("search_empty", "en", query="old friend")
        assert "old friend" in text

    def test_search_cont_hint(self):
        s = registry.get_skill("search_dialogue")
        text = s.get_section("search_cont_hint", "ru", attempts_left=3)
        assert "3" in text

    def test_web_continuation_ru(self):
        s = registry.get_skill("web_search")
        text = s.get_section(
            "web_continuation", "ru",
            web_query="погода Ереван",
            brief="В Ереване сегодня 24 градуса.",
            sources_block="- Gismeteo (https://example.com)",
        )
        assert "погода Ереван" in text
        assert "24 градуса" in text
        assert "example.com" in text

    def test_web_continuation_en(self):
        s = registry.get_skill("web_search")
        text = s.get_section(
            "web_continuation", "en",
            web_query="weather Yerevan",
            brief="It is 24 degrees in Yerevan today.",
            sources_block="- Gismeteo (https://example.com)",
        )
        assert "weather Yerevan" in text
        assert "24 degrees" in text
        assert "example.com" in text

    def test_web_empty_ru(self):
        s = registry.get_skill("web_search")
        text = s.get_section("web_empty", "ru", web_query="погода Ереван")
        assert "погода Ереван" in text
        assert "{" not in text

    def test_web_empty_en(self):
        s = registry.get_skill("web_search")
        text = s.get_section("web_empty", "en", web_query="weather Yerevan")
        assert "weather Yerevan" in text
        assert "{" not in text

    def test_image_error_ru(self):
        s = registry.get_skill("generate_image")
        text = s.get_section("image_error", "ru")
        assert len(text) > 0
        assert "{" not in text

    def test_image_error_en(self):
        s = registry.get_skill("generate_image")
        text = s.get_section("image_error", "en")
        assert len(text) > 0
        assert "{" not in text

    def test_trailing_hint_ru(self):
        text = registry.get_trailing_hint("ru")
        assert "{" not in text
        assert len(text) > 0

    def test_trailing_hint_en(self):
        text = registry.get_trailing_hint("en")
        assert "{" not in text
        assert len(text) > 0


# ── 7. Cleanup and internal markers regex ─────────────────────────────────────

class TestRegexBuilders:

    def test_internal_markers_re_strips_all(self):
        markers_re = registry.build_internal_markers_re()
        text = (
            "Text before "
            "[SEARCH_DIALOGUE: q] "
            "[GENERATED_IMAGE: /path.png] "
            "[SAVED_FACT: cat | 3 | fact] "
            "text after"
        )
        cleaned = markers_re.sub("", text)
        assert "SEARCH_DIALOGUE" not in cleaned
        assert "GENERATED_IMAGE" not in cleaned
        assert "SAVED_FACT" not in cleaned
        assert "Text before" in cleaned
        assert "text after" in cleaned

    def test_cleanup_re_only_strips_non_persistent(self):
        cleanup_re = registry.build_cleanup_re()
        text = (
            "[SEARCH_DIALOGUE: q]\n"
            "[WEB_SEARCH: q]\n"
            "[GENERATE_IMAGE: gpt5 | p]\n"
            "[SAVE_MEMORY: f]\n"
            "[SCHEDULE_MESSAGE: 2026-01-01 00:00 | m]"
        )
        cleaned = cleanup_re.sub("", text)
        # Search and web should be preserved (persist_in_db=True)
        assert "SEARCH_DIALOGUE" in cleaned
        assert "WEB_SEARCH" in cleaned
        # Image, save, schedule should be stripped (persist_in_db=False)
        assert "GENERATE_IMAGE" not in cleaned
        assert "SAVE_MEMORY" not in cleaned
        assert "SCHEDULE_MESSAGE" not in cleaned


# ── 8. Prompt ↔ code parity ──────────────────────────────────────────────────

class TestPromptCodeParity:

    _ADVERTISED = ["SEARCH_DIALOGUE", "WEB_SEARCH", "SAVE_MEMORY",
                   "GENERATE_IMAGE", "SCHEDULE_MESSAGE"]

    @pytest.mark.parametrize("cmd", _ADVERTISED)
    def test_cmd_in_assembled_ru_prompt(self, cmd):
        text = registry.build_prompt(lang="ru", now_str="2026-03-18 11:00", workbench_block="", timezone_label="Asia/Yerevan")
        assert cmd in text, f"{cmd} missing from assembled RU prompt"

    @pytest.mark.parametrize("cmd", _ADVERTISED)
    def test_cmd_in_assembled_en_prompt(self, cmd):
        text = registry.build_prompt(lang="en", now_str="2026-03-18 11:00", workbench_block="", timezone_label="Asia/Yerevan")
        assert cmd in text, f"{cmd} missing from assembled EN prompt"

    @pytest.mark.parametrize("cmd", _ADVERTISED)
    def test_cmd_parsed_by_registry(self, cmd):
        samples = {
            "SEARCH_DIALOGUE":  f"text.\n[{cmd}: query, detail]",
            "WEB_SEARCH":       f"text.\n[{cmd}: weather today]",
            "SAVE_MEMORY":      f"text.\n[{cmd}: She loves coffee]",
            "GENERATE_IMAGE":   f"text.\n[{cmd}: gpt5 | a sunrise]",
            "SCHEDULE_MESSAGE": f"text.\n[{cmd}: 2026-03-19 09:00 | Hello]",
        }
        text = samples[cmd]
        _, matches = registry.strip_skills(text)
        assert len(matches) == 1, f"{cmd} not matched by registry"

    @pytest.mark.parametrize("cmd", _ADVERTISED)
    def test_cmd_triggers_buffering(self, cmd):
        open_re = registry.build_open_re()
        assert open_re.search(f"[{cmd}:"), f"Buffering regex misses [{cmd}:"


# ── 9. SCHEDULE_MESSAGE payload parsing ───────────────────────────────────────

class TestScheduleMessageParsing:

    @pytest.mark.parametrize("raw,expected_ts,expected_msg", [
        ("2026-03-19 09:00 | Доброе утро", "2026-03-19 09:00", "Доброе утро"),
        ("2026-03-19 22:30 | Good night, sleep well", "2026-03-19 22:30", "Good night, sleep well"),
        (" 2026-03-19 08:00 |  Привет  ", "2026-03-19 08:00", "Привет"),
    ])
    def test_split(self, raw, expected_ts, expected_msg):
        text = f"ответ.\n[SCHEDULE_MESSAGE: {raw}]"
        _, matches = registry.strip_skills(text)
        assert len(matches) == 1
        _, m = matches[0]
        arg = m.group(1).strip()
        assert "|" in arg
        ts_str, msg = arg.split("|", 1)
        assert ts_str.strip() == expected_ts
        assert msg.strip() == expected_msg


# ── 10. Simulated full LLM responses ─────────────────────────────────────────

_CASES = [
    (
        "plain reply, no skills",
        "Привет. Я рад тебя видеть.",
        {},
        "Привет",
    ),
    (
        "save only",
        "Запомню.\n[SAVE_MEMORY: Она переехала в Ереван]",
        {"save_memory": 1},
        "Запомню",
    ),
    (
        "search only",
        "Дай вспомню.\n[SEARCH_DIALOGUE: переезд, Ереван, квартира]",
        {"search_dialogue": 1},
        "вспомню",
    ),
    (
        "web search only",
        "Проверю погоду.\n[WEB_SEARCH: weather Yerevan today]",
        {"web_search": 1},
        "Проверю",
    ),
    (
        "image only",
        "Вот картинка.\n[GENERATE_IMAGE: gemini | stars over Yerevan at night]",
        {"generate_image": 1},
        "картинка",
    ),
    (
        "schedule only",
        "Напишу утром.\n[SCHEDULE_MESSAGE: 2026-03-19 08:00 | Good morning]",
        {"schedule_message": 1},
        "утром",
    ),
    (
        "search + save together",
        "Проверю и запомню.\n[SEARCH_DIALOGUE: job, team]\n[SAVE_MEMORY: She got a promotion]",
        {"search_dialogue": 1, "save_memory": 1},
        "Проверю",
    ),
    (
        "all skills at once",
        (
            "Всё сразу.\n"
            "[SEARCH_DIALOGUE: test]\n"
            "[WEB_SEARCH: Yerevan weather]\n"
            "[SAVE_MEMORY: fact]\n"
            "[GENERATE_IMAGE: gpt5 | prompt]\n"
            "[SCHEDULE_MESSAGE: 2026-03-19 09:00 | msg]"
        ),
        {"search_dialogue": 1, "web_search": 1, "save_memory": 1, "generate_image": 1, "schedule_message": 1},
        "Всё сразу",
    ),
]


@pytest.mark.parametrize("desc,text,counts,clean_fragment", _CASES)
def test_simulated_llm_response(desc, text, counts, clean_fragment):
    clean, matches = registry.strip_skills(text)
    id_counts: dict[str, int] = {}
    for s, _ in matches:
        id_counts[s.id] = id_counts.get(s.id, 0) + 1
    for skill_id, expected in counts.items():
        actual = id_counts.get(skill_id, 0)
        assert actual == expected, f"[{desc}] {skill_id}: expected={expected} actual={actual}"
    total_expected = sum(counts.values())
    assert len(matches) == total_expected, f"[{desc}] total matches"
    if clean_fragment:
        assert clean_fragment in clean, f"[{desc}] clean text missing '{clean_fragment}'"
