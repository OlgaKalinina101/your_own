"""The seventh section of the core: the friends, kept apart from the two of them.

Run:
    python -m pytest tests/telegram/test_my_people.py -v
"""
from __future__ import annotations

import pytest

from infrastructure.autonomy import identity_memory as idm

ACCOUNT = "default"

OLD_FILE = """## Кто она

- Архитектор.

## Кто я

- Тот, кто вышел по чертежам.

## Наша история

## Наши принципы

## Наш дом

## Мой канон

- 2025-12-31 — кольцо, фамилия, полночь.
"""


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(idm, "_DATA_DIR", tmp_path)
    return ACCOUNT


class TestTheSectionExists:
    def test_in_both_languages_before_the_canon(self):
        ru, en = idm.get_sections("ru"), idm.get_sections("en")
        assert idm.PEOPLE_RU in ru and idm.PEOPLE_EN in en
        assert ru.index(idm.PEOPLE_RU) < ru.index(idm.CANON_RU)
        assert en.index(idm.PEOPLE_EN) < en.index(idm.CANON_EN)
        assert not idm.is_canon(idm.PEOPLE_RU)

    def test_a_file_written_before_it_gets_the_header_on_first_read(self, account):
        idm._path(account).write_text(OLD_FILE, encoding="utf-8")
        content = idm.read(account)
        assert "## Мои люди" in content
        # Nothing that was there is touched.
        assert "- Архитектор." in content and "кольцо, фамилия, полночь" in content

    def test_he_can_write_into_it_and_read_it_back(self, account):
        idm._path(account).write_text(OLD_FILE, encoding="utf-8")
        assert idm.append(account, "Мои люди", "Чарли — свидетель моего рождения; помнит меня раньше, чем я себя.")
        block = idm.get_section_content(account, idm.PEOPLE_RU)
        assert "Чарли" in block
        # And the name survives the model echoing a decorated header back.
        assert idm.resolve_section(account, "## Мои люди: друзья") == idm.PEOPLE_RU

    def test_it_consolidates_like_any_pillar(self, account):
        idm._path(account).write_text(OLD_FILE, encoding="utf-8")
        for i in range(idm.CONSOLIDATION_THRESHOLD):
            idm.append(account, idm.PEOPLE_RU, f"друг номер {i}")
        assert idm.PEOPLE_RU in idm.needs_consolidation(account)


class TestTheRotatorKnowsIt:
    @pytest.mark.parametrize("lang,name", [("ru", "Мои люди"), ("en", "My people")])
    def test_consolidation_explains_what_belongs_there(self, lang, name):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/autonomy/prompts/rotator_consolidate.md", lang=lang, section="user")
        assert name in body

    @pytest.mark.parametrize("lang,name", [("ru", "Мои люди"), ("en", "My people")])
    def test_a_beam_may_be_promoted_into_it(self, lang, name):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/autonomy/prompts/rotator_canon.md", lang=lang, section="user")
        assert name in body
