"""Pull a shared bundle back in: fetch it, say what is inside, import only on confirm.

The format is decided from the CONTENT of each candidate file, never from its name or the URL. A
gist carries two files (jsonl and markdown); only the one that parses as session records is offered
for import, so the readable copy can never be imported as if it were a transcript.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
_GIST_RE = re.compile(r"^https?://gist\.github\.com/(?:[A-Za-z0-9_.-]+/)?([0-9a-fA-F]{8,})$")


class ImportError_(RuntimeError):
    """The target cannot be read or is not a session bundle; safe to show the user."""


class NamedText:
    __slots__ = ("name", "text")

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text


def gist_id_from_url(url: str) -> Optional[str]:
    match = _GIST_RE.match((url or "").strip())
    return match.group(1) if match else None


def _gh_gist_files(gist_id: str) -> list[NamedText]:
    from . import gists

    if not gists.find_gh():
        raise ImportError_("the GitHub CLI (gh) is not installed, so a private gist cannot be read")
    problem = gists.auth_problem()
    if problem:
        raise ImportError_(
            "gh must be signed in to read a gist (a secret gist is not a public URL).\n"
            "  Run: gh auth login"
        )
    listing = gists.list_gist_files(gist_id)
    if not listing:
        raise ImportError_(f"gist {gist_id} has no files, or gh could not read it")
    files: list[NamedText] = []
    for name in listing:
        text = gists.read_gist_file(gist_id, name)
        if text is None:
            continue
        files.append(NamedText(name, text))
        if len(text.encode("utf-8")) > MAX_DOWNLOAD_BYTES:
            raise ImportError_(f"{name} is past the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap")
    if not files:
        raise ImportError_(f"gist {gist_id} could not be read")
    return files


def _https_file(url: str) -> list[NamedText]:
    if not url.lower().startswith(("https://", "http://")):
        raise ImportError_(f"not a URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "hermes-share"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ImportError_(f"{url} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ImportError_(f"could not download {url} ({type(exc).__name__})") from exc
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ImportError_(f"{url} is past the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap")
    name = url.rstrip("/").rsplit("/", 1)[-1] or "download"
    return [NamedText(name, raw.decode("utf-8", errors="replace"))]


def _local_file(target: str) -> list[NamedText]:
    path = Path(target).expanduser()
    if not path.is_file():
        raise ImportError_(f"file not found: {target}")
    try:
        if path.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ImportError_(f"{path.name} is past the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB cap")
        return [NamedText(path.name, path.read_text(encoding="utf-8", errors="replace"))]
    except OSError as exc:
        raise ImportError_(f"could not read {path.name} ({type(exc).__name__})") from exc


def fetch(target: str) -> list[NamedText]:
    """Files behind a gist URL, an http(s) URL, or a local path."""
    token = (target or "").strip()
    if not token:
        raise ImportError_("nothing to import: pass a gist URL or a file path")
    if gist_id_from_url(token):
        return _gh_gist_files(gist_id_from_url(token) or "")
    if token.lower().startswith(("https://", "http://")):
        return _https_file(token)
    return _local_file(token)


def _records(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            return []
        if not isinstance(value, dict):
            return []
        out.append(value)
    return out


def detect(text: str) -> Optional[str]:
    """``hermes`` / ``claude`` / ``codex`` from the content, or None."""
    records = _records(text)
    if not records:
        return None
    if all(isinstance(record.get("messages"), list) for record in records) and any(
        isinstance(record.get("messages"), list) and record.get("messages") for record in records
    ):
        return "hermes"
    kinds = {str(record.get("type") or record.get("record_type") or "") for record in records}
    if kinds & {"response_item", "state", "session_meta", "turn_context"}:
        return "codex"
    if kinds & {"user", "assistant", "system", "summary"} and any(
        "message" in record or "sessionId" in record for record in records
    ):
        return "claude"
    return None


def _first_user_line(text: str) -> str:
    for record in _records(text):
        if str(record.get("type") or "") != "user":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip().splitlines()[0][:80]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip():
                    return block["text"].strip().splitlines()[0][:80]
    return ""


def preview(files: list[NamedText]) -> dict[str, Any]:
    """Pick the importable file and describe what it holds."""
    for candidate in files:
        source = detect(candidate.text)
        if not source:
            continue
        if source == "hermes":
            from . import bundle as bundle_mod

            records = bundle_mod.parse_export_jsonl(candidate.text, origin=candidate.name)
            sessions = [{
                "id": str(record.get("id") or "?"),
                "title": str(record.get("title") or "untitled"),
                "messages": len(record.get("messages") or []),
                "source": str(record.get("source") or "?"),
            } for record in records]
        else:
            records = _records(candidate.text)
            turns = sum(1 for record in records if str(record.get("type") or "") in ("user", "assistant"))
            sessions = [{
                "id": candidate.name,
                "title": _first_user_line(candidate.text) or candidate.name,
                "messages": turns,
                "source": "Claude Code" if source == "claude" else "Codex CLI",
            }]
        return {"file": candidate, "source": source, "sessions": sessions}
    raise ImportError_(
        "none of the files in that target is a session bundle "
        "(looked for a Hermes jsonl export, a Claude Code JSONL or a Codex rollout)"
    )


def write_import_file(name: str, text: str, directory: Path) -> Path:
    """Persist the importable file so the CLI can read it, with a safe basename."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name) or "bundle.jsonl"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe
    path.write_text(text, encoding="utf-8")
    return path
