"""Logging setup — structured, colored console output."""
from __future__ import annotations

import logging
import sys


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    # Force UTF-8 console output on Windows so logs with Cyrillic/emoji do not
    # explode with cp1251 encoding errors in dev terminals / IDE consoles.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
            # With the date: a timestamp of "03:14:22" is enough while you watch
            # the console and useless the next morning, which is when a gap in
            # the heartbeat or a failed waking actually gets looked at.
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
