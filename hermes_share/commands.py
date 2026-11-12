"""The ``/share`` and ``/unshare`` slash commands.

Handlers take ``raw_args`` plus the session keywords the host passes (``session_id``,
``session_key``, ``platform``). The host object reaches them through :class:`Runtime`, bound once at
registration, so this module imports no Hermes internals.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import PLUGIN_NAME, bundle, gists, hostcli, importing, store

#: Surfaces where the whole preview is read on the screen that confirms it.
LOCAL_SURFACES = frozenset({"local", "cli", "tui", "desktop", "acp", ""})
#: Setting that lets a chat platform import a bundle into this profile's store.
GATEWAY_IMPORT_SETTING = "allow_gateway_import"
#: Setting that lets a chat platform name other sessions (selectors, ``--last``) and list or delete
#: gists it did not create. Off by default: on a gateway the session store holds every platform's and
#: every user's sessions, and the gist account is the owner's.
GATEWAY_SELECTORS_SETTING = "allow_gateway_selectors"
FORMATS = {
    "jsonl": {"jsonl"},
    "md": {"md"},
    "html": {"html"},
    "both": {"jsonl", "md"},
    "all": {"jsonl", "md", "html"},
}

USAGE = """\
/share -- share sessions as a redacted, revocable secret gist, and import them back

  /share                        share this session
  /share <id|title> [...]       share the named sessions (up to 12)
  /share --last N               share the N most recent sessions
  /share --format <kinds>       jsonl, md, html, both (jsonl+md), all (default), or a list
  /share confirm                upload the preview as a SECRET gist
  /share cancel                 delete the preview and forget it
  /share list                   gists this profile has shared
  /share import <gist|file>     read a shared bundle and show what is inside
  /share import confirm         import it (Claude Code, Codex, or a Hermes export)
  /unshare [id|last]            delete a shared gist and mark it revoked

Redaction is always on and cannot be switched off: credentials become non-reusable sentinels, home
paths become ~, and your account and host names are replaced. Nothing is uploaded without `confirm`.
"""
NEEDS_87988 = (
    "this Hermes cannot import a Hermes export yet: `hermes sessions import --from` offers "
    "only claude and codex.\n  It needs session import from Hermes exports, "
    "NousResearch/hermes-agent#87988."
)


class Refused(RuntimeError):
    """A command cannot go ahead; the message is the whole reply."""


class Runtime:
    """What the handlers need from the host, bound once at registration."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def redactor(self) -> Callable[[str], str]:
        method = getattr(self.ctx, "redact_text", None)
        if not callable(method):
            raise Refused(
                "Cannot share: this Hermes has no ctx.redact_text(), the only way this plugin "
                "scrubs a bundle. Nothing was written or uploaded.\n"
                "  Update Hermes to a version that includes ctx.redact_text()."
            )
        return lambda text: method(text, force=True)

    def setting(self, name: str, default: Any = None) -> Any:
        try:
            return self.ctx.get_config(name, default)
        except Exception:
            return default


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _owner_scope(rt: Runtime, platform: str) -> bool:
    """True where the caller may act on the whole profile: a local surface, or the owner allowed it."""
    return platform in LOCAL_SURFACES or rt.setting(GATEWAY_SELECTORS_SETTING, False) is True


def _visible(rows: list[dict[str, Any]], rt: Runtime, platform: str, key: str) -> list[dict[str, Any]]:
    """Ledger rows this caller may see and delete: all of them locally, else only this chat's own."""
    if _owner_scope(rt, platform):
        return rows
    return [r for r in rows if key and r.get("session_key") == key]


def _size(paths: list[Path]) -> str:
    n = sum(p.stat().st_size for p in paths if p.is_file())
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 2**20 else f"{n / 2**20:.1f} MB"


def _formats(token: str) -> frozenset[str]:
    kinds = set().union(*(FORMATS.get(part.strip(), {part.strip()}) for part in token.lower().split(",")))
    if not kinds or kinds - FORMATS["all"]:
        raise Refused(f"unknown format {token!r}: use md, html, jsonl, both, all, or a comma-separated list")
    return frozenset(kinds)


def _parse(raw: str) -> dict[str, Any]:
    """``[selector ...] [--last N] [--format K]`` or a subcommand and its rest."""
    tokens = raw.split()
    parsed: dict[str, Any] = {"sub": "", "rest": "", "selectors": [], "last": 0, "formats": FORMATS["all"]}
    while tokens:
        token = tokens.pop(0)
        flag, _, value = token.partition("=")
        if flag in ("--last", "--format"):
            value = value or (tokens.pop(0) if tokens else "")
            if flag == "--format":
                parsed["formats"] = _formats(value)
            elif value.isdigit() and int(value) > 0:
                parsed["last"] = int(value)
            else:
                raise Refused(f"--last needs a positive number, got {value!r}")
        elif not parsed["selectors"] and token in ("confirm", "cancel", "list", "help", "import"):
            return {**parsed, "sub": token, "rest": " ".join(tokens)}
        else:
            parsed["selectors"].append(token)
    return parsed


# ---------------------------------------------------------------- sharing


def _share_preview(rt: Runtime, key: str, session_id: str, parsed: dict[str, Any], platform: str) -> str:
    if (parsed["selectors"] or parsed["last"]) and not _owner_scope(rt, platform):
        raise Refused(
            f"On {platform}, /share shares only this conversation: naming other sessions or using --last "
            "would read every platform's and every user's sessions in this profile.\n"
            f"  The owner can allow it with plugins.entries.{PLUGIN_NAME}.settings."
            f"{GATEWAY_SELECTORS_SETTING}: true. Plain /share works here without it."
        )
    redact = rt.redactor()
    ids = (
        hostcli.session_ids(parsed["selectors"], parsed["last"])
        if parsed["selectors"] or parsed["last"]
        else [session_id]
    )
    if not ids or not ids[0]:
        raise Refused("No session is bound to this command, so there is nothing to share.")
    previews = store.data_dir("previews")
    built = bundle.build(
        ids, redact, previews, f"hermes-sessions-{_dt.datetime.now():%Y%m%d-%H%M%S}", parsed["formats"]
    )
    store.trim(previews, keep=store.MAX_FILES_PER_DIR + len(built["files"]))
    sessions, totals, files = built["sessions"], built["totals"], built["files"]
    titles = ", ".join(s["title"] for s in sessions[:2]) + (", ..." if len(sessions) > 2 else "")
    description = " ".join(built["scrub"].text(f"Hermes sessions: {titles} ({len(sessions)})").split())[:200]
    store.save_pending(
        key,
        {
            "kind": "share",
            "paths": [str(p) for p in files],
            "description": description,
            "formats": sorted(parsed["formats"]),
            "sessions": sessions,
        },
    )
    lines = [
        "/share - preview ready, nothing uploaded",
        "",
        f"  sessions   {len(sessions)}",
        *bundle.summary_lines(sessions),
        "",
        f"  redacted   {totals['secrets']} secret mark(s), {totals['paths']} home path(s), "
        f"{totals['identities']} account/host name(s)",
        f"  files      {', '.join(p.name for p in files)} ({_size(files)} total)",
        f"  gist desc  {description}",
        "",
        "Next: /share confirm   uploads it as a SECRET gist (unlisted, not private)",
        "      /share cancel    deletes the preview and forgets it",
    ]
    warning = gists.missing_gh_hint()
    return "\n".join(lines + (["", f"  ! {warning}"] if warning else []))


def _share_confirm(rt: Runtime, key: str, platform: str) -> str:
    pending = store.load_pending(key, "share")
    if not pending:
        return "There is no pending preview for this session.\n  Run /share first; previews last 15 minutes."
    paths = [Path(p) for p in pending["paths"]]
    if not paths or not all(p.is_file() for p in paths):
        store.clear_pending(key)
        return "The preview files are gone, so nothing was uploaded.\n  Run /share again."
    redact = rt.redactor()
    gists.require_auth(redact)
    url, gist_id = gists.create_secret_gist(pending["description"], [str(p) for p in paths], redact)
    store.add_share(
        {
            "gist_id": gist_id,
            "url": url,
            "sessions": pending["sessions"],
            "files": [p.name for p in paths],
            "format": ",".join(pending["formats"]),
            "created": _now(),
            "status": "shared",
            # Who may delete it from a chat: the conversation that created it (see _visible).
            "session_key": key,
            "platform": platform or "local",
        }
    )
    store.clear_pending(key)
    return "\n".join(
        [
            "Shared as a secret gist:",
            f"  {url}",
            "",
            f"  {len(paths)} file(s), {_size(paths)}: {', '.join(p.name for p in paths)}",
            f"  sessions: {len(pending['sessions'])}",
            "  Secret means unlisted and readable by anyone with the URL - it is not access-controlled.",
            "",
            "Import it in another Hermes with: /share import <url>",
            "Revoke with:                     /unshare last",
        ]
    )


# ---------------------------------------------------------------- import


def _import_allowed(rt: Runtime, platform: str) -> None:
    if platform not in LOCAL_SURFACES and rt.setting(GATEWAY_IMPORT_SETTING, False) is not True:
        raise Refused(
            f"/share import is refused on {platform}: an import writes sessions into this Hermes's own "
            f"store, so it needs the owner's consent.\n  Set plugins.entries.{PLUGIN_NAME}.settings."
            f"{GATEWAY_IMPORT_SETTING}: true to allow it. Sharing and /unshare work here without it."
        )


def _import_preview(rt: Runtime, key: str, target: str, platform: str) -> str:
    _import_allowed(rt, platform)
    found = importing.preview(importing.fetch(target))
    directory = store.data_dir("imports")
    path = directory / (
        "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(found["name"]).name) or "bundle.jsonl"
    )
    path.write_text(found["text"], encoding="utf-8")
    store.trim(directory)
    store.save_pending(
        f"{key}:import",
        {"kind": "import", "path": str(path), "source": found["source"], "sessions": found["sessions"]},
    )
    blocked = found["source"] == "hermes" and not hostcli.import_offers_hermes()
    return "\n".join(
        [
            "/share import - preview, nothing imported yet",
            "",
            f"  source     {importing.LABELS[found['source']]}",
            f"  file       {path.name}",
            f"  sessions   {len(found['sessions'])}",
            *bundle.summary_lines(found["sessions"], secrets=False),
            *(["", f"  ! {NEEDS_87988} Confirming will refuse."] if blocked else []),
            "",
            "Next: /share import confirm   imports it into this Hermes",
            "      /share cancel            forgets it",
        ]
    )


def _import_confirm(rt: Runtime, key: str, platform: str) -> str:
    _import_allowed(rt, platform)
    pending = store.load_pending(f"{key}:import", "import")
    if not pending:
        return "There is no pending import for this session.\n  Run /share import <gist-url-or-file> first."
    path, source = Path(pending["path"]), pending["source"]
    if not path.is_file():
        store.clear_pending(f"{key}:import")
        return "The downloaded file is gone, so nothing was imported.\n  Run /share import again."
    if source == "hermes" and not hostcli.import_offers_hermes():
        return f"Nothing was imported: {NEEDS_87988}\n  /share cancel clears the pending bundle."
    try:
        hostcli.import_file(source, path)
    except hostcli.HostCliError as exc:
        return f"Import failed: {exc}\n  Still pending: /share import confirm retries; /share cancel drops."
    store.clear_pending(f"{key}:import")
    return "\n".join(
        [
            f"Imported {len(pending['sessions'])} session(s) into this Hermes from "
            f"{importing.LABELS[source]}:",
            *(f"  {str(s['id'])[:24]:<24}  {str(s['title'])[:60]}" for s in pending["sessions"]),
            "",
            "Continue one with:  hermes --resume <session-id>",
        ]
    )


# ---------------------------------------------------------------- list / cancel / unshare


def _list(rt: Runtime, platform: str, key: str) -> str:
    whole = _owner_scope(rt, platform)
    rows = _visible(store.shares(), rt, platform, key)
    if not rows:
        return (
            "No gists have been shared from this profile yet."
            if whole
            else "No gists have been shared from this conversation."
        )
    out = [f"Shared gists for this {'profile' if whole else 'conversation'} ({len(rows)}):", ""]
    for r in rows:
        out += [
            f"  {r.get('status', 'shared'):<7}  {r.get('created', '?')}  "
            f"{len(r.get('sessions') or []):>2} session(s)  {r.get('gist_id', '?')}",
            f"            {r.get('url', '')}",
        ]
    return "\n".join(out + ["", "Delete one with: /unshare <gist-id>   or   /unshare last"])


def _cancel(key: str) -> str:
    removed = 0
    for pending_key, kind in ((key, "share"), (f"{key}:import", "import")):
        pending = store.load_pending(pending_key, kind)
        if pending:
            for item in pending.get("paths") or [pending.get("path")]:
                Path(item).unlink(missing_ok=True)
            store.clear_pending(pending_key)
            removed += 1
    return (
        "Preview deleted; nothing was uploaded or imported." if removed else "No pending preview to cancel."
    )


def _unshare(target: str, rt: Runtime, platform: str, key: str) -> str:
    rows = _visible(store.shares(), rt, platform, key)
    if target in ("", "last"):
        row = next((r for r in rows if r.get("status") == "shared"), None)
        if row is None:
            return "No live shared gist to revoke. /share list shows what has been shared."
    else:
        gist_id = gists.gist_id_from_url(target) or target
        row = next((r for r in rows if r.get("gist_id") == gist_id), None)
        if row is None:
            if any(r.get("gist_id") == gist_id for r in store.shares()):
                return (
                    f"{gist_id} is not one this chat shared, so it was not deleted. The owner can remove it "
                    f"from a local session, or allow it here with plugins.entries.{PLUGIN_NAME}.settings."
                    f"{GATEWAY_SELECTORS_SETTING}: true."
                )
            return f"{gist_id} is not in this profile's ledger; nothing was deleted (see /share list)."
        if row.get("status") == "revoked":
            return f"{gist_id} is already revoked; nothing to do."
    gists.require_auth()
    gists.delete_gist(row["gist_id"])
    store.mark_revoked(row["gist_id"], _now())
    return f"Deleted gist {row['gist_id']}. The URL now returns 404."


# ---------------------------------------------------------------- entry points


def _guard(run: Callable[[], str], name: str) -> str:
    """Every failure becomes a reply; a traceback never reaches the user."""
    try:
        return run()
    except Refused as exc:
        return str(exc)
    except (bundle.BundleError, hostcli.HostCliError) as exc:
        return f"Cannot build the bundle: {exc}. Nothing was written or uploaded."
    except (importing.ImportFailed, gists.GistError, store.StateRootUnavailable) as exc:
        return f"/{name}: {exc}"
    except Exception as exc:
        return f"/{name} failed ({type(exc).__name__}: {exc}). Nothing was uploaded or changed."


def handle_share(
    raw_args: str = "",
    *,
    rt: Runtime,
    session_id: str = "",
    session_key: str = "",
    platform: str = "",
    **_kwargs: Any,
) -> str:
    key = session_key or session_id or ""

    def run() -> str:
        parsed = _parse(raw_args or "")
        sub, rest = parsed["sub"], parsed["rest"].strip()
        if sub == "import":
            if rest == "confirm":
                return _import_confirm(rt, key, platform)
            return (
                _import_preview(rt, key, rest, platform)
                if rest
                else "Usage: /share import <gist-url-or-file>   (then /share import confirm)"
            )
        return {
            "help": lambda: USAGE,
            "list": lambda: _list(rt, platform, key),
            "cancel": lambda: _cancel(key),
            "confirm": lambda: _share_confirm(rt, key, platform),
        }.get(sub, lambda: _share_preview(rt, key, session_id, parsed, platform))()

    return _guard(run, "share")


def handle_unshare(
    raw_args: str = "",
    *,
    rt: Runtime,
    session_id: str = "",
    session_key: str = "",
    platform: str = "",
    **_kwargs: Any,
) -> str:
    tokens = (raw_args or "").split()
    if len(tokens) > 1 or tokens[:1] in (["help"], ["-h"], ["--help"]):
        return USAGE
    key = session_key or session_id or ""
    return _guard(lambda: _unshare(tokens[0] if tokens else "", rt, platform, key), "unshare")
