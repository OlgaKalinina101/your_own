"""What happens to a file between her picker and the model.

Run:
    python -m pytest tests/api/test_attachment_uploads.py -v

An attachment makes two trips through this code. It is saved to disk under a
name of our choosing, and later read back with nothing left of it but that
name — so the name has to carry both what the file is and what she called it.
Every failure here is silent: the wrong extension means the wrong MIME on the
way back, which means the wrong shape to OpenRouter, which means a model
answering about a file it was never handed.
"""
from __future__ import annotations

import pytest

from api.chat import (
    _EXT_BY_MIME,
    _MIME_BY_EXT,
    _content_type_of,
    _original_name_of,
    _safe_stem,
    _save_upload,
)
from infrastructure.llm.client import kind_of


class TestTheKindSurvivesTheRoundTrip:
    """Saved, then read back off disk — does it still know what it is?"""

    @pytest.mark.parametrize(
        "mime",
        ["image/png", "image/jpeg", "application/pdf", "text/plain", "text/markdown",
         "text/csv", "application/json", "audio/mpeg", "audio/wav", "video/mp4",
         "video/quicktime"],
    )
    def test_what_goes_in_is_what_comes_out(self, mime, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"bytes", mime, "thing")
        assert _content_type_of(url.rsplit("/", 1)[-1]) == mime

    @pytest.mark.parametrize(
        "mime",
        ["image/png", "application/pdf", "text/plain", "audio/mpeg", "video/mp4"],
    )
    def test_and_so_does_the_kind_the_client_dispatches_on(self, mime, tmp_path, monkeypatch):
        # This is the one that decides which OpenRouter shape it is sent in.
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"bytes", mime, "thing")
        assert kind_of(_content_type_of(url.rsplit("/", 1)[-1])) == kind_of(mime)

    def test_a_charset_on_the_type_does_not_change_the_extension(self, tmp_path, monkeypatch):
        """Browsers send "text/plain; charset=utf-8". Looked up whole it misses
        the table, and the fallback carves an extension out of the type itself —
        the file lands on disk as ".plainc", reads back as an unknown type, and
        is dropped before it ever reaches a model."""
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"x", "text/plain; charset=utf-8", "n.txt")
        assert url.endswith(".txt")

    def test_an_uppercase_type_finds_the_table_too(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        assert _save_upload(b"x", "IMAGE/PNG", "n").endswith(".png")

    def test_the_canonical_extension_wins_over_its_alias(self):
        """text/plain is listed twice — as .txt and as .log. With last-wins,
        every text file she attaches would be saved as .log."""
        assert _EXT_BY_MIME["text/plain"] == "txt"
        assert _MIME_BY_EXT["log"] == "text/plain"

    def test_both_spellings_of_wav_read_back_as_audio(self):
        assert kind_of(_MIME_BY_EXT["wav"]) == "audio"

    def test_an_unknown_type_does_not_masquerade_as_a_picture(self):
        # It used to default to image/jpeg, which sent a PDF as a photograph.
        assert _content_type_of("mystery.qqq") == "application/octet-stream"
        assert kind_of(_content_type_of("mystery.qqq")) == "other"

    def test_a_file_with_no_extension_at_all(self):
        assert _content_type_of("README") == "application/octet-stream"


class TestTheNameShePickedIsKept:
    """A document is handed to the model under its filename.

    Two attachments both called by their uuid are two walls of text the model
    cannot tell apart, and neither can she when she reads the answer.
    """

    def test_the_stem_survives_the_trip_through_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"x", "text/plain", "meeting notes.txt")
        assert _original_name_of(url.rsplit("/", 1)[-1]) == "meeting_notes.txt"

    def test_two_files_of_the_same_name_do_not_collide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        first = _save_upload(b"one", "text/plain", "notes.txt")
        second = _save_upload(b"two", "text/plain", "notes.txt")
        assert first != second
        assert (tmp_path / first.rsplit("/", 1)[-1]).read_bytes() == b"one"
        assert (tmp_path / second.rsplit("/", 1)[-1]).read_bytes() == b"two"

    def test_a_file_stored_before_names_were_kept_still_reads(self, tmp_path, monkeypatch):
        # There are months of uploads on the server named <uuid>.jpg with no
        # seam in them. They must not come back as a fragment of a uuid.
        assert _original_name_of("a3f9c1d2e4b5.jpg") == "a3f9c1d2e4b5.jpg"

    def test_an_upload_with_no_name_is_saved_anyway(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"x", "image/png", "")
        assert url.endswith(".png")


class TestTheNameCannotSteerTheWrite:
    """The stem is concatenated into a path, so it is reduced, not escaped."""

    @pytest.mark.parametrize(
        "hostile",
        ["../../etc/passwd", "..\\..\\windows\\system32\\config", "/etc/shadow",
         "....//....//evil", "con.txt", "a/b/c.txt"],
    )
    def test_nothing_that_could_leave_the_directory_survives(self, hostile):
        stem = _safe_stem(hostile)
        assert "/" not in stem and "\\" not in stem and ".." not in stem

    @pytest.mark.parametrize(
        "hostile,banned",
        [("report:secret.txt", ":"),        # NTFS alternate data stream
         ("note\x00.txt", "\x00"),          # truncates the path in a C call
         ("we*rd.txt", "*"),                # a glob, if this name ever reaches one
         ("line\nbreak.txt", "\n"),         # splits a log line in two
         ('quo"te.txt', '"')],
    )
    def test_the_characters_that_a_split_on_slashes_would_not_catch(self, hostile, banned):
        """The rsplit above removes separators on its own, so it is not what
        makes these safe — the character filter is, and without a case it does
        not remove, nothing here would notice if it were deleted."""
        assert banned not in _safe_stem(hostile)

    def test_a_name_from_a_folder_keeps_the_file_not_the_folder(self):
        # Both the rsplit and the filter make this safe; only the rsplit makes
        # it readable. Without it the stem is "foldernotes", which is nobody's
        # filename.
        assert _safe_stem("folder\\notes.txt") == "notes"
        assert _safe_stem("folder/notes.txt") == "notes"

    def test_the_write_lands_in_the_uploads_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.chat.USER_UPLOADS_DIR", tmp_path)
        url = _save_upload(b"x", "text/plain", "../../../escape.txt")
        written = list(tmp_path.iterdir())
        assert len(written) == 1
        assert written[0].name == url.rsplit("/", 1)[-1]

    def test_a_very_long_name_is_cut(self):
        assert len(_safe_stem("z" * 500)) <= 40

    def test_cyrillic_is_dropped_rather_than_mangled(self):
        # isalnum() is true for Cyrillic, and the filesystem here takes it; the
        # point of this test is that whatever it does, it stays one path segment.
        stem = _safe_stem("заметки.txt")
        assert "/" not in stem and "\\" not in stem
