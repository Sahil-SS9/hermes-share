"""Run the Hermes CLI the way a plugin is allowed to: as a child process, never as an import.

Every host module this file touches -- ``hermes_constants`` for the home, ``hermes_state`` for the
session index -- is on the published plugin API list (``hermes_cli/data/plugin_api.json``). The
private modules the first version imported (``hermes_cli.session_export``, ``agent.redact``) are
gone: exports go through ``hermes sessions export``, imports through ``hermes sessions import``, and
redaction through ``ctx.redact_text``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

EXPORT_TIMEOUT_SECONDS = 120
IMPORT_TIMEOUT_SECONDS = 120
HELP_TIMEOUT_SECONDS = 30

#: Variables the child may see. Hermes needs its home and a PATH; it must not inherit the session's
#: provider keys any more than ``gh`` does.
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LANGUAGE", "SHELL", "TMPDIR", "SYSTEMROOT")


class HostCliError(RuntimeError):
    """A ``hermes`` subcommand failed; the message is safe to show the user."""


def hermes_home() -> str:
    """The active Hermes home, resolved through the public helper (follows profile switches)."""
    from hermes_constants import get_hermes_home

    return str(get_hermes_home())


def argv_prefix() -> list[str]:
    """``hermes`` on PATH, else this interpreter's own module entry point.

    A gateway launched by a service manager may have no console script on PATH, but it always has
    the interpreter that is running the host -- so the fallback is the host's own code, not a guess.
    """
    found = shutil.which("hermes")
    if found:
        return [found]
    return [sys.executable, "-m", "hermes_cli.main"]


def child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env["HERMES_HOME"] = hermes_home()
    # A plugin-run child must not think it is a gateway or inherit a turn's session binding.
    for key in list(env):
        if key.startswith("HERMES_SESSION_"):
            env.pop(key, None)
    return env


def run(args: list[str], *, timeout: int = EXPORT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [*argv_prefix(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostCliError(f"`hermes {' '.join(args[:2])}` timed out after {timeout}s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostCliError(f"could not run `hermes {' '.join(args[:2])}` ({type(exc).__name__})") from exc


def export_session_jsonl(session_id: str) -> str:
    """The one-line jsonl ``hermes sessions export`` writes for *session_id*."""
    result = run(["sessions", "export", "-", "--format", "jsonl", "--session-id", session_id])
    if result.returncode != 0:
        detail = _last_line(result.stderr or result.stdout)
        raise HostCliError(
            f"`hermes sessions export` failed for {session_id}"
            + (f": {detail}" if detail else "")
        )
    text = (result.stdout or "").strip()
    if not text:
        raise HostCliError(f"`hermes sessions export` produced nothing for {session_id}")
    return text + "\n"


def export_session_html(session_id: str, destination: Path) -> None:
    """Write the standalone HTML document ``hermes sessions export --format html`` renders.

    HTML is the one export format that refuses ``-``: the CLI writes the file itself, so the caller
    names the path. One call per session, because the CLI renders a single session per document.
    """
    result = run(["sessions", "export", str(destination), "--format", "html", "--session-id", session_id])
    if result.returncode != 0:
        detail = _last_line(result.stderr or result.stdout)
        raise HostCliError(
            f"`hermes sessions export --format html` failed for {session_id}"
            + (f": {detail}" if detail else "")
        )
    if not destination.is_file():
        raise HostCliError(f"the HTML export for {session_id} wrote no file")


def import_help_offers_hermes() -> bool:
    """Whether this host's ``sessions import --from`` accepts ``hermes``.

    Feature detection, not a version guess: the flag's own choices are the only authority.
    """
    try:
        result = run(["sessions", "import", "--help"], timeout=HELP_TIMEOUT_SECONDS)
    except HostCliError:
        return False
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    return bool(re.search(r"--from\s+\{[^}]*\bhermes\b", text))


def import_file(source: str, path: str) -> None:
    """``hermes sessions import --from <source> <path>``."""
    result = run(["sessions", "import", "--from", source, path], timeout=IMPORT_TIMEOUT_SECONDS)
    if result.returncode != 0:
        detail = _last_line(result.stderr or result.stdout)
        raise HostCliError(
            f"`hermes sessions import --from {source}` failed" + (f": {detail}" if detail else "")
        )
    if "Error:" in (result.stdout or ""):
        raise HostCliError(_last_line(result.stdout) or "the import was refused")


def _last_line(text: str) -> str:
    for line in reversed((text or "").strip().splitlines()):
        if line.strip():
            return line.strip()[:300]
    return ""


def open_session_index():
    """A read-only ``SessionDB`` for selectors and the ``--last N`` window."""
    from hermes_state import SessionDB

    return SessionDB(db_path=_state_db_path(), read_only=True)


def _state_db_path():
    from pathlib import Path

    return Path(hermes_home()) / "state.db"


def resolve_selector(db, selector: str) -> Optional[str]:
    """A session id from an id, an id prefix, or an unambiguous title fragment."""
    token = (selector or "").strip()
    if not token:
        return None
    resolved = db.resolve_session_id(token)
    if resolved:
        return resolved
    needle = token.lower()
    hits = [row for row in db.list_sessions_rich(limit=500)
            if needle in str(row.get("title") or "").lower()]
    if len(hits) == 1:
        return str(hits[0]["id"])
    return None


def recent_session_ids(db, count: int) -> list[str]:
    rows = db.list_sessions_rich(limit=max(1, count), order_by_last_active=True)
    return [str(row["id"]) for row in rows if row.get("id")]
