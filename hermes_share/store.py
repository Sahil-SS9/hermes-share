"""Bounded, atomically-written JSON state: the share ledger and the pending previews.

Both files live under the plugin-data directory and both are capped, so a long-lived profile
cannot grow them without bound. Writes go through a temporary file in the same directory and
``os.replace``, so a crash mid-write leaves the previous document intact.
"""

from __future__ import annotations

import json
import os
import hashlib
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

LEDGER_FILE = "ledger.json"
LEDGER_MAX_ROWS = 50
PENDING_TTL_SECONDS = 15 * 60
PENDING_MAX_FILES = 20
PREVIEW_MAX_FILES = 20


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError:
        return default
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return loaded if isinstance(loaded, dict) else default


def session_hash(session_id: str) -> str:
    """A stable, opaque handle for a session id -- the ledger never stores the id itself."""
    return hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- ledger

def ledger_path(root: Path) -> Path:
    return root / LEDGER_FILE


def load_ledger(root: Path) -> dict[str, Any]:
    data = _read_json(ledger_path(root), {"version": 1, "shares": []})
    shares = data.get("shares")
    if not isinstance(shares, list):
        shares = []
    data["shares"] = [row for row in shares if isinstance(row, dict)]
    data.setdefault("version", 1)
    return data


def append_share(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Add one share row, newest first, trimmed to :data:`LEDGER_MAX_ROWS`."""
    data = load_ledger(root)
    rows = [r for r in data["shares"] if r.get("gist_id") != row.get("gist_id")]
    rows.insert(0, row)
    data["shares"] = rows[:LEDGER_MAX_ROWS]
    _write_json(ledger_path(root), data)
    return data


def update_share(root: Path, gist_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    data = load_ledger(root)
    for row in data["shares"]:
        if row.get("gist_id") == gist_id:
            row.update(fields)
            _write_json(ledger_path(root), data)
            return row
    return None


def find_share(root: Path, gist_id: str) -> Optional[dict[str, Any]]:
    for row in load_ledger(root)["shares"]:
        if row.get("gist_id") == gist_id:
            return row
    return None


def latest_live_share(root: Path) -> Optional[dict[str, Any]]:
    """Newest row that has not been revoked yet."""
    for row in load_ledger(root)["shares"]:
        if row.get("status", "shared") == "shared":
            return row
    return None


# ---------------------------------------------------------------- pending previews

def pending_path(root: Path, key: str) -> Path:
    return root / "pending" / f"{session_hash(key)}.json"


def save_pending(root: Path, key: str, payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["created"] = time.time()
    path = pending_path(root, key)
    _write_json(path, payload)
    trim_pending(root)


def load_pending(root: Path, key: str) -> Optional[dict[str, Any]]:
    """The pending preview for *key*, or None when it is absent or older than the TTL."""
    data = _read_json(pending_path(root, key), {})
    created = data.get("created")
    if not isinstance(created, (int, float)):
        return None
    if time.time() - float(created) > PENDING_TTL_SECONDS:
        clear_pending(root, key)
        return None
    return data


def clear_pending(root: Path, key: str) -> None:
    try:
        pending_path(root, key).unlink()
    except OSError:
        pass


def trim_pending(root: Path) -> None:
    """Expire stale pendings and cap the directory at :data:`PENDING_MAX_FILES`."""
    directory = root / "pending"
    try:
        entries = [p for p in directory.iterdir() if p.suffix == ".json"]
    except OSError:
        return
    now = time.time()
    live: list[tuple[float, Path]] = []
    for path in entries:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age > PENDING_TTL_SECONDS:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        live.append((age, path))
    for _age, path in sorted(live, reverse=True)[PENDING_MAX_FILES:]:
        try:
            path.unlink()
        except OSError:
            pass


def trim_previews(root: Path) -> None:
    """Cap retained preview documents, newest first."""
    directory = root / "previews"
    try:
        files = [p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[PREVIEW_MAX_FILES:]:
        try:
            path.unlink()
        except OSError:
            pass
