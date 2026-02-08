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


def _posix_ps_command_output() -> str:
    """
    Return a best-effort snapshot of all process command lines.

    Used to avoid deleting temp directories that are actively referenced by a running browser.
    """
    if os.name != "posix":
        return ""
    try:
        # -ww: don't truncate long command lines (important for long temp paths).
        proc = subprocess.run(
            ["ps", "axww", "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return proc.stdout or ""
    except Exception:
        return ""


def _looks_like_chrome_user_data_dir(path: pathlib.Path) -> bool:
    try:
        # Common top-level artifacts in Chrome/Chromium user-data-dir.
        if (path / "Local State").is_file():
            return True
        if (path / "Default").is_dir():
            return True
        if (path / "Last Version").is_file():
            return True
    except Exception:
        pass
    return False


def _is_empty_dir(path: pathlib.Path) -> bool:
    try:
        with os.scandir(str(path)) as it:
            return next(it, None) is None
    except Exception:
        return False


def cleanup_legacy_uc_profile_dirs(
    *,
    min_age_seconds: float = 120.0,
) -> None:
    """
    Clean up stale legacy nodriver temp profiles directly under the system temp dir.

    Older nodriver versions created temp profiles like:
      $TMPDIR/uc_<random>/

    The current code stores profiles under $TMPDIR/nodriver/profiles, but the old
    directories can still exist and accumulate until the disk fills.

    Safety:
    - Only touches direct children of `tempfile.gettempdir()` whose name starts with "uc_".
    - Only deletes directories that look like a Chrome user-data-dir or are empty.
    - Skips directories referenced by a running process command line containing --user-data-dir=<path>.
    - Skips very recent directories to avoid races with a concurrently starting browser.
    """
    tmp_dir = pathlib.Path(tempfile.gettempdir())
    now = time.time()
    ps_out = _posix_ps_command_output()

    try:
        entries = list(os.scandir(str(tmp_dir)))
    except Exception:
        return

    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if not name.startswith("uc_"):
                continue
            path = pathlib.Path(entry.path)

            try:
                st = entry.stat(follow_symlinks=False)
                age = now - float(st.st_mtime)
            except Exception:
                age = min_age_seconds

            if age < min_age_seconds:
                continue

            if not (_is_empty_dir(path) or _looks_like_chrome_user_data_dir(path)):
                continue

            # Skip anything that looks active.
            if ps_out:
                real = os.path.realpath(str(path))
                needles = (
                    f"--user-data-dir={path}",
                    f"--user-data-dir={real}",
                    f"--user-data-dir {path}",
                    f"--user-data-dir {real}",
                )
                if any(n in ps_out for n in needles):
                    continue

            try:
                if os.name == "posix" and os.path.exists("/bin/rm"):
                    subprocess.run(
                        ["/bin/rm", "-rf", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    shutil.rmtree(str(path), ignore_errors=True)
            except Exception:
                pass
        except Exception:
            continue
