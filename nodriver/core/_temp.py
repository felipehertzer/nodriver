from __future__ import annotations

import errno
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from typing import Iterable


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


def _iter_chromium_singleton_dirs(tmp_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    """
    Yield Chromium/Chrome "singleton" socket directories in the system temp dir.

    On Linux (and sometimes other POSIX platforms), Chromium creates directories like:
      /tmp/org.chromium.Chromium.<random>/
    containing files such as SingletonSocket/SingletonCookie. These can leak on crashes.
    """
    prefixes = (
        "org.chromium.Chromium.",
        ".org.chromium.Chromium.",
        "com.google.Chrome.",
        ".com.google.Chrome.",
    )
    try:
        with os.scandir(str(tmp_dir)) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name
                if not any(name.startswith(p) for p in prefixes):
                    continue
                p = pathlib.Path(entry.path)
                if (p / "SingletonSocket").exists():
                    yield p
    except Exception:
        return


def _socket_is_listening(sock_path: pathlib.Path, timeout: float = 0.15) -> bool:
    try:
        st = sock_path.stat()
    except FileNotFoundError:
        return False
    except Exception:
        # Unknown state; be conservative and don't delete.
        return True

    # If it's not a unix socket, don't try to interpret it.
    if not stat.S_ISSOCK(st.st_mode):
        return True

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        return True
    except OSError as e:
        if e.errno in (errno.ENOENT, errno.ECONNREFUSED):
            return False
        # Timeout / permission / other errors: treat as active to be safe.
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass


def cleanup_chromium_singleton_dirs(
    *,
    retries: int = 5,
    retry_delay: float = 0.15,
) -> None:
    """
    Best-effort cleanup for stale Chromium/Chrome singleton temp dirs.

    Safe behavior:
    - Only touches dirs in `tempfile.gettempdir()` that match known Chromium/Chrome patterns.
    - Only deletes dirs whose SingletonSocket is not accepting connections after a few retries.
    """
    tmp_dir = pathlib.Path(tempfile.gettempdir())
    for d in _iter_chromium_singleton_dirs(tmp_dir):
        sock_path = d / "SingletonSocket"

        active = False
        for i in range(max(1, retries)):
            if _socket_is_listening(sock_path):
                active = True
                break
            if i < retries - 1:
                time.sleep(retry_delay)
        if active:
            continue

        # Stale: remove directory.
        try:
            if os.name == "posix" and os.path.exists("/bin/rm"):
                subprocess.run(
                    ["/bin/rm", "-rf", str(d)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                shutil.rmtree(str(d), ignore_errors=True)
        except Exception:
            pass
