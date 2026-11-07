"""Every network action the plugin takes, all through the ``gh`` CLI.

No token is read, stored or passed: authentication is whatever ``gh`` already has for the OS user,
and ``gh`` sees an environment allowlist only (``hostcli.GH_ENV``).
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable

from .hostcli import GH_ENV, HostCliError, child_env, last_line, run

_URL_RE = re.compile(r"https://gist\.github\.com/[^\s\"'<>]+")
_GIST_RE = re.compile(r"^https?://gist\.github\.com/(?:[A-Za-z0-9_.-]+/)?([0-9a-fA-F]{8,})/?$")
_INSTALL = "Install the GitHub CLI from https://cli.github.com/ then run: gh auth login"


class GistError(RuntimeError):
    """A ``gh`` call failed; the message is safe to show the user."""


def gist_id_from_url(url: str) -> str | None:
    match = _GIST_RE.match((url or "").strip())
    return match.group(1) if match else None


def _gh(*args: str, timeout: int = 60, redact: Callable[[str], str] | None = None):
    exe = shutil.which("gh")
    if not exe:
        raise GistError(f"the GitHub CLI (gh) is not installed. {_INSTALL}")
    try:
        result = run([exe, *args], env=child_env(GH_ENV), timeout=timeout, what=f"gh {args[0]} {args[1]}")
    except HostCliError as exc:
        raise GistError(f"{exc}; check `gh gist list` before retrying") from exc
    if result.returncode != 0:
        detail = last_line(result.stderr) or last_line(result.stdout)
        raise GistError(f"`gh {args[0]} {args[1]}` failed: {(redact or str)(detail) or 'no detail reported'}")
    return result.stdout or ""


def missing_gh_hint() -> str:
    """For the preview: a PATH lookup only, never a ``gh`` call."""
    return "" if shutil.which("gh") else f"gh is not installed, so /share confirm will refuse. {_INSTALL}"


def require_auth(redact: Callable[[str], str] | None = None) -> None:
    try:
        _gh("auth", "status", timeout=30, redact=redact)
    except GistError as exc:
        raise GistError(f"gh is not ready, so nothing was sent. Run: gh auth login\n  ({exc})") from exc


def create_secret_gist(description: str, paths: list[str], redact: Callable[[str], str]) -> tuple[str, str]:
    """Create a SECRET gist of *paths*; returns (url, id). No visibility flag is ever passed."""
    out = _gh("gist", "create", *paths, "--desc", description, redact=redact)
    urls = _URL_RE.findall(out)
    gist_id = gist_id_from_url(urls[-1]) if urls else None
    if not gist_id:
        raise GistError("gh printed no gist URL, so the upload cannot be confirmed; check `gh gist list`")
    return urls[-1], gist_id


def read_gist(gist_id: str) -> list[tuple[str, str]]:
    """(name, text) for every file in the gist; ``--raw`` alone cannot read a multi-file gist."""
    names = [n.strip() for n in _gh("gist", "view", gist_id, "--files", timeout=45).splitlines() if n.strip()]
    return [(name, _gh("gist", "view", gist_id, "--raw", "--filename", name, timeout=45)) for name in names]


def delete_gist(gist_id: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{8,}", gist_id or ""):
        raise GistError(f"not a gist id: {gist_id!r}")
    _gh("gist", "delete", gist_id, "--yes", timeout=30)
