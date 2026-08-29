"""One timezone, from the setting, everywhere — and never the system's.

Run:
    python -m pytest tests/test_clock.py -v

The symptom that led here: in a reflection he read four hours of silence where
there had been four minutes. The cause was not the arithmetic — instants stored
in Postgres were always correct — but the rendering. The push validator printed
her last message straight off the row, and rows come back UTC-aware, so a UTC
wall clock sat next to a local "now" in the same prompt.

Underneath that were two functions named ``now_local`` (one tz-aware in the
user's zone, one naive in the *system's*), an alias named ``now_utc`` pointing
at the naive one, and ``.astimezone(user_tz)`` written by hand at nine call
sites. On the machine this was written on the system clock also happened to be
UTC+4, so nothing looked wrong.

These tests use a timezone deliberately far from whatever this machine is set
to, because a test that passes only where the two coincide is the exact test
that was missing.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure import clock

REPO = pathlib.Path(__file__).resolve().parents[1]


def _far_from_the_system_clock() -> str:
    """A zone whose offset is nothing like this machine's.

    Otherwise the test agrees with a system-clock bug by coincidence — which is
    how this one survived five months.
    """
    system_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    for name in ("Pacific/Kiritimati", "Pacific/Auckland", "Pacific/Honolulu", "UTC"):
        from zoneinfo import ZoneInfo

        if datetime.now(ZoneInfo(name)).utcoffset() != system_offset:
            return name
    raise AssertionError("no candidate zone differs from the system clock")


@pytest.fixture
def elsewhere(monkeypatch):
    """Point the single setting at a far-away zone and return its name."""
    name = _far_from_the_system_clock()
    monkeypatch.setattr(clock, "timezone_name", lambda: name)
    return name


class TestTheSingleSource:
    def test_the_zone_comes_from_the_setting(self, monkeypatch):
        import infrastructure.settings_store as settings_store

        monkeypatch.setattr(
            settings_store, "load_settings", lambda: {"user_timezone": "Europe/Lisbon"}
        )
        assert clock.timezone_name() == "Europe/Lisbon"
        assert str(clock.user_tz()) == "Europe/Lisbon"

    def test_a_missing_setting_falls_back_to_the_documented_default(self, monkeypatch):
        import infrastructure.settings_store as settings_store

        monkeypatch.setattr(settings_store, "load_settings", lambda: {})
        assert clock.timezone_name() == clock.DEFAULT_TIMEZONE

    def test_a_nonsense_zone_is_loud_and_falls_back_to_utc(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(clock, "timezone_name", lambda: "Middle/Earth")
        with caplog.at_level(logging.ERROR):
            tz = clock.user_tz()
        assert str(tz) == "UTC"
        assert caplog.records, "a wrong timezone silently wrong is the whole problem"

    def test_local_now_follows_the_setting_not_the_machine(self, elsewhere):
        from zoneinfo import ZoneInfo

        expected = datetime.now(ZoneInfo(elsewhere)).utcoffset()
        assert clock.now_local().utcoffset() == expected
        # And it is genuinely different from what the machine would have said.
        assert clock.now_local().utcoffset() != (
            datetime.now().astimezone().utcoffset()
        )

    def test_local_and_utc_are_the_same_instant(self, elsewhere):
        gap = abs((clock.now_local() - clock.now_utc()).total_seconds())
        assert gap < 2, "now_local and now_utc disagree about when it is"


class TestRenderingAStoredTimestamp:
    """A row's created_at is an instant; showing it must go through the clock."""

    def test_format_local_moves_a_utc_row_into_the_users_zone(self, elsewhere):
        from zoneinfo import ZoneInfo

        row_created_at = datetime(2026, 8, 29, 13, 40, tzinfo=timezone.utc)
        expected = row_created_at.astimezone(ZoneInfo(elsewhere)).strftime(clock.TIME_FMT)

        assert clock.format_local(row_created_at) == expected
        # The bug, stated: strftime on the row prints UTC.
        assert clock.format_local(row_created_at) != row_created_at.strftime(clock.TIME_FMT)

    def test_a_naive_value_is_read_as_utc(self, elsewhere):
        naive = datetime(2026, 8, 29, 13, 40)
        aware = naive.replace(tzinfo=timezone.utc)
        assert clock.format_local(naive) == clock.format_local(aware)

    def test_none_renders_as_the_given_placeholder(self):
        assert clock.format_local(None, empty="неизвестно") == "неизвестно"

    def test_a_local_wall_clock_round_trips(self, elsewhere):
        # He writes [SCHEDULE_MESSAGE: 2026-08-30 09:00] meaning his 09:00.
        written = datetime(2026, 8, 30, 9, 0)
        instant = clock.local_to_utc(written)
        assert clock.format_local(instant) == "2026-08-30 09:00"


class TestThePushValidatorRegression:
    """"Her last message" and "now" must be on the same clock."""

    def test_a_message_sent_a_minute_ago_reads_as_a_minute_ago(self, elsewhere):
        # Exactly the shape of the prompt: a row's created_at next to now_local_str.
        row_created_at = clock.now_utc() - timedelta(minutes=1)

        last_message_time = clock.format_local(row_created_at)
        current_time = clock.now_local_str()

        shown_then = datetime.strptime(last_message_time, clock.TIME_FMT)
        shown_now = datetime.strptime(current_time.split(" (")[0], clock.TIME_FMT)
        drift_hours = abs((shown_now - shown_then).total_seconds()) / 3600

        assert drift_hours < 0.1, (
            f"the prompt shows {drift_hours:.1f}h between a message sent one "
            f"minute ago and now: {last_message_time!r} vs {current_time!r}"
        )

    def test_the_label_matches_the_zone_being_used(self, elsewhere):
        assert elsewhere in clock.label()


class TestNothingReadsTheSystemClock:
    """A source scan, because this is a class of bug rather than one line."""

    SKIP = {"clock.py"}

    def _production_files(self):
        for path in (REPO / "infrastructure").rglob("*.py"):
            if path.name not in self.SKIP:
                yield path
        yield from (REPO / "api").rglob("*.py")
        yield REPO / "main.py"

    def test_no_naive_datetime_now_in_production_code(self):
        """``datetime.now()`` with no argument is the system's clock, not ours."""
        offenders: list[str] = []
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or node.args or node.keywords:
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "now":
                    owner = func.value
                    if isinstance(owner, ast.Name) and owner.id in ("datetime", "dt"):
                        offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

        assert offenders == [], (
            "naive datetime.now() reads the machine's timezone, which is not "
            f"the user's: {offenders}. Use infrastructure.clock."
        )

    def test_only_the_clock_defines_the_timezone(self):
        """``ZoneInfo(...)`` anywhere else is a second source of truth."""
        offenders: list[str] = []
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ZoneInfo"
                ):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

        assert offenders == [], f"timezone built outside the clock: {offenders}"

    def test_there_is_exactly_one_now_local(self):
        """There were two, and they disagreed on a server."""
        definitions: list[str] = []
        for path in self._production_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in (
                    "now_local", "now_utc", "user_tz", "get_user_tz",
                ):
                    definitions.append(f"{path.relative_to(REPO)}:{node.name}")

        assert definitions == [], (
            f"clock functions defined outside infrastructure/clock.py: {definitions}"
        )


class TestOnlyOneModuleKnowsWhereThingsAre:
    """The project root, like the timezone, is a single source or it is several.

    It was computed in four places with three different spellings —
    ``parents[1]``, ``parents[3]``, ``.parent.parent.parent`` — each correct only
    from the file it sat in. infrastructure/paths.py owns it now.
    """

    ALLOWED = {"paths.py"}

    def _production_files(self):
        for directory in ("infrastructure", "api"):
            yield from (REPO / directory).rglob("*.py")
        yield REPO / "main.py"

    def test_nobody_walks_up_to_the_repo_root_by_hand(self):
        offenders: list[str] = []
        for path in self._production_files():
            if path.name in self.ALLOWED:
                continue
            source = path.read_text(encoding="utf-8-sig")
            for number, line in enumerate(source.splitlines(), start=1):
                if "Path(__file__).resolve()" not in line:
                    continue
                tail = line.split("resolve()", 1)[1]
                # One `.parent` is a module finding its own directory — a skill
                # looking for its own prompt file. Two or more, or `parents[N]`,
                # is a module deciding where the repository root is.
                if "parents[" in tail or tail.count(".parent") >= 2:
                    offenders.append(f"{path.relative_to(REPO)}:{number}")

        assert offenders == [], (
            f"project root computed outside paths.py: {offenders}"
        )
