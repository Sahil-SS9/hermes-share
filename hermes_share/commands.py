"""The ``/share`` and ``/unshare`` slash commands.

Both handlers take ``raw_args`` plus whichever session-context keywords the host passes
(``source``, ``session_id``, ``session_key``, ``platform``) -- the seam is
``hermes_cli.plugin_command_dispatch``, and a handler opts in simply by naming them.

Nothing here uploads without ``/share confirm``. The preview is written to plugin-data first, so
the document that will be uploaded can be read, shared or deleted before anything leaves the
machine.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from . import PLUGIN_NAME
from . import gists
from . import session as session_mod
from . import store
from .paths import StateRootUnavailable, data_dir

#: Surfaces where a terminal-side confirm is possible. A messaging chat is refused: the preview has
#: to be read somewhere the user can also inspect the file, and a chat reply cannot carry the first
#: 20 lines plus a confirm round trip without leaking the document into the chat history.
#:
#: ``local`` is what a classic-CLI or TUI session reports once its turn is routed through the
#: gateway (``gateway/config.py::Platform.LOCAL``); ``cli`` is the same session on the in-process
#: dispatch path. Anything not named here refuses, so a surface added later fails closed.
LOCAL_SURFACES = frozenset({"local", "cli", "tui", "desktop", "acp", ""})

USAGE = """\
/share -- share this session as a redacted, revocable secret gist

  /share [md|html|jsonl]   write a redacted preview (md is the default)
  /share confirm           upload the preview as a SECRET gist
  /share cancel            delete the preview and forget it
  /share list              gists this profile has shared
  /unshare [id|last]       delete a shared gist and mark it revoked
  /unshare last            delete the most recent one you shared

Redaction is always on: API keys, tokens, headers, .env lines, home paths, your account name and
hostnames are scrubbed before the preview is written. Nothing is uploaded without `confirm`."""


def _now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _refuse_platform(platform: str) -> str:
    where = platform or "this chat platform"
    return (
        f"/share is not available on {where}.\n"
        "  It runs from the classic CLI (`hermes`), the TUI (`hermes --tui`) and Desktop, where the\n"
        "  preview can be inspected and confirmed. On messaging platforms use /save to write a local\n"
        "  file instead; a gist upload from chat is a follow-up, not a silent half-implementation."
    )


def _gate(platform: str) -> str | None:
    return None if platform in LOCAL_SURFACES else _refuse_platform(platform)


def _pending_key(session_key: str, session_id: str) -> str:
    return session_key or session_id or ""


# ---------------------------------------------------------------- preview

def _preview_text(root: Path, document: str, fmt: str, counts: dict[str, int], description: str,
                  path: Path, warning: str) -> str:
    lines = document.splitlines()
    head = "\n".join(lines[:20])
    if len(lines) > 20:
        head += f"\n... ({len(lines) - 20} more lines)"
    parts = [
        "/share - preview ready, nothing uploaded",
        "",
        f"  file       {path}",
        f"  format     {fmt}",
        f"  size       {_bytes(len(document.encode('utf-8')))}, {len(lines)} lines",
        (
            "  redacted   "
            f"{counts.get('secrets', 0)} secret mark(s), "
            f"{counts.get('paths', 0)} home path(s), "
            f"{counts.get('identities', 0)} account/host name(s)"
        ),
        f"  gist desc  {description}",
        "",
        "  --- first 20 lines ---",
        head,
        "  --- end ---",
        "",
        "Next: /share confirm   uploads it as a SECRET gist (unlisted, not private)",
        "      /share cancel    deletes the preview and forgets it",
    ]
    if warning:
        parts += ["", f"  ! {warning}"]
    return "\n".join(parts)


def _do_preview(session_id: str, pending_key: str, token: str) -> str:
    fmt = session_mod.normalise_format(token)
    root = data_dir()
    data = session_mod.load_session(session_id, fmt)
    document, secrets, extra = session_mod.redact_document(data, fmt)
    document, more = session_mod.redact(document)
    counts = {
        "secrets": secrets + extra + more["secrets"],
        "paths": more["paths"],
        "identities": more["identities"],
    }

    title = _session_title(data, session_id)
    description, _desc_counts = session_mod.redact(f"Hermes session: {title}")
    description = " ".join(description.split())[:200]

    path = root / "previews" / f"hermes_session_{store.session_hash(session_id)}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    store.trim_previews(root)

    warning = gists.missing_gh_hint()
    store.save_pending(
        root,
        pending_key,
        {"path": str(path), "format": fmt, "description": description, "session": store.session_hash(session_id)},
    )
    return _preview_text(root, document, fmt, counts, description, path, warning)


def _session_title(data: dict[str, Any], session_id: str) -> str:
    for key in ("title", "name", "summary"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    started = data.get("started_at")
    stamp = ""
    if isinstance(started, (int, float)):
        try:
            stamp = _dt.datetime.fromtimestamp(float(started)).strftime("%d/%m/%Y")
        except (OverflowError, OSError, ValueError):
            stamp = ""
    return f"untitled session {store.session_hash(session_id)}" + (f" ({stamp})" if stamp else "")


# ---------------------------------------------------------------- confirm

def _do_confirm(pending_key: str) -> str:
    root = data_dir()
    pending = store.load_pending(root, pending_key)
    if not pending:
        return (
            "There is no pending preview for this session.\n"
            "  Run /share to write one first. Previews expire after 15 minutes."
        )
    path = Path(str(pending.get("path") or ""))
    if not path.is_file():
        store.clear_pending(root, pending_key)
        return (
            f"The preview file is gone ({path.name}), so nothing was uploaded.\n"
            "  Run /share again to write a fresh one."
        )

    # Only reach for gh once there is a document to upload: a stale preview must not cause even an
    # authentication probe.
    problem = gists.auth_problem()
    if problem:
        return problem

    document = path.read_text(encoding="utf-8", errors="replace")
    description = str(pending.get("description") or "Hermes session")

    result = gists.create_secret_gist(description, str(path))
    row = {
        "gist_id": result["gist_id"],
        "url": result["url"],
        "session": str(pending.get("session") or ""),
        "format": str(pending.get("format") or ""),
        "created": _now_utc(),
        "status": "shared",
    }
    store.append_share(root, row)
    store.clear_pending(root, pending_key)
    return (
        f"Shared as a secret gist:\n  {result['url']}\n\n"
        f"  {_bytes(len(document.encode('utf-8')))}, format {row['format']}, "
        f"{path.name}\n"
        "  Secret means unlisted and searchable only by URL - it is not access-controlled.\n\n"
        "Revoke with: /unshare last"
    )


# ---------------------------------------------------------------- list / unshare

def _do_list() -> str:
    root = data_dir()
    rows = store.load_ledger(root)["shares"]
    if not rows:
        return "No gists have been shared from this profile yet."
    out = [f"Shared gists for this profile ({len(rows)}):", ""]
    for row in rows:
        status = row.get("status", "shared")
        mark = "revoked" if status == "revoked" else "shared "
        out.append(
            f"  {mark}  {row.get('created', '?')}  {row.get('format', '?'):<5}  "
            f"{row.get('gist_id', '?')}  {row.get('url', '')}"
        )
    out += ["", "Delete one with: /unshare <gist-id>   or   /unshare last"]
    return "\n".join(out)


def _do_unshare(target: str) -> str:
    root = data_dir()
    if target in ("", "last"):
        row = store.latest_live_share(root)
        if row is None:
            return "No live shared gist to revoke. /share list shows what has been shared."
    else:
        gist_id = gists.gist_id_from_url(target) if target.startswith("http") else target.strip()
        row = store.find_share(root, gist_id)
        if row is None:
            return (
                f"{gist_id} is not in this profile's ledger, so nothing was deleted.\n"
                "  /share list shows the ids this plugin created. Gists made by hand are not in it."
            )
        if row.get("status") == "revoked":
            return f"{gist_id} is already marked revoked; nothing to do."

    problem = gists.auth_problem()
    if problem:
        return problem

    gist_id = str(row.get("gist_id") or "")
    gists.delete_gist(gist_id)
    store.update_share(root, gist_id, status="revoked", revoked=_now_utc())
    return f"Deleted gist {gist_id}. The URL now returns 404.\n  Revoked at {_now_utc()}"


# ---------------------------------------------------------------- entry points

def handle_share(raw_args: str = "", *, session_id: str = "", session_key: str = "",
                 platform: str = "", source: Any = None, **_kwargs: Any) -> str:
    """``/share`` -- preview, then confirm."""
    refusal = _gate(platform)
    if refusal:
        return refusal
    tokens = (raw_args or "").split()
    sub = tokens[0].lower() if tokens else ""
    try:
        if sub in ("help", "-h", "--help", "?"):
            return USAGE
        if sub == "confirm":
            return _do_confirm(_pending_key(session_key, session_id))
        if sub == "cancel":
            root = data_dir()
            pending = store.load_pending(root, _pending_key(session_key, session_id))
            if not pending:
                return "No pending preview to cancel."
            try:
                Path(str(pending.get("path") or "")).unlink()
            except OSError:
                pass
            store.clear_pending(root, _pending_key(session_key, session_id))
            return "Preview deleted; nothing was uploaded."
        if sub == "list":
            return _do_list()
        if len(tokens) > 1:
            return USAGE
        return _do_preview(session_id, _pending_key(session_key, session_id), sub)
    except ValueError as exc:
        return f"{exc}\n\n{USAGE}"
    except session_mod.SessionUnavailable as exc:
        return f"Cannot share this session: {exc}."
    except StateRootUnavailable as exc:
        return f"Cannot share this session: {exc}. Nothing was written or uploaded."
    except gists.GistError as exc:
        return f"{exc}"
    except Exception as exc:  # never let a handler traceback reach the terminal uncaught
        return f"/share failed ({type(exc).__name__}: {exc}). Nothing was uploaded."


def handle_unshare(raw_args: str = "", *, session_id: str = "", session_key: str = "",
                   platform: str = "", source: Any = None, **_kwargs: Any) -> str:
    """``/unshare [gist-id|last]`` -- delete a shared gist."""
    refusal = _gate(platform)
    if refusal:
        return refusal
    tokens = (raw_args or "").split()
    try:
        if tokens and tokens[0].lower() in ("help", "-h", "--help", "?"):
            return USAGE
        if len(tokens) > 1:
            return USAGE
        return _do_unshare(tokens[0] if tokens else "")
    except gists.GistError as exc:
        return f"{exc}"
    except StateRootUnavailable as exc:
        return f"Cannot revoke: {exc}."
    except Exception as exc:
        return f"/unshare failed ({type(exc).__name__}: {exc}). Nothing was changed."


__all__ = ["handle_share", "handle_unshare", "USAGE", "PLUGIN_NAME"]
