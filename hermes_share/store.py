"""Plugin-data state: the share ledger, pending previews and retained files, all bounded.

JSON writes go through a temporary file and ``os.replace``, so a crash mid-write keeps the previous
document. The data root is re-resolved on every call so a profile switch is honoured, and when the
host cannot resolve it we refuse by name rather than fall back to ``~/.hermes``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import PLUGIN_NAME

LEDGER_MAX_ROWS = 50
PENDING_TTL_SECONDS = 15 * 60
MAX_FILES_PER_DIR = 20


class StateRootUnavailable(RuntimeError):
    """The host could not resolve the plugin-data root; nothing was written."""


def data_dir(sub: str = "") -> Path:
    """``<active hermes home>/plugin-data/hermes-share[/<sub>]``, created on demand."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        path = plugin_data_dir(PLUGIN_NAME) / sub
    except Exception as exc:
        raise StateRootUnavailable(
            f"cannot resolve the Hermes plugin-data directory ({type(exc).__name__})"
        ) from exc
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def trim(directory: Path, keep: int = MAX_FILES_PER_DIR, ttl: float | None = None) -> None:
    """Keep the newest *keep* files in *directory*, and none older than *ttl* seconds."""
    try:
        files = sorted(
            (p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    now = time.time()
    for index, path in enumerate(files):
        if index >= keep or (ttl is not None and now - path.stat().st_mtime > ttl):
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------- ledger


def shares() -> list[dict[str, Any]]:
    """Shared gists, newest first."""
    return [r for r in _read_json(data_dir() / "ledger.json").get("shares") or [] if isinstance(r, dict)]


def _save_shares(rows: list[dict[str, Any]]) -> None:
    _write_json(data_dir() / "ledger.json", {"version": 1, "shares": rows[:LEDGER_MAX_ROWS]})


def add_share(row: dict[str, Any]) -> None:
    _save_shares([row] + [r for r in shares() if r.get("gist_id") != row["gist_id"]])


def mark_revoked(gist_id: str, when: str) -> None:
    rows = shares()
    for row in rows:
        if row.get("gist_id") == gist_id:
            row.update(status="revoked", revoked=when)
    _save_shares(rows)


# ---------------------------------------------------------------- pending previews


def _pending_path(key: str) -> Path:
    """Keyed by a hash so the file name never carries a session id."""
    return data_dir("pending") / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}.json"


def save_pending(key: str, payload: dict[str, Any]) -> None:
    _write_json(_pending_path(key), {**payload, "created": time.time()})
    trim(data_dir("pending"), ttl=PENDING_TTL_SECONDS)


def load_pending(key: str, kind: str) -> dict[str, Any] | None:
    """The pending *kind* preview for *key*, or None when absent, of another kind, or expired."""
    data = _read_json(_pending_path(key))
    created = data.get("created")
    if data.get("kind") != kind or not isinstance(created, (int, float)):
        return None
    if time.time() - created > PENDING_TTL_SECONDS:
        clear_pending(key)
        return None
    return data


def clear_pending(key: str) -> None:
    _pending_path(key).unlink(missing_ok=True)
