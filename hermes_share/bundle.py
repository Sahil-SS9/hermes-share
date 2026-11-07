"""Turn session selectors into the files a shared gist carries.

One bundle = a jsonl file (the importable record, exactly what ``hermes sessions export`` writes,
one session per line) plus a Markdown file (the readable view). Both are redacted before they are
written; there is no switch to skip it.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

MAX_BUNDLE_SESSIONS = 12
MAX_MESSAGE_CHARS_PER_SESSION = 400_000

_MARK_RE = re.compile(r"\*{3,}|«redacted(?::[^»\s]*|-secret)»|\[REDACTED[^\]]*\]")
_HOME_RE = re.compile(r"(?:/home/|/Users/)([^/\s\"'<>`)\],;:]+)")
_PLACEHOLDER = "<redacted>"

Redactor = Callable[[str], str]


class BundleError(RuntimeError):
    """The bundle cannot be built; the message is safe to show the user."""


def count_marks(text: str) -> int:
    return len(_MARK_RE.findall(text or ""))


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


def scrub_home_paths(text: str) -> tuple[str, int]:
    removed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        return "~"

    out = _HOME_RE.sub(_replace, text)
    home = os.path.expanduser("~")
    if home and home not in ("~", "/") and home in out:
        out = out.replace(home, "~")
        removed += 1
    return out, removed


def scrub_identity(text: str, tokens: list[str]) -> tuple[str, int]:
    removed = 0
    for token in tokens:
        pattern = re.compile(r"(?<![\w.-])" + re.escape(token) + r"(?![\w.-])")
        text, hits = pattern.subn(_PLACEHOLDER, text)
        removed += hits
    return text, removed


def redact_text(text: str, redact: Redactor, tokens: list[str]) -> tuple[str, dict[str, int]]:
    """Secret pass, then home paths, then identity. Counts what each one changed."""
    before = count_marks(text)
    text = redact(text)
    secrets = max(count_marks(text) - before, 0)
    text, paths = scrub_home_paths(text)
    text, identities = scrub_identity(text, tokens)
    return text, {"secrets": secrets, "paths": paths, "identities": identities}


def _redact_value(value: Any, redact: Redactor) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item, redact) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, redact) for key, item in value.items()}
    return value


def parse_export_jsonl(text: str, *, origin: str = "session") -> list[dict[str, Any]]:
    """Parse one-or-more exported sessions; a malformed record is refused, never skipped silently."""
    sessions: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise BundleError(f"{origin}: line {number} is not JSON ({exc.__class__.__name__})") from exc
        if not isinstance(record, dict) or not isinstance(record.get("messages"), list):
            raise BundleError(f"{origin}: line {number} is not a Hermes session record")
        sessions.append(record)
    if not sessions:
        raise BundleError(f"{origin}: no session records found")
    return sessions


def redact_session(record: dict[str, Any], redact: Redactor) -> tuple[dict[str, Any], int]:
    """Redact every string in one session record; returns it and the number of marks added."""
    messages = record.get("messages") or []
    raw_text = json.dumps(messages, ensure_ascii=False, default=str)
    cleaned = {**record, "messages": _redact_value(messages, redact)}
    for key in ("segments",):
        if record.get(key) is not None:
            cleaned[key] = _redact_value(record[key], redact)
    redacted_text = json.dumps(cleaned.get("messages") or [], ensure_ascii=False, default=str)
    added = max(count_marks(redacted_text) - count_marks(raw_text), 0)
    return cleaned, added


def _stamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return "?"
    return str(value or "?")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n\n".join(part for part in parts if part)
    return ""


def render_markdown(sessions: Iterable[dict[str, Any]]) -> str:
    """A readable transcript of every session in the bundle."""
    out: list[str] = []
    sessions = list(sessions)
    if len(sessions) > 1:
        out.append(f"# Shared Hermes sessions ({len(sessions)})\n")
        for record in sessions:
            out.append(f"- {record.get('title') or record.get('id') or 'untitled'}")
        out.append("")
    for record in sessions:
        title = record.get("title") or record.get("id") or "untitled session"
        out.append(f"# {title}\n")
        out.append(f"- Session ID: `{record.get('id') or '?'}`")
        out.append(f"- Source: `{record.get('source') or '?'}`")
        if record.get("model"):
            out.append(f"- Model: `{record['model']}`")
        out.append(f"- Started: {_stamp(record.get('started_at'))}")
        out.append(f"- Messages: {len(record.get('messages') or [])}")
        out.append("")
        for message in record.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = message.get("role") or "unknown"
            header = f"## {role} - {_stamp(message.get('timestamp'))}"
            if message.get("tool_name"):
                header += f" ({message['tool_name']})"
            out.append(header)
            out.append("")
            text = _message_text(message)
            out.append(text if text else "_(no text content)_")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def build(session_ids: list[str], redact: Redactor, *, formats: Optional[set[str]] = None,
          html_paths: Optional[dict[str, "os.PathLike[str] | str"]] = None) -> dict[str, Any]:
    """Export, redact and render every session in the bundle.

    ``formats`` carries the kinds to produce (``jsonl``, ``md``, ``html``). HTML is rendered by the
    host CLI into the caller's path, one call per session, then redacted in place like every other
    file -- it is a public export format, not a private renderer.
    """
    from . import hostcli

    formats = {"jsonl", "md"} if formats is None else set(formats)
    if not session_ids:
        raise BundleError("no sessions selected")
    if len(session_ids) > MAX_BUNDLE_SESSIONS:
        raise BundleError(
            f"a bundle carries at most {MAX_BUNDLE_SESSIONS} sessions ({len(session_ids)} selected)"
        )
    tokens = identity_tokens()
    records: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    html_docs: list[dict[str, Any]] = []
    for session_id in session_ids:
        raw = parse_export_jsonl(hostcli.export_session_jsonl(session_id), origin=session_id)
        if len(raw) != 1:
            raise BundleError(f"{session_id}: the export returned {len(raw)} records, expected 1")
        record, secret_marks = redact_session(raw[0], redact)
        size = len(json.dumps(record.get("messages") or [], ensure_ascii=False, default=str))
        if size > MAX_MESSAGE_CHARS_PER_SESSION:
            raise BundleError(
                f"{session_id}: {size} characters of messages is past the per-session cap "
                f"({MAX_MESSAGE_CHARS_PER_SESSION})"
            )
        records.append(record)
        stats.append({
            "id": str(record.get("id") or session_id),
            "title": str(record.get("title") or "untitled"),
            "messages": len(record.get("messages") or []),
            "secrets": secret_marks,
        })
        if "html" in formats:
            target = (html_paths or {}).get(session_id)
            if target is None:
                raise BundleError(f"{session_id}: html was requested but no output path was given")
            path = Path(target)
            hostcli.export_session_html(session_id, path)
            document, scrub = redact_text(path.read_text(encoding="utf-8", errors="replace"), redact, tokens)
            path.write_text(document, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            html_docs.append({"id": str(record.get("id") or session_id), "path": path, "scrub": scrub})

    jsonl_text = "\n".join(json.dumps(record, ensure_ascii=False, default=str) for record in records) + "\n"
    jsonl_text, jsonl_scrub = redact_text(jsonl_text, redact, tokens)
    md_text = render_markdown(records)
    md_text, md_scrub = redact_text(md_text, redact, tokens)

    html_scrub = {"secrets": 0, "paths": 0, "identities": 0}
    for doc in html_docs:
        for key, value in doc.pop("scrub").items():
            html_scrub[key] = html_scrub.get(key, 0) + value

    totals = {
        "secrets": (sum(item["secrets"] for item in stats)
                    + jsonl_scrub["secrets"] + md_scrub["secrets"] + html_scrub["secrets"]),
        "paths": jsonl_scrub["paths"] + md_scrub["paths"] + html_scrub["paths"],
        "identities": (jsonl_scrub["identities"] + md_scrub["identities"]
                       + html_scrub["identities"]),
    }
    return {"sessions": stats, "jsonl": jsonl_text, "markdown": md_text, "html": html_docs,
            "totals": totals}


def summary_lines(bundle: dict[str, Any]) -> list[str]:
    """One line per session, shared by the terminal preview and the chat preview."""
    lines = []
    for item in bundle["sessions"]:
        lines.append(
            f"  {item['id'][:24]:<24}  {item['messages']:>4} msg  "
            f"{item['secrets']:>3} secret mark(s)  {item['title'][:60]}"
        )
    return lines
