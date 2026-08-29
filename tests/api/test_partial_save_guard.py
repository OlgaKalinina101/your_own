"""A reply saved after the client hung up must not outlive its own pair.

`_save_partial` runs detached: the request is gone, the embeddings take seconds
of CPU, and only then does it write. `DELETE /api/chat/pair/{pair_id}` can arrive
inside that window. Written unconditionally, the partial landed *after* the
delete and stayed — a half-reply with no question in front of it, in the
transcript and in the vector memory both.

Neither client calls that endpoint any more (both keep what the reader saw
instead of deleting it), so today the window is only reachable by hand. The
guard is here because the endpoint is: it lives exactly as long as the endpoint
does.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from tests.conftest import FakeRepo


@pytest.fixture
def published(monkeypatch) -> list:
    """Record the change-feed notifications a save would send."""
    import api.chat as chat_mod

    calls: list = []
    monkeypatch.setattr(chat_mod, "publish_pairs_changed", lambda **kw: calls.append(kw))
    return calls


class TestSavePartial:
    @pytest.mark.asyncio
    async def test_it_writes_while_the_pair_is_there(self, chat_app, published) -> None:
        import api.chat as chat_mod

        await chat_mod._save_partial(uuid.uuid4(), "default", "половина ответа")

        assert FakeRepo.saved, "the partial reply was not written"
        assert published, "other clients were not told the transcript changed"

    @pytest.mark.asyncio
    async def test_it_writes_nothing_once_the_pair_is_gone(self, chat_app, published) -> None:
        FakeRepo.pair_alive = False

        import api.chat as chat_mod

        await chat_mod._save_partial(uuid.uuid4(), "default", "половина ответа")

        assert FakeRepo.saved == []
        # And no change-feed frame either: telling every open client to go and
        # fetch a pair that does not exist is its own small bug.
        assert published == []

    @pytest.mark.asyncio
    async def test_it_does_not_use_the_unconditional_write(
        self, chat_app, published, monkeypatch
    ) -> None:
        """The guard is only a guard while this is the call it makes."""
        import api.chat as chat_mod

        async def _refuse(_self, _rows) -> None:
            raise AssertionError("_save_partial must not call bulk_save()")

        # Through monkeypatch, not a bare assignment: `del FakeRepo.bulk_save`
        # afterwards removes the class's own method rather than the stand-in,
        # and every later test sharing this fake fails somewhere else entirely.
        monkeypatch.setattr(FakeRepo, "bulk_save", _refuse)
        await chat_mod._save_partial(uuid.uuid4(), "default", "половина ответа")

        assert FakeRepo.saved, "the partial reply was not written"


def _code_of(method) -> str:
    """The method's source with its docstring taken out.

    Without this the assertions below pass on the prose: the docstring of
    `bulk_save_if_pair_exists` explains why `FOR UPDATE` matters, and a test
    reading the whole source therefore stayed green when the clause itself was
    deleted. Found by mutating the code and watching nothing fail.
    """
    source = inspect.getsource(method)
    doc = method.__doc__
    return source.replace(doc, "") if doc else source


class TestTheLockIsWhatMakesItWork:
    def test_the_conditional_write_takes_a_row_lock(self) -> None:
        """`FOR UPDATE` is the guarantee, not decoration.

        A plain existence check reads as sufficient and is not: under READ
        COMMITTED the delete can commit between the SELECT and the INSERT, and
        the orphan comes straight back. This asserts on the source because the
        clause cannot be observed without two live Postgres sessions, and losing
        it would look like a tidy-up.
        """
        from infrastructure.database.repositories.message_repo import MessageRepository

        assert "FOR UPDATE" in _code_of(MessageRepository.bulk_save_if_pair_exists)

    def test_it_does_not_commit_before_deciding(self) -> None:
        """The check and the insert have to be one transaction.

        A commit between them hands the lock back, and the window reopens.
        """
        from infrastructure.database.repositories.message_repo import MessageRepository

        code = _code_of(MessageRepository.bulk_save_if_pair_exists)
        before_insert, _, _ = code.partition("await self._insert(msgs)")
        assert "commit()" not in before_insert
