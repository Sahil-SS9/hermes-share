"""The only network actions the plugin can take, all through the ``gh`` CLI.

No token is ever read, stored or passed: authentication is whatever ``gh`` already has for the OS
user running Hermes. The child gets an explicit allowlist of environment variables rather than the
parent's whole environment, because a Hermes session holds every provider key on the machine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Callable, Optional

CREATE_TIMEOUT_SECONDS = 60
DELETE_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 45
_URL_RE = re.compile(r"https://gist\.github\.com/[^\s\"'<>]+")

#: Nothing else reaches the child. ``GH_CONFIG_DIR`` is honoured when the user sets it.
_ENV_ALLOWLIST = (
    "HOME",
    "PATH",
    "GH_CONFIG_DIR",
    "GH_HOST",
    "XDG_CONFIG_HOME",
    "LANG",
    "LC_ALL",
    "LANGUAGE",
    "SHELL",
    "TMPDIR",
)

Redactor = Callable[[str], str]


class GistError(RuntimeError):
    """A ``gh`` call failed; the message is safe to show the user."""


def child_env() -> dict[str, str]:
    """The environment handed to ``gh``: an allowlist, never ``os.environ`` wholesale."""
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


def find_gh() -> Optional[str]:
    return shutil.which("gh")


def _run(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_env(),
        check=False,
    )


def _detail(text: str, redact: Optional[Redactor] = None, limit: int = 300) -> str:
    """A short, safe excerpt of gh's own output for an error line."""
    cleaned = (redact(text) if redact else (text or "")).strip()
    return cleaned[-limit:]


def missing_gh_hint() -> str:
    """A local-only check for the preview footer: a PATH lookup, never a ``gh`` invocation."""
    if find_gh():
        return ""
    return (
        "the GitHub CLI (gh) is not installed, so /share confirm will refuse to upload.\n"
        "    Install it from https://cli.github.com/ then run: gh auth login"
    )


def auth_problem(exe: Optional[str] = None, redact: Optional[Redactor] = None) -> Optional[str]:
    """None when ``gh`` exists and is signed in, else the line telling the user what to run."""
    exe = exe or find_gh()
    if not exe:
        return (
            "the GitHub CLI (gh) is not installed, so nothing can be uploaded.\n"
            "  Install it from https://cli.github.com/ then run: gh auth login"
        )
    try:
        result = _run([exe, "auth", "status"], timeout=DELETE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run gh ({type(exc).__name__}), so nothing was uploaded."
    if result.returncode != 0:
        detail = _detail(result.stderr or result.stdout or "", redact)
        suffix = f"\n  gh said: {detail}" if detail else ""
        return f"gh is not signed in for this user, so nothing was uploaded.\n  Run: gh auth login{suffix}"
    return None


def gist_id_from_url(url: str) -> str:
    """``https://gist.github.com/<user>/<id>`` -> ``<id>``."""
    path = (url or "").strip().rstrip("/").split("/")
    return path[-1] if path else ""


def create_secret_gist(description: str, paths: list[str], *, exe: Optional[str] = None,
                       redact: Optional[Redactor] = None) -> dict[str, Any]:
    """Create a secret gist holding every file in *paths*. ``--public`` is never passed."""
    exe = exe or find_gh()
    if not exe:
        raise GistError("gh is not installed, so nothing was uploaded")
    if not paths:
        raise GistError("nothing to upload")
    argv = [exe, "gist", "create", *paths, "--desc", description]
    # Defence in depth: the only visibility flag this plugin may ever send is none at all.
    assert "--public" not in argv and "-p" not in argv
    try:
        result = _run(argv, timeout=CREATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GistError(
            "gh gist create timed out; the gist may or may not exist - check `gh gist list`"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GistError(f"could not run gh ({type(exc).__name__}); nothing was uploaded") from exc
    if result.returncode != 0:
        detail = _detail(result.stderr or result.stdout or "", redact)
        raise GistError(f"gh gist create failed: {detail or 'no detail reported'}")
    matches = _URL_RE.findall(result.stdout or "")
    if not matches:
        raise GistError(
            "gh gist create printed no gist URL, so the upload cannot be confirmed; "
            "check `gh gist list`"
        )
    url = matches[-1]
    gist_id = gist_id_from_url(url)
    if not gist_id:
        raise GistError("gh printed a gist URL with no id, so the upload cannot be confirmed")
    return {"url": url, "gist_id": gist_id}


def list_gist_files(gist_id: str) -> list[str]:
    exe = find_gh()
    if not exe:
        raise GistError("gh is not installed")
    try:
        result = _run([exe, "gist", "view", gist_id, "--files"], timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GistError(f"could not list the gist's files ({type(exc).__name__})") from exc
    if result.returncode != 0:
        raise GistError(f"could not list the gist's files: {_detail(result.stderr or result.stdout)}")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def read_gist_file(gist_id: str, name: str) -> Optional[str]:
    exe = find_gh()
    if not exe:
        raise GistError("gh is not installed")
    try:
        result = _run([exe, "gist", "view", gist_id, "--raw", "--filename", name],
                      timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GistError(f"could not read {name} ({type(exc).__name__})") from exc
    if result.returncode != 0:
        return None
    return result.stdout or ""


def delete_gist(gist_id: str, *, exe: Optional[str] = None, redact: Optional[Redactor] = None) -> None:
    """Delete a gist by id. ``--yes`` because the caller already asked the user."""
    exe = exe or find_gh()
    if not exe:
        raise GistError("gh is not installed, so nothing was deleted")
    if not gist_id or "/" in gist_id or " " in gist_id:
        raise GistError(f"not a gist id: {gist_id!r}")
    try:
        result = _run([exe, "gist", "delete", gist_id, "--yes"], timeout=DELETE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GistError("gh gist delete timed out; check `gh gist list`") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GistError(f"could not run gh ({type(exc).__name__}); nothing was deleted") from exc
    if result.returncode != 0:
        detail = _detail(result.stderr or result.stdout or "", redact)
        raise GistError(f"gh gist delete failed: {detail or 'no detail reported'}")
