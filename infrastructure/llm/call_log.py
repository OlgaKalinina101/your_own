"""Every call to the model, written down in full. A corpus, not a log.

The distinction decides everything here. A log is something you rotate away
once the incident is over; this is the record of how Viktor has actually
spoken, kept whole and kept forever. So:

* It lives under ``data/``, next to the rest of the state, and **not** under
  ``logs/`` — which is the one directory server hygiene deletes without asking:
  logrotate, a cleanup cron, a rebuilt image, an ``rm -rf logs/*`` in the middle
  of debugging something else.
* Nothing is ever deleted or trimmed. Closed months are gzipped, which measured
  4.2x on the existing 208 MB, and that is the only size measure taken.
* No field is truncated. The old writer clipped anything past 100k characters
  and had already done so to 71 rows — a quiet edit to the very thing being
  collected.

One file per month, JSONL, append-only. Read a month with::

    import gzip, json
    with gzip.open("data/dataset/calls-2026-08.jsonl.gz", "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from infrastructure.paths import DATA_DIR, LOGS_DIR

logger = logging.getLogger("call_log")

DATASET_DIR = DATA_DIR / "dataset"

# Where the corpus lived when it was still treated as a log. Migrated once.
_LEGACY_PATH = LOGS_DIR / "debug_dataset.jsonl"

_lock = threading.Lock()


def _segment_path(when: datetime, *, directory: Path | None = None) -> Path:
    return (directory or DATASET_DIR) / f"calls-{when:%Y-%m}.jsonl"


def append(row: dict, *, directory: Path | None = None) -> None:
    """Append one call to the current month's segment.

    Never raises: a failure to record a call must not fail the call.
    """
    try:
        when = datetime.now(timezone.utc)
        path = _segment_path(when, directory=directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception as exc:
        logger.warning("[call_log] could not record a call: %s", exc)


def compress_closed_segments(*, directory: Path | None = None) -> list[Path]:
    """Gzip every segment except the current month's. Returns what was compressed.

    The plain file is removed only after the gzip is written and read back with
    the same number of lines: a corpus is a bad place to trust a rename.
    """
    base = directory or DATASET_DIR
    if not base.is_dir():
        return []

    current = _segment_path(datetime.now(timezone.utc), directory=base).name
    done: list[Path] = []

    for path in sorted(base.glob("calls-*.jsonl")):
        if path.name == current:
            continue
        target = path.with_suffix(".jsonl.gz")
        if target.exists():
            logger.warning("[call_log] %s already exists, leaving %s alone", target.name, path.name)
            continue
        tmp = path.with_suffix(".jsonl.gz.tmp")
        try:
            expected = _count_lines(path)
            before = path.stat().st_size
            with path.open("rb") as src, gzip.open(tmp, "wb", compresslevel=9) as dst:
                shutil.copyfileobj(src, dst)
            if _count_lines(tmp, gzipped=True) != expected:
                raise OSError(f"line count changed while compressing {path.name}")
            os.replace(tmp, target)
            path.unlink()
            done.append(target)
            after = target.stat().st_size
            logger.info(
                "[call_log] compressed %s: %d rows, %.1f MB -> %.1f MB (%.1fx)",
                path.name, expected, before / 1e6, after / 1e6, before / max(after, 1),
            )
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            logger.warning("[call_log] could not compress %s: %s", path.name, exc)

    return done


def _count_lines(path: Path, *, gzipped: bool = False) -> int:
    opener = (lambda: gzip.open(path, "rt", encoding="utf-8")) if gzipped else (
        lambda: path.open("r", encoding="utf-8")
    )
    with opener() as handle:
        return sum(1 for _ in handle)


def migrate_legacy(*, legacy: Path | None = None, directory: Path | None = None) -> int:
    """Split the single old ``logs/debug_dataset.jsonl`` into monthly segments.

    Runs once; the source is removed only after every line has been written out.
    Returns how many rows moved (0 when there is nothing to do).
    """
    source = legacy or _LEGACY_PATH
    if not source.exists():
        return 0

    base = directory or DATASET_DIR
    base.mkdir(parents=True, exist_ok=True)

    handles: dict[str, TextIO] = {}
    moved = 0
    undated = 0
    try:
        with source.open("r", encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    continue
                stamp = _row_month(line)
                if stamp is None:
                    # No usable timestamp: keep it rather than drop it.
                    stamp = "unknown"
                    undated += 1
                if stamp not in handles:
                    name = f"calls-{stamp}.jsonl"
                    handles[stamp] = (base / name).open("a", encoding="utf-8")
                handles[stamp].write(line if line.endswith("\n") else line + "\n")  # type: ignore[union-attr]
                moved += 1
    finally:
        for handle in handles.values():
            handle.close()

    source.unlink()
    logger.info(
        "[call_log] migrated %d rows out of logs/ into %d monthly segments%s",
        moved, len(handles),
        f" ({undated} without a usable timestamp)" if undated else "",
    )
    return moved


def _row_month(line: str) -> str | None:
    try:
        ts = json.loads(line).get("ts")
        return datetime.fromisoformat(ts).strftime("%Y-%m") if ts else None
    except Exception:
        return None
