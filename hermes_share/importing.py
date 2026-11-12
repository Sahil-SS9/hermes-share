"""Read a shared bundle back: fetch it, say what is inside, import only on confirm.

The format is decided from the CONTENT of each file, never its name or the URL. A gist carries a
jsonl, a Markdown and maybe HTML pages; only a file that parses as session records is offered, so
the readable copy can never be imported as if it were a transcript.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import gists
from .bundle import parse_export_jsonl

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
LABELS = {"hermes": "Hermes export", "claude": "Claude Code", "codex": "Codex CLI"}


class ImportFailed(RuntimeError):
    """The target cannot be read or holds no session; safe to show the user."""


def _capped(name: str, data: bytes) -> tuple[str, str]:
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ImportFailed(f"{name} is past the {MAX_DOWNLOAD_BYTES // 2**20} MB cap")
    return name, data.decode("utf-8", errors="replace")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL, so a download never drops to plain http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - stdlib signature
        if not str(newurl).lower().startswith("https://"):
            raise ImportFailed(f"refusing a redirect to {newurl}: imports download over https only")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request):  # noqa: ANN202 - an http.client response
    return urllib.request.build_opener(_HttpsOnlyRedirects()).open(request, timeout=30)


def fetch(target: str) -> list[tuple[str, str]]:
    """(name, text) for each file behind a gist URL (via gh: a secret gist is not public), an
    https URL, or a local path."""
    gist_id = gists.gist_id_from_url(target)
    if gist_id:
        gists.require_auth()
        return [_capped(name, text.encode("utf-8")) for name, text in gists.read_gist(gist_id)]
    if target.lower().startswith("http://"):
        raise ImportFailed(f"refusing {target}: imports download over https only")
    if target.lower().startswith("https://"):
        request = urllib.request.Request(target, headers={"User-Agent": "hermes-share"})
        try:
            with _open(request) as response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
        except ImportFailed:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImportFailed(f"could not download {target} ({exc})") from exc
        return [_capped(target.rstrip("/").rsplit("/", 1)[-1] or "download", data)]
    path = Path(target).expanduser()
    try:
        with path.open("rb") as handle:
            return [_capped(path.name, handle.read(MAX_DOWNLOAD_BYTES + 1))]
    except OSError as exc:
        raise ImportFailed(f"cannot read {target} ({type(exc).__name__})") from exc


def _records(text: str) -> list[dict[str, Any]]:
    try:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        return []
    return records if records and all(isinstance(r, dict) for r in records) else []


def detect(text: str) -> str | None:
    """``hermes`` / ``claude`` / ``codex`` from the content, or None."""
    records = _records(text)
    if records and all(isinstance(r.get("messages"), list) for r in records):
        return "hermes" if any(r["messages"] for r in records) else None
    kinds = {str(r.get("type") or "") for r in records}
    if kinds & {"response_item", "session_meta", "turn_context"}:
        return "codex"
    if kinds & {"user", "assistant"} and any("message" in r or "sessionId" in r for r in records):
        return "claude"
    return None


def _first_user_line(records: list[dict[str, Any]]) -> str:
    for r in records:
        message = r.get("message") if r.get("type") == "user" else None
        content = message.get("content") if isinstance(message, dict) else None
        texts = (
            [content]
            if isinstance(content, str)
            else [
                b.get("text") for b in content or [] if isinstance(b, dict) and isinstance(b.get("text"), str)
            ]
        )
        for text in texts:
            if text.strip():
                return text.strip().splitlines()[0][:80]
    return ""


def _turns(records: list[dict[str, Any]], source: str) -> int:
    """Turns as the host's parser counts them (a Codex message is ``response_item`` ->
    ``payload.type == "message"``); the host stays the authority on import."""
    if source == "codex":
        return sum(
            1
            for r in records
            if r.get("type") == "response_item"
            and isinstance(r.get("payload"), dict)
            and r["payload"].get("type") == "message"
            and r["payload"].get("role") in ("user", "assistant")
        )
    return sum(1 for r in records if r.get("type") in ("user", "assistant"))


def preview(files: list[tuple[str, str]]) -> dict[str, Any]:
    """Pick the first importable file and describe the sessions in it."""
    for name, text in files:
        source = detect(text)
        if source == "hermes":
            sessions = [
                {
                    "id": str(r.get("id") or "?"),
                    "title": str(r.get("title") or "untitled"),
                    "messages": len(r["messages"]),
                }
                for r in parse_export_jsonl(text, origin=name)
            ]
        elif source:
            records = _records(text)
            sessions = [
                {"id": name, "title": _first_user_line(records) or name, "messages": _turns(records, source)}
            ]
        else:
            continue
        return {"name": name, "text": text, "source": source, "sessions": sessions}
    raise ImportFailed(
        "none of those files is a session (looked for a Hermes jsonl export, "
        "a Claude Code JSONL or a Codex rollout)"
    )
