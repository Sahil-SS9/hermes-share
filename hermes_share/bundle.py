"""Turn session ids into the redacted files a shared gist carries.

A bundle is up to three kinds of file: jsonl (the importable record, one exported session per
line), md (the readable view) and one html page per session. Every string is scrubbed before
anything is written, and there is no switch to skip it.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import re
import socket
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import hostcli

MAX_BUNDLE_SESSIONS = 12
MAX_MESSAGE_CHARS_PER_SESSION = 400_000

_MARK_RE = re.compile(r"\*{3,}|«redacted(?::[^»\s]*|-secret)»|\[REDACTED[^\]]*\]")
_HOME_RE = re.compile(r"(?:/home/|/Users/)[^/\s\"'<>`)\],;:]+")

Redactor = Callable[[str], str]


class BundleError(RuntimeError):
    """The bundle cannot be built; the message is safe to show the user."""


def _identity_tokens() -> list[str]:
    """Account and host names, longest first so a FQDN goes before its short form."""
    tokens: set[str] = set()
    for getter in (getpass.getuser, socket.gethostname, socket.getfqdn):
        try:
            value = (getter() or "").strip()
        except Exception:
            continue
        if len(value) >= 3:
            tokens.add(value)
    return sorted(tokens, key=len, reverse=True)


class Scrubber:
    """The host's secret pass, then home paths to ``~``, then account/host names; counts each."""

    def __init__(self, redact: Redactor) -> None:
        self.redact = redact
        self.counts: Counter[str] = Counter(secrets=0, paths=0, identities=0)
        home = os.path.expanduser("~")
        self._home = home if home not in ("~", "/") else ""
        self._identity = [
            re.compile(r"(?<![\w.-])" + re.escape(t) + r"(?![\w.-])") for t in _identity_tokens()
        ]

    def text(self, value: str) -> str:
        before = len(_MARK_RE.findall(value))
        value = self.redact(value)
        self.counts["secrets"] += max(len(_MARK_RE.findall(value)) - before, 0)
        value, hits = _HOME_RE.subn("~", value)
        if self._home and self._home in value:
            hits += value.count(self._home)
            value = value.replace(self._home, "~")
        self.counts["paths"] += hits
        for pattern in self._identity:
            value, hits = pattern.subn("<redacted>", value)
            self.counts["identities"] += hits
        return value

    def value(self, value: Any) -> Any:
        """Every string inside a JSON value, keys included (a path can be a key)."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.value(key): self.value(item) for key, item in value.items()}
        return value


def parse_export_jsonl(text: str, *, origin: str = "session") -> list[dict[str, Any]]:
    """Parse exported sessions; a malformed record is refused by line number, never skipped."""
    sessions: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise BundleError(f"{origin}: line {number} is not JSON") from exc
        if not isinstance(record, dict) or not isinstance(record.get("messages"), list):
            raise BundleError(f"{origin}: line {number} is not a Hermes session record")
        sessions.append(record)
    if not sessions:
        raise BundleError(f"{origin}: no session records found")
    return sessions


def _stamp(value: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value or "?")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n\n".join(
            b if isinstance(b, str) else str(b.get("text") or "")
            for b in content
            if isinstance(b, (str, dict))
        )
    return content.strip() if isinstance(content, str) else ""


def render_markdown(sessions: list[dict[str, Any]]) -> str:
    """A readable transcript of every session in the bundle."""
    out: list[str] = []
    if len(sessions) > 1:
        out += [f"# Shared Hermes sessions ({len(sessions)})", ""]
        out += [f"- {s.get('title') or s.get('id') or 'untitled'}" for s in sessions] + [""]
    for s in sessions:
        out += [
            f"# {s.get('title') or s.get('id') or 'untitled session'}",
            "",
            f"- Session ID: `{s.get('id') or '?'}`",
            f"- Source: `{s.get('source') or '?'}`",
        ]
        if s.get("model"):
            out.append(f"- Model: `{s['model']}`")
        out += [f"- Started: {_stamp(s.get('started_at'))}", f"- Messages: {len(s['messages'])}", ""]
        for m in (m for m in s["messages"] if isinstance(m, dict)):
            tool = f" ({m['tool_name']})" if m.get("tool_name") else ""
            out += [
                f"## {m.get('role') or 'unknown'} - {_stamp(m.get('timestamp'))}{tool}",
                "",
                _message_text(m) or "_(no text content)_",
                "",
            ]
    return "\n".join(out).rstrip() + "\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def build(
    session_ids: list[str],
    redact: Redactor,
    directory: Path,
    stem: str,
    formats: frozenset[str] = frozenset({"jsonl", "md"}),
) -> dict[str, Any]:
    """Export, scrub and write the bundle under *directory*; returns its sessions, files and counts."""
    if not session_ids:
        raise BundleError("no sessions selected")
    if len(session_ids) > MAX_BUNDLE_SESSIONS:
        raise BundleError(
            f"a bundle carries at most {MAX_BUNDLE_SESSIONS} sessions ({len(session_ids)} selected)"
        )
    scrub = Scrubber(redact)
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    pages: list[Path] = []
    for session_id in session_ids:
        raw = parse_export_jsonl(hostcli.export_session_jsonl(session_id), origin=session_id)
        if len(raw) != 1:
            raise BundleError(f"{session_id}: the export returned {len(raw)} records, expected 1")
        size = len(json.dumps(raw[0]["messages"], ensure_ascii=False, default=str))
        if size > MAX_MESSAGE_CHARS_PER_SESSION:
            raise BundleError(
                f"{session_id}: {size} characters of messages is past the "
                f"per-session cap ({MAX_MESSAGE_CHARS_PER_SESSION})"
            )
        before = scrub.counts["secrets"]
        record = scrub.value(raw[0])
        records.append(record)
        sessions.append(
            {
                "id": str(record.get("id") or session_id),
                "title": str(record.get("title") or "untitled"),
                "messages": len(record["messages"]),
                "secrets": scrub.counts["secrets"] - before,
            }
        )
        if "html" in formats:
            page = directory / f"{stem}-{re.sub(r'[^A-Za-z0-9_-]', '', session_id) or 'session'}.html"
            hostcli.export_session_html(session_id, page)
            pages.append(_write(page, scrub.text(page.read_text(encoding="utf-8", errors="replace"))))
    files: list[Path] = []
    if "jsonl" in formats:
        body = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records)
        files.append(_write(directory / f"{stem}.jsonl", body))
    if "md" in formats:
        files.append(_write(directory / f"{stem}.md", render_markdown(records)))
    return {"sessions": sessions, "files": files + pages, "totals": dict(scrub.counts), "scrub": scrub}


def summary_lines(sessions: list[dict[str, Any]], *, secrets: bool = True) -> list[str]:
    """One line per session, shared by the share and import previews."""
    return [
        f"  {str(s['id'])[:24]:<24}  {s['messages']:>4} msg  "
        + (f"{s.get('secrets', 0):>3} secret mark(s)  " if secrets else "")
        + str(s["title"])[:60]
        for s in sessions
    ]
