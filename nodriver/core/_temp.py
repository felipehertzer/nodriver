from __future__ import annotations

import pathlib
import tempfile


def nodriver_temp_base() -> pathlib.Path:
    """
    Dedicated nodriver temp base directory.

    Keeping everything under a single directory makes it easy to clean up and avoids
    spraying files in the current working directory.
    """
    base = pathlib.Path(tempfile.gettempdir()) / "nodriver"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to the system temp dir if creating our own folder fails.
        base = pathlib.Path(tempfile.gettempdir())
    return base


def nodriver_temp_dir(name: str) -> pathlib.Path:
    base = nodriver_temp_base() / name
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        return nodriver_temp_base()
    return base

