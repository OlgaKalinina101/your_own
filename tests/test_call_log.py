"""The recorded-call corpus: nothing is ever lost, trimmed or overwritten.

Run:
    python -m pytest tests/test_call_log.py -v

This is the one store in the project with no second copy anywhere: Postgres has
the conversation, Chroma has the facts, and neither has the prompt that produced
them. So the tests here are about preservation, not behaviour.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone


from infrastructure.llm import call_log


def _rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class TestAppend:
    def test_a_call_lands_in_the_current_month(self, tmp_path):
        call_log.append({"ts": "x", "response": "привет"}, directory=tmp_path)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        assert _rows(tmp_path / f"calls-{month}.jsonl") == [{"ts": "x", "response": "привет"}]

    def test_nothing_is_truncated(self, tmp_path):
        # The previous writer clipped any field past 100k characters and had
        # already done so to 71 rows of the real corpus.
        huge = "я" * 500_000
        call_log.append({"response": huge}, directory=tmp_path)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        stored = _rows(tmp_path / f"calls-{month}.jsonl")[0]
        assert stored["response"] == huge

    def test_the_client_records_the_whole_response(self, tmp_path, monkeypatch):
        """Through _append_debug_row, which is where the truncation lived.

        Testing call_log.append alone missed this: re-adding `response[:100_000]`
        in the client left every test here green.
        """
        from infrastructure.llm.client import _append_debug_row

        monkeypatch.setattr(call_log, "DATASET_DIR", tmp_path)
        huge = "я" * 300_000
        _append_debug_row(
            call_type="t", model="m", system=huge, messages=[], response=huge,
        )

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        stored = _rows(tmp_path / f"calls-{month}.jsonl")[0]
        assert stored["response"] == huge
        assert stored["system"] == huge

    def test_a_failure_to_record_never_raises(self, tmp_path, monkeypatch):
        # Recording a call must not be able to fail the call.
        monkeypatch.setattr(
            call_log, "_segment_path", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("nope"))
        )
        call_log.append({"a": 1}, directory=tmp_path)


class TestCompression:
    def _seed(self, tmp_path, month: str, count: int) -> None:
        path = tmp_path / f"calls-{month}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for i in range(count):
                handle.write(json.dumps({"ts": f"{month}-01", "n": i, "pad": "щ" * 200}) + "\n")

    def test_closed_months_are_packed_and_still_readable(self, tmp_path):
        self._seed(tmp_path, "2026-03", 50)
        done = call_log.compress_closed_segments(directory=tmp_path)

        assert [p.name for p in done] == ["calls-2026-03.jsonl.gz"]
        assert not (tmp_path / "calls-2026-03.jsonl").exists()
        rows = _rows(tmp_path / "calls-2026-03.jsonl.gz")
        assert len(rows) == 50 and rows[0]["n"] == 0 and rows[-1]["n"] == 49

    def test_the_current_month_is_left_open(self, tmp_path):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        self._seed(tmp_path, month, 5)
        assert call_log.compress_closed_segments(directory=tmp_path) == []
        assert (tmp_path / f"calls-{month}.jsonl").exists()

    def test_an_existing_archive_is_never_clobbered(self, tmp_path):
        (tmp_path / "calls-2026-03.jsonl.gz").write_bytes(b"precious")
        self._seed(tmp_path, "2026-03", 3)

        assert call_log.compress_closed_segments(directory=tmp_path) == []
        assert (tmp_path / "calls-2026-03.jsonl.gz").read_bytes() == b"precious"
        assert (tmp_path / "calls-2026-03.jsonl").exists(), "the plain file was dropped anyway"

    def test_a_bad_compression_leaves_the_original_alone(self, tmp_path, monkeypatch):
        self._seed(tmp_path, "2026-03", 10)
        # Pretend the gzip came back with a different number of rows.
        real = call_log._count_lines
        monkeypatch.setattr(
            call_log, "_count_lines",
            lambda path, gzipped=False: 0 if gzipped else real(path),
        )

        assert call_log.compress_closed_segments(directory=tmp_path) == []
        assert len(_rows(tmp_path / "calls-2026-03.jsonl")) == 10
        assert not list(tmp_path.glob("*.tmp"))


class TestMigration:
    def test_every_row_moves_and_lands_in_its_month(self, tmp_path):
        legacy = tmp_path / "debug_dataset.jsonl"
        target = tmp_path / "out"
        with legacy.open("w", encoding="utf-8") as handle:
            for month, day in (("2026-03", 19), ("2026-03", 20), ("2026-08", 1)):
                handle.write(json.dumps({"ts": f"{month}-{day:02d}T10:00:00+00:00", "m": month}) + "\n")

        moved = call_log.migrate_legacy(legacy=legacy, directory=target)

        assert moved == 3
        assert not legacy.exists()
        assert len(_rows(target / "calls-2026-03.jsonl")) == 2
        assert len(_rows(target / "calls-2026-08.jsonl")) == 1

    def test_a_row_without_a_timestamp_is_kept_not_dropped(self, tmp_path):
        legacy = tmp_path / "debug_dataset.jsonl"
        target = tmp_path / "out"
        legacy.write_text('{"no_ts": 1}\nnot json at all\n', encoding="utf-8")

        moved = call_log.migrate_legacy(legacy=legacy, directory=target)

        assert moved == 2
        # Counted as raw lines, not parsed rows: one of them is deliberately not
        # JSON, and keeping it verbatim is the point.
        raw = (target / "calls-unknown.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(raw) == 2
        assert "not json at all" in raw, "an unparseable line was thrown away"
        assert '{"no_ts": 1}' in raw

    def test_nothing_happens_twice(self, tmp_path):
        legacy = tmp_path / "debug_dataset.jsonl"
        legacy.write_text('{"ts": "2026-03-19T10:00:00+00:00"}\n', encoding="utf-8")
        target = tmp_path / "out"

        assert call_log.migrate_legacy(legacy=legacy, directory=target) == 1
        assert call_log.migrate_legacy(legacy=legacy, directory=target) == 0
        assert len(_rows(target / "calls-2026-03.jsonl")) == 1


class TestTestsDoNotTouchTheRealCorpus:
    def test_the_autouse_fixture_redirects_the_dataset_dir(self):
        # The guard for the accident this file's history records: a test run
        # appended 64 rows into data/dataset/ before this existed.
        from infrastructure.paths import DATA_DIR

        assert call_log.DATASET_DIR != DATA_DIR / "dataset"

    def test_a_client_call_writes_somewhere_harmless(self):
        from infrastructure.llm.client import _append_debug_row

        _append_debug_row(call_type="test", model="m", messages=[], response="r")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        assert (call_log.DATASET_DIR / f"calls-{month}.jsonl").exists()
