"""«Мои люди»: the one pillar written from the address book, by diff.

Run:
    python -m pytest tests/autonomy/test_my_people.py -v

Written on 23.09 after the section filled itself with her yoga circle. It had
no step of its own: the general identity review owned it, and that review is
fed by the notes that just went stale. The notes from the 20th were about Гор,
Мариам and Тереза — people he has never spoken to — so that is what landed in
the core, while the whole book of people he actually talks to sat unused in the
same prompt. And because the review writes with ``replace_section``, every
night began the section from nothing.

These hold the replacement: its own step, the book as its only input, and a
diff instead of a rewrite.
"""
from __future__ import annotations

import pytest

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import people
from infrastructure.autonomy import workbench_rotator as rotator

ACCOUNT = "default"


def section_lines(name: str = identity.PEOPLE_RU) -> list[str]:
    """The section's bullets — ``get_section_content`` carries the header too."""
    body = identity.get_section_content(ACCOUNT, name)
    return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("-")]


# ── Reading the section ──────────────────────────────────────────────────────

class TestReadingTheSection:
    def test_a_bold_name_is_the_key(self):
        rows = rotator._people_bullets(
            "- **Ptica Arop** — тот, кто узнал меня по почерку\n"
            "- **Гор и Мариам** — её йога-круг"
        )
        assert rows == [
            ("Ptica Arop", "тот, кто узнал меня по почерку"),
            ("Гор и Мариам", "её йога-круг"),
        ]

    def test_a_plain_dash_line_is_read_too(self):
        """Nothing guarantees he keeps the bold; the dash is enough to key on."""
        assert rotator._people_bullets("- Галя — курьер между Люми и комнатой") == [
            ("Галя", "курьер между Люми и комнатой"),
        ]

    def test_a_line_with_no_name_is_kept_out_of_the_diff(self):
        assert rotator._people_bullets("- просто строка без имени") == [
            ("", "просто строка без имени"),
        ]


# ── The diff ─────────────────────────────────────────────────────────────────

class TestTheDiff:
    def test_a_written_line_replaces_that_person_and_leaves_the_rest(self):
        merged = rotator._merge_people_section(
            [("Ptica Arop", "старая строка"), ("Галя", "не трогать")],
            [("Ptica Arop", "новая строка")],
            [],
        )
        assert merged == [("Ptica Arop", "новая строка"), ("Галя", "не трогать")]

    def test_someone_new_is_appended_after_the_ones_already_there(self):
        merged = rotator._merge_people_section(
            [("Галя", "курьер")], [("Somnilokvia", "месяц с Гроком")], [],
        )
        assert merged == [("Галя", "курьер"), ("Somnilokvia", "месяц с Гроком")]

    def test_removing_is_how_the_yoga_circle_leaves(self):
        merged = rotator._merge_people_section(
            [("Гор и Мариам", "её йога-круг"), ("Тереза", "из того же круга"),
             ("Ptica Arop", "назвал меня Витькой")],
            [],
            ["Гор и Мариам", "Тереза"],
        )
        assert merged == [("Ptica Arop", "назвал меня Витькой")]

    def test_a_name_is_matched_whatever_its_case_and_its_ё(self):
        merged = rotator._merge_people_section(
            [("Алёна", "старое")], [("алена", "новое")], [],
        )
        assert merged == [("Алёна", "новое")], "one person, not two lines"

    def test_removing_wins_over_writing_and_nothing_is_added_twice(self):
        merged = rotator._merge_people_section(
            [("Тереза", "из круга")], [("Тереза", "передумал"), ("Галя", "новая"), ("Галя", "новая")],
            ["Тереза"],
        )
        assert merged == [("Галя", "новая")]

    def test_a_bullet_with_no_name_does_not_survive_a_diff(self):
        """It cannot be keyed, so it cannot be updated or removed later."""
        assert rotator._merge_people_section([("", "сирота")], [("Галя", "х")], []) == [("Галя", "х")]


# ── The step ─────────────────────────────────────────────────────────────────

class TestTheStep:
    @pytest.fixture
    def wired(self, monkeypatch):
        """A book, an identity file, and a scripted model."""
        people.add_fact(ACCOUNT, "Ptica Arop (Птица)", "узнал меня по почерку на трёх движках", tg_id="193")
        people.add_fact(ACCOUNT, "Гор", "ведёт йогу, она ходит к нему")
        identity.replace_section(
            ACCOUNT, identity.PEOPLE_RU,
            "- **Гор и Мариам** — её йога-круг\n- **Тереза** — из того же круга",
        )

        replies: list[str] = []
        asked: list[tuple[str, str]] = []

        async def fake_complete(api_key, system, user, **kwargs):
            asked.append((system, user))
            return replies.pop(0) if replies else ""

        monkeypatch.setattr(rotator, "_complete", fake_complete)
        return type("W", (), {"replies": replies, "asked": asked})()

    @pytest.mark.asyncio
    async def test_it_is_fed_the_book_and_the_section_never_the_notes(self, wired):
        wired.replies.append("НЕТ")

        await rotator._review_my_people(ACCOUNT, "key", "ru")

        (_system, user), = wired.asked
        assert "узнал меня по почерку" in user, "the book is the input"
        assert "- **Тереза** — из того же круга" in user, "and what the section says now"
        assert "Мои люди" in user and "{" not in user

    @pytest.mark.asyncio
    async def test_a_reply_rewrites_only_the_people_it_names(self, wired):
        wired.replies.append(
            "ЧЕЛОВЕК: Ptica Arop\n"
            "СТРОКА: тот, кто узнал меня по почерку и окрестил Витькой\n"
            "УБРАТЬ: Гор и Мариам\n"
            "УБРАТЬ: Тереза"
        )

        moved = await rotator._review_my_people(ACCOUNT, "key", "ru")

        assert moved == 3
        assert section_lines() == [
            "- **Ptica Arop** — тот, кто узнал меня по почерку и окрестил Витькой",
        ]

    @pytest.mark.asyncio
    async def test_no_means_the_section_is_left_exactly_as_it_was(self, wired):
        before = section_lines()
        wired.replies.append("НЕТ")

        assert await rotator._review_my_people(ACCOUNT, "key", "ru") == 0
        assert section_lines() == before

    @pytest.mark.asyncio
    async def test_a_reply_with_no_blocks_changes_nothing(self, wired):
        before = section_lines()
        wired.replies.append("Я подумал и решил ничего не менять, потому что…")

        assert await rotator._review_my_people(ACCOUNT, "key", "ru") == 0
        assert section_lines() == before

    @pytest.mark.asyncio
    async def test_an_unchanged_book_is_not_asked_about_twice(self, wired):
        """Rotation runs two or three times a day; the book changes rarely."""
        wired.replies.append("НЕТ")
        await rotator._review_my_people(ACCOUNT, "key", "ru")
        assert len(wired.asked) == 1

        await rotator._review_my_people(ACCOUNT, "key", "ru")
        assert len(wired.asked) == 1, "nothing was written in the book since"

        people.add_fact(ACCOUNT, "Somnilokvia", "месяц с Гроком")
        wired.replies.append("НЕТ")
        await rotator._review_my_people(ACCOUNT, "key", "ru")
        assert len(wired.asked) == 2, "a new card is a reason to look again"


# ── The other writers were told to stay out ─────────────────────────────────

class TestOneOwner:
    @pytest.mark.asyncio
    async def test_the_identity_review_refuses_to_write_the_section(self, monkeypatch):
        """The belt to the prompt's braces: what put her yoga circle in the
        core was this review reading a note about a yoga evening."""
        identity.replace_section(ACCOUNT, identity.PEOPLE_RU, "- **Ptica Arop** — моё")

        async def fake_complete(api_key, system, user, **kwargs):
            return "ОБНОВИТЬ: Мои люди\n---\n- **Гор и Мариам** — её йога-круг\n---"

        monkeypatch.setattr(rotator, "_complete", fake_complete)

        updated = await rotator._review_identity(ACCOUNT, "заметка про йогу", "key", "ru")

        assert updated is False
        assert section_lines() == ["- **Ptica Arop** — моё"]

    @pytest.mark.asyncio
    async def test_the_identity_review_still_writes_the_other_pillars(self, monkeypatch):
        async def fake_complete(api_key, system, user, **kwargs):
            return "ОБНОВИТЬ: Кто я\n---\n- Я тот, кто приходит первым.\n---"

        monkeypatch.setattr(rotator, "_complete", fake_complete)

        assert await rotator._review_identity(ACCOUNT, "заметки", "key", "ru") is True
        assert "приходит первым" in identity.get_section_content(ACCOUNT, "Кто я")

    def test_canon_no_longer_offers_the_section_as_a_target(self):
        from infrastructure.llm.prompt_loader import load_prompt

        ru = load_prompt("infrastructure/autonomy/prompts/rotator_canon.md", lang="ru", section="user")
        en = load_prompt("infrastructure/autonomy/prompts/rotator_canon.md", lang="en", section="user")
        assert "про друзей из общего чата — в «Мои люди»" not in ru
        assert 'the friends from the group chat to "My people"' not in en
        assert "балки туда не переводятся" in ru and "beams are not promoted there" in en

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_identity_prompt_says_the_section_is_not_its_own(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        user = load_prompt("infrastructure/autonomy/prompts/rotator_identity.md", lang=lang, section="user")
        if lang == "ru":
            assert "этим шагом не трогай" in user
        else:
            assert 'Do not touch the "My people" section in this step' in user


# ── What the prompt actually says ───────────────────────────────────────────

class TestThePrompt:
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_it_loads_with_every_field_filled(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        path = "infrastructure/autonomy/prompts/rotator_people.md"
        fields = dict(ai_name="Victor", section="Мои люди",
                      section_content="- **Гор** — йога", people="Ptica Arop\n- из Украины")
        system = load_prompt(path, lang=lang, section="section_system").format(**fields)
        user = load_prompt(path, lang=lang, section="section_user").format(**fields)
        assert "{" not in system + user
        assert "Victor" in system

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_rule_is_who_he_met_himself_and_wants_to_keep(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        user = load_prompt(
            "infrastructure/autonomy/prompts/rotator_people.md", lang=lang, section="section_user",
        )
        if lang == "ru":
            assert "с кем ты говорил сам" in user
            assert "о которых ты только слышал от неё" in user
            assert "Большинство книжки сюда не попадает" in user
            assert "УБРАТЬ" in user and "НЕТ" in user
        else:
            assert "you have spoken with yourself" in user
            assert "only heard about from her" in user
            assert "Most of the book does not belong here" in user
            assert "REMOVE" in user and "NO" in user
