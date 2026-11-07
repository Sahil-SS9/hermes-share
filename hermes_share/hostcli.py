"""Child processes: the Hermes CLI (export, import) and ``gh``, never an import of host internals.

The host modules this file touches, ``hermes_constants`` and ``hermes_state``, are on the published
plugin API list. Every child gets an environment allowlist, never ``os.environ``: a Hermes process
holds every provider key on the machine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 120
_BASE_ENV = ("PATH", "LANG", "LC_ALL", "LANGUAGE", "SHELL", "TMPDIR", "SYSTEMROOT")
GH_ENV = (*_BASE_ENV, "HOME", "GH_CONFIG_DIR", "GH_HOST", "XDG_CONFIG_HOME")


class HostCliError(RuntimeError):
    """A child process failed; the message is safe to show the user."""


def hermes_home() -> Path:
    """The active Hermes home, through the public helper (follows profile switches)."""
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def child_env(keys: tuple[str, ...] = _BASE_ENV, **extra: str) -> dict[str, str]:
    return {**{k: os.environ[k] for k in keys if k in os.environ}, **extra}


def last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def run(
    argv: list[str], *, env: dict[str, str], timeout: int = TIMEOUT_SECONDS, what: str = ""
) -> subprocess.CompletedProcess:
    """Run *argv*; a timeout or spawn failure becomes a :class:`HostCliError` naming *what*."""
    what = what or " ".join(argv[:3])
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostCliError(f"`{what}` timed out after {timeout}s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostCliError(f"could not run `{what}` ({type(exc).__name__})") from exc


def hermes(*args: str, timeout: int = TIMEOUT_SECONDS) -> str:
    """``hermes <args>``; returns stdout, raises with the CLI's own last line on failure.

    ``hermes`` on PATH, else this interpreter's module entry point: a service-launched gateway may
    have no console script on PATH, but it always runs the host's own interpreter.
    """
    exe = shutil.which("hermes")
    argv = [exe] if exe else [sys.executable, "-m", "hermes_cli.main"]
    what = "hermes " + " ".join(args[:2])
    result = run([*argv, *args], env=child_env(HERMES_HOME=str(hermes_home())), timeout=timeout, what=what)
    if result.returncode != 0:
        detail = last_line(result.stderr) or last_line(result.stdout)
        raise HostCliError(f"`{what}` failed" + (f": {detail}" if detail else ""))
    return result.stdout or ""


def export_session_jsonl(session_id: str) -> str:
    """The one-line jsonl ``hermes sessions export`` writes for *session_id*."""
    text = hermes("sessions", "export", "-", "--format", "jsonl", "--session-id", session_id).strip()
    if not text:
        raise HostCliError(f"`hermes sessions export` produced nothing for {session_id}")
    return text + "\n"


def export_session_html(session_id: str, destination: Path) -> None:
    """``hermes sessions export --format html``: one session per page, written to *destination*."""
    hermes("sessions", "export", str(destination), "--format", "html", "--session-id", session_id)
    if not destination.is_file():
        raise HostCliError(f"the HTML export for {session_id} wrote no file")


def import_offers_hermes() -> bool:
    """Whether this host's ``sessions import --from`` accepts ``hermes`` (its own choices decide)."""
    try:
        text = hermes("sessions", "import", "--help", timeout=30)
    except HostCliError:
        return False
    return bool(re.search(r"--from\s+\{[^}]*\bhermes\b", text))


def import_file(source: str, path: Path) -> None:
    out = hermes("sessions", "import", "--from", source, str(path))
    if "Error:" in out:  # the import subcommand reports some refusals on stdout with status 0
        raise HostCliError(last_line(out))


def session_ids(selectors: list[str], last: int) -> list[str]:
    """Ids for ``--last N`` or for each selector (an id, id prefix or unambiguous title fragment)."""
    from hermes_state import SessionDB

    with SessionDB(db_path=hermes_home() / "state.db", read_only=True) as db:
        if last:
            return [
                str(r["id"])
                for r in db.list_sessions_rich(limit=last, order_by_last_active=True)
                if r.get("id")
            ]
        found, missing = [], []
        rows = None
        for selector in selectors:
            sid = db.resolve_session_id(selector)
            if not sid:
                rows = rows if rows is not None else db.list_sessions_rich(limit=500)
                hits = [r for r in rows if selector.lower() in str(r.get("title") or "").lower()]
                sid = str(hits[0]["id"]) if len(hits) == 1 else None
            if sid is None:
                missing.append(selector)
            elif sid not in found:
                found.append(sid)
    if missing:
        raise HostCliError(
            "no single session matches "
            + ", ".join(map(repr, missing))
            + " (pass an id, an id prefix, or a distinctive title fragment)"
        )
    return found
