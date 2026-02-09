# Copyright 2024 by UltrafunkAmsterdam (https://github.com/UltrafunkAmsterdam)
# All rights reserved.
# This file is part of the nodriver package.
# and is released under the "GNU AFFERO GENERAL PUBLIC LICENSE".
# Please see the LICENSE.txt file that should have been included as part of this package.

import logging
import os
import pathlib
import secrets
import shutil
import sys
import tempfile
import zipfile
from typing import List, Optional, TypeVar

from ._temp import nodriver_temp_dir

__all__ = [
    "Config",
    "find_chrome_executable",
    "temp_profile_dir",
    "is_root",
    "is_posix",
    "PathLike",
]

logger = logging.getLogger(__name__)
is_posix = sys.platform.startswith(("darwin", "cygwin", "linux", "linux2"))

PathLike = TypeVar("PathLike", bound=str | pathlib.Path)
AUTO = None
_UNSET = object()

def _is_executable_file(path: str) -> bool:
    try:
        p = pathlib.Path(path)
        if not p.is_file():
            return False
        # On POSIX, ensure the user can execute the binary (common container footgun).
        if is_posix and not os.access(str(p), os.X_OK):
            return False
        return True
    except Exception:
        return False


class Config:
    """
    Config object
    """

    def __init__(
        self,
        user_data_dir: Optional[PathLike] = AUTO,
        headless: Optional[bool] = False,
        browser_executable_path: Optional[PathLike] = AUTO,
        browser_args: Optional[List[str]] = AUTO,
        sandbox: Optional[bool] = True,
        lang: Optional[str] = "en-US",
        host: str = AUTO,
        port: int = AUTO,
        expert: bool = AUTO,
        **kwargs: dict,
    ):
        """
        creates a config object.
        Can be called without any arguments to generate a best-practice config, which is recommended.

        calling the object, eg :  myconfig() , will return the list of arguments which
        are provided to the browser.

        additional arguments can be added using the :py:obj:`~add_argument method`

        Instances of this class are usually not instantiated by end users.

        :param user_data_dir: the data directory to use
        :param headless: set to True for headless mode
        :param browser_executable_path: specify browser executable, instead of using autodetect
        :param browser_args: forwarded to browser executable. eg : ["--some-chromeparam=somevalue", "some-other-param=someval"]
        :param sandbox: disables sandbox
        :param autodiscover_targets: use autodiscovery of targets
        :param lang: language string to use other than the default "en-US,en;q=0.9"
        :param expert: when set to True, enabled "expert" mode.
               This conveys, the inclusion of parameters:  ----disable-site-isolation-trials,
               as well as some scripts and patching useful for debugging (for example, ensuring shadow-root is always in "open" mode)

        :param kwargs:

        :type user_data_dir: PathLike
        :type headless: bool
        :type browser_executable_path: PathLike
        :type browser_args: list[str]
        :type sandbox: bool
        :type lang: str
        :type kwargs: dict
        """

        # Backwards-compatible alias:
        # Historically the library (and its error messages/docs) referred to `no_sandbox=True`.
        # The actual config knob is `sandbox` (where False adds --no-sandbox).
        no_sandbox = kwargs.pop("no_sandbox", _UNSET)
        if no_sandbox is not _UNSET and no_sandbox is not None:
            sandbox = not bool(no_sandbox)

        if not browser_args:
            browser_args = []

        if not user_data_dir:
            self._user_data_dir = temp_profile_dir()
            self._custom_data_dir = False
        else:
            self.user_data_dir = user_data_dir

        # When attaching to an existing DevTools endpoint (host+port provided),
        # a local Chrome executable is not required.
        if not browser_executable_path and not (host and port):
            # Allow container-friendly configuration via env var as a fallback
            # (useful when the browser isn't on PATH, e.g. Helium in /opt/helium/chrome).
            env_path = (
                os.environ.get("BROWSER_EXECUTABLE_PATH")
                or os.environ.get("CHROME_EXECUTABLE_PATH")
                or os.environ.get("CHROME_PATH")
                or os.environ.get("CHROMIUM_PATH")
                or os.environ.get("BRAVE_EXECUTABLE_PATH")
                or os.environ.get("HELIUM_EXECUTABLE_PATH")
                or ""
            ).strip()
            if env_path and _is_executable_file(env_path):
                browser_executable_path = env_path
            else:
                browser_executable_path = find_chrome_executable()

        self._browser_args = browser_args

        self.browser_executable_path = browser_executable_path
        self.headless = headless
        self.sandbox = sandbox
        self.host = host
        self.port = port
        self.expert = expert
        # Extension sources: user-provided paths (directory or packaged extension file).
        # Prepared extensions: directories passed to Chrome via --load-extension (may include temp dirs).
        self._extension_sources: List[pathlib.Path] = []
        self._extensions: List[str] = []
        self._temp_extension_dirs: set[str] = set()
        # when using posix-ish operating system and running as root
        # you must use no_sandbox = True, which in case is corrected here
        if is_posix and is_root() and sandbox:
            logger.info("detected root usage, auto disabling sandbox mode")
            self.sandbox = False

        self.autodiscover_targets = True
        self.lang = lang

        # other keyword args will be accessible by attribute
        self.__dict__.update(kwargs)
        super().__init__()
        self._default_browser_args = [
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-service-autorun",
            "--no-default-browser-check",
            "--homepage=about:blank",
            "--no-pings",
            "--password-store=basic",
            "--disable-infobars",
            "--disable-breakpad",
            "--disable-dev-shm-usage",
            "--disable-session-crashed-bubble",
            "--disable-search-engine-choice-screen",
        ]

    @property
    def browser_args(self):
        return sorted(self._default_browser_args + self._browser_args)

    @property
    def no_sandbox(self) -> bool:
        """Alias for `sandbox=False` (adds Chrome flag `--no-sandbox`)."""
        return not bool(self.sandbox)

    @no_sandbox.setter
    def no_sandbox(self, value: bool) -> None:
        self.sandbox = not bool(value)

    @property
    def user_data_dir(self):
        return self._user_data_dir

    @user_data_dir.setter
    def user_data_dir(self, path: PathLike):
        self._user_data_dir = str(path)
        self._custom_data_dir = True

    @property
    def uses_custom_data_dir(self) -> bool:
        return self._custom_data_dir

    def add_extension(self, extension_path: PathLike):
        """
        adds an extension to load, you could point extension_path
        to a folder (containing the manifest), or extension file (crx)

        :param extension_path:
        :type extension_path:
        :return:
        :rtype:
        """
        path = pathlib.Path(extension_path)

        if not path.exists():
            raise FileNotFoundError("could not find anything here: %s" % str(path))

        if path.is_dir():
            # Normalize to the directory containing the manifest.
            for item in path.rglob("manifest.*"):
                path = item.parent
            self._extension_sources.append(path)
        else:
            # Packaged extension file (e.g. .crx). We'll extract it at browser start,
            # so we can always clean up the temp directory afterwards without breaking
            # re-use of this Config across multiple runs.
            self._extension_sources.append(path)

    def _prepare_extensions(self) -> List[str]:
        """
        Prepare the extension directories for a single browser run.

        - Directory sources are used as-is.
        - File sources are extracted into a unique temp directory (tracked for cleanup).
        """
        # Remove any stale temp extraction dirs from a previous run attempt.
        self.cleanup_extensions()

        prepared: List[str] = []
        for src in self._extension_sources:
            if src.is_file():
                tf = tempfile.mkdtemp(
                    prefix="extension_",
                    suffix=secrets.token_hex(4),
                    dir=str(nodriver_temp_dir("extensions")),
                )
                try:
                    with zipfile.ZipFile(src, "r") as z:
                        z.extractall(tf)
                except Exception:
                    # Best-effort cleanup of the just-created directory.
                    shutil.rmtree(tf, ignore_errors=True)
                    raise
                self._temp_extension_dirs.add(tf)
                prepared.append(tf)
            else:
                prepared.append(str(src))

        self._extensions = prepared
        return prepared

    def cleanup_extensions(self):
        """Remove any temp directories created for extracted packaged extensions."""
        temp_dirs = list(getattr(self, "_temp_extension_dirs", set()))
        for d in temp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=False)
            except FileNotFoundError:
                pass
            except Exception:
                logger.debug("failed to remove temp extension dir %s", d, exc_info=True)
        if hasattr(self, "_temp_extension_dirs"):
            self._temp_extension_dirs.clear()
        # Drop prepared extension dirs so we don't keep referencing deleted paths.
        if hasattr(self, "_extensions"):
            self._extensions = []

    def __call__(self):
        # the host and port will be added when starting
        # the browser, as by the time it starts, the port
        # is probably already taken
        args = self._default_browser_args.copy()
        args += ["--user-data-dir=%s" % self.user_data_dir]
        args += ["--disable-session-crashed-bubble"]

        disabled_features = "IsolateOrigins,site-per-process"
        if self._extensions:
            disabled_features += ",DisableLoadExtensionCommandLineSwitch"
        args += [f"--disable-features={disabled_features}"]
        if self._extensions:
            # Prepared by _prepare_extensions() (called by Browser.start()).
            # If a user explicitly sets --load-extension themselves, don't add ours.
            if not any(str(a).startswith("--load-extension") for a in self._browser_args):
                args += [
                    "--load-extension=%s" % ",".join(str(_) for _ in self._extensions)
                ]
            if not any(
                str(a).startswith("--enable-unsafe-extension-debugging")
                for a in self._browser_args
            ):
                args += ["--enable-unsafe-extension-debugging"]
        if self.expert:
            args += ["--disable-site-isolation-trials"]
        if self._browser_args:
            args.extend([arg for arg in self._browser_args if arg not in args])
        if self.headless:
            args.append("--headless=new")
        if not self.sandbox:
            args.append("--no-sandbox")
        if self.host:
            args.append("--remote-debugging-host=%s" % self.host)
        if self.port:
            args.append("--remote-debugging-port=%s" % self.port)
        return args

    def add_argument(self, arg: str):
        if any(
            x in arg.lower()
            for x in [
                "headless",
                "data-dir",
                "data_dir",
                "no-sandbox",
                "no_sandbox",
                "lang",
            ]
        ):
            raise ValueError(
                '"%s" not allowed. please use one of the attributes of the Config object to set it'
                % arg
            )
        self._browser_args.append(arg)

    def __repr__(self):
        s = f"{self.__class__.__name__}"
        for k, v in ({**self.__dict__, **self.__class__.__dict__}).items():
            if k[0] == "_":
                continue
            if not v:
                continue
            if isinstance(v, property):
                v = getattr(self, k)
            if callable(v):
                continue
            s += f"\n\t{k} = {v}"
        return s


def is_root():
    """
    helper function to determine if user trying to launch chrome
    under linux as root, which needs some alternative handling
    :return:
    :rtype:
    """
    import ctypes
    import os

    try:
        return os.getuid() == 0
    except AttributeError:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0


def temp_profile_dir():
    """generate a temp dir (path)"""
    path = os.path.normpath(
        tempfile.mkdtemp(prefix="uc_", dir=str(nodriver_temp_dir("profiles")))
    )
    return path


def find_chrome_executable(return_all=False):
    """
    Finds the chrome, beta, canary, chromium executable
    and returns the disk path
    """
    candidates = []
    if is_posix:
        for item in os.environ.get("PATH").split(os.pathsep):
            for subitem in (
                "google-chrome",
                "chromium",
                "chromium-browser",
                "chrome",
                "google-chrome-stable",
            ):
                candidates.append(os.sep.join((item, subitem)))
        if "darwin" in sys.platform:
            candidates += [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]

    else:
        for item in map(
            os.environ.get,
            ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"),
        ):
            if item is not None:
                for subitem in (
                    "Google/Chrome/Application",
                    "Google/Chrome Beta/Application",
                    "Google/Chrome Canary/Application",
                ):
                    candidates.append(os.sep.join((item, subitem, "chrome.exe")))
    rv = []
    for candidate in candidates:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            logger.debug("%s is a valid candidate... " % candidate)
            rv.append(candidate)
        else:
            logger.debug(
                "%s is not a valid candidate because don't exist or not executable "
                % candidate
            )

    winner = None

    if return_all and rv:
        return rv

    if rv and len(rv) > 1:
        # assuming the shortest path wins
        winner = min(rv, key=lambda x: len(x))

    elif len(rv) == 1:
        winner = rv[0]

    if winner:
        return os.path.normpath(winner)

    raise FileNotFoundError(
        "could not find a valid chrome browser binary. please make sure chrome is installed."
        "or use the keyword argument 'browser_executable_path=/path/to/your/browser' "
    )
