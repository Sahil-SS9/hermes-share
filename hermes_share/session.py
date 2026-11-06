"""Read the current session and turn it into a redacted document.

The read goes through the same two host functions ``/save`` uses -- ``load_save_snapshot`` and
``render_session_for_save`` from ``hermes_cli.session_export`` -- so a shared session is byte-for-byte
the document ``/save`` would have written, only redacted and with format ``jsonl`` added.

Redaction runs in three passes, each of which reports how much it changed:

1. ``redact_session_data`` on the exported dict: the force-mode secret pass over every message
   body, tool-call argument and segment. This is exactly what ``/save <fmt> redact`` does.
2. ``redact_sensitive_text(force=True)`` over the whole rendered document, which also covers the
   metadata the dict pass skips (title, model, cwd, timings).
3. A home-path and identity scrub: ``/home/<user>`` and ``/Users/<user>`` become ``~``, and the
   local account name and hostnames are replaced with placeholders.

There is no switch to skip any of it.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
from pathlib import Path
from typing import Any

FORMAT_ALIASES = {
    "md": "md",
    "markdown": "md",
    "html": "html",
    "json": "json",
    "jsonl": "jsonl",
}
DEFAULT_FORMAT = "md"

#: Redaction output of an earlier pass: ``***`` masks, ``«redacted:ghp_…»`` sentinels, PEM notices.
_MARK_RE = re.compile(r"\*{3,}|«redacted(?::[^»\s]*|-secret)»|\[REDACTED[^\]]*\]")

_HOME_RE = re.compile(r"(?:/home/|/Users/)([^/\s\"'<>`)\],;:]+)")
_PLACEHOLDER = "<redacted>"


class SessionUnavailable(RuntimeError):
    """The session cannot be exported (no row yet, or past the export-size cap)."""


def normalise_format(token: str | None) -> str:
    """Map a user-typed format token to a canonical one."""
    key = (token or "").strip().lower()
    if not key:
        return DEFAULT_FORMAT
    mapped = FORMAT_ALIASES.get(key)
    if mapped is None:
        raise ValueError(
            f"unknown format {key!r} - use md, html, json or jsonl"
        )
    return mapped


def _open_db():
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB

    return SessionDB(db_path=Path(get_hermes_home()) / "state.db", read_only=True)


def load_session(session_id: str, fmt: str) -> dict[str, Any]:
    """The stored session a ``/save`` of *fmt* renders, or a named refusal."""
    from hermes_cli.session_export import load_save_snapshot

    if not session_id:
        raise SessionUnavailable(
            "no session is bound to this command, so there is nothing to share"
        )
    projection = "md" if fmt == "jsonl" else fmt
    try:
        with _open_db() as db:
            data = load_save_snapshot(db, session_id, projection)
    except SessionUnavailable:
        raise
    except Exception as exc:
        name = type(exc).__name__
        if "TooLarge" in name:
            raise SessionUnavailable(
                "this session is past the export-size cap Hermes sets for /save; nothing was "
                "written or uploaded"
            ) from exc
        raise SessionUnavailable(f"could not read the stored session ({name})") from exc
    if not data:
        raise SessionUnavailable(
            "this session has no stored messages yet - say something first, then /share"
        )
    rows = data.get("messages")
    if not isinstance(rows, list) or not rows:
        raise SessionUnavailable(
            "this session has no stored messages yet - say something first, then /share"
        )
    return data


def render_session(data: dict[str, Any], fmt: str) -> str:
    """Render an exported session dict as the document to share."""
    if fmt == "jsonl":
        rows = data.get("messages")
        if not isinstance(rows, list):
            rows = []
        return "\n".join(
            json.dumps(row, ensure_ascii=False, default=str) for row in rows
        ) + ("\n" if rows else "")
    from hermes_cli.session_export import render_session_for_save

    return render_session_for_save(data, fmt)


def count_marks(text: str) -> int:
    return len(_MARK_RE.findall(text or ""))


def redact_session_data_strong(session: dict[str, Any]) -> dict[str, Any]:
    """A copy of an export dict with every message and segment string strongly redacted.

    The shape is ``hermes_cli.session_export_md.redact_session_data``; only the masking mode differs
    (see :func:`redact_document`). Field names and ordering are untouched, so the renderers produce
    the same document, redacted.
    """
    from agent.redact import redact_sensitive_text

    def _clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_sensitive_text(value, force=True, file_read=True, secret_file=True)
        if isinstance(value, list):
            return [_clean(item) for item in value]
        if isinstance(value, dict):
            return {key: _clean(item) for key, item in value.items()}
        return value

    return {**session, **{key: _clean(session[key])
                          for key in ("messages", "segments") if session.get(key) is not None}}


def redact_document(data: dict[str, Any], fmt: str) -> tuple[str, int, int]:
    """Render *data* twice: once raw (discarded) and once redacted.

    Returns ``(document, secret_marks, extra_marks)`` where ``secret_marks`` is the number of
    redaction marks the two secret passes added and ``extra_marks`` those added by the whole-text
    pass over the already-redacted document.

    The message pass is ``/save``'s redact pass in the stronger masking mode: ``file_read`` makes a
    prefix-matched credential a non-reusable ``«redacted:sk-…»`` sentinel instead of the head/tail
    mask ``sk-pro...z2Xc`` that is right for a terminal but leaks ten characters of the key into a
    document that leaves the machine. ``secret_file`` keeps the ``KEY=value`` passes enabled, which
    ``file_read`` alone would skip.
    """
    from agent.redact import redact_sensitive_text

    raw = render_session(data, fmt)
    redacted = render_session(redact_session_data_strong(data), fmt)
    secrets = max(count_marks(redacted) - count_marks(raw), 0)
    del raw  # never written anywhere: it only ever existed to count what the pass removed

    before = count_marks(redacted)
    redacted = redact_sensitive_text(redacted, force=True)
    extra = max(count_marks(redacted) - before, 0)
    return redacted, secrets, extra


def scrub_home_paths(text: str) -> tuple[str, int]:
    """``/home/<user>`` and ``/Users/<user>`` become ``~``; counts the paths rewritten."""
    removed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        return "~"

    out = _HOME_RE.sub(_replace, text)

    # A home directory outside the two conventional roots (containers, CI runners) still leaks
    # the account name in full; replace it literally when it is present.
    home = os.path.expanduser("~")
    if home and home not in ("~", "/") and home in out:
        out = out.replace(home, "~")
        removed += 1
    return out, removed


def identity_tokens() -> list[str]:
    """Account and host names to scrub, longest first so a FQDN is replaced before its short form."""
    tokens: list[str] = []
    for getter in (lambda: getpass.getuser(), socket.gethostname, socket.getfqdn):
        try:
            value = (getter() or "").strip()
        except Exception:
            continue
        if len(value) >= 3 and value not in tokens:
            tokens.append(value)
    return sorted(tokens, key=len, reverse=True)


def scrub_identity(text: str, tokens: list[str] | None = None) -> tuple[str, int]:
    """Replace the local account name and hostnames with placeholders."""
    removed = 0
    for token in (identity_tokens() if tokens is None else tokens):
        pattern = re.compile(r"(?<![\w.-])" + re.escape(token) + r"(?![\w.-])")
        text, hits = pattern.subn(_PLACEHOLDER, text)
        removed += hits
    return text, removed


def redact(text: str, *, tokens: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """The full scrub over an already-rendered document (used for the gist description too)."""
    from agent.redact import redact_sensitive_text

    before = count_marks(text)
    text = redact_sensitive_text(text, force=True)
    secrets = max(count_marks(text) - before, 0)
    text, paths = scrub_home_paths(text)
    text, identities = scrub_identity(text, tokens)
    return text, {"secrets": secrets, "paths": paths, "identities": identities}
