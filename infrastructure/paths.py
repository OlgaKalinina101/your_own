"""Where things live on disk, resolved from this file rather than from the CWD.

Two reasons for one module.

**One definition.** ``Path(__file__).resolve().parent.parent.parent / "data" /
"autonomy"`` was written out in five modules. Moving the data directory meant
finding all five.

**Absolute, always.** A couple of paths were relative — ``"data/body"`` and the
Chroma store — so they resolved against the working directory. Started from
anywhere but the repo root, ``get_or_create_collection`` quietly made a new,
empty collection: no error, no warning, just a memory that returned nothing.
A systemd unit without ``WorkingDirectory=`` is enough.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
AUTONOMY_DIR = DATA_DIR / "autonomy"
BODY_ASSETS_DIR = DATA_DIR / "body"

LOGS_DIR = PROJECT_ROOT / "logs"
GENERATED_IMAGES_DIR = PROJECT_ROOT / "generated_images"
USER_UPLOADS_DIR = PROJECT_ROOT / "user_uploads"


def resolve(path: str | Path) -> Path:
    """Make *path* absolute against the project root if it is not already.

    For settings that may reasonably be given either way — a vector store on
    another disk, say — without letting a relative one follow the CWD.
    """
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
