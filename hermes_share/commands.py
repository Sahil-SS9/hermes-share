"""The ``/share`` and ``/unshare`` slash commands.

Both handlers take ``raw_args`` plus whichever session-context keywords the host passes (``source``,
``session_id``, ``session_key``, ``platform``). Everything host-specific arrives through a
:class:`Runtime` bound at registration time, so this module imports no Hermes internals: redaction is
``ctx.redact_text``, exports and imports are the public CLI, storage is the plugin-data directory.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Optional

from . import PLUGIN_NAME
from . import bundle as bundle_mod
from . import gists
from . import hostcli
from . import importing
from . import store
from .paths import StateRootUnavailable, data_dir, imports_dir

#: Surfaces where the whole preview can be read on the same screen that confirms it.
LOCAL_SURFACES = frozenset({"local", "cli", "tui", "desktop", "acp", ""})
#: Setting that lets a chat platform import a bundle into this profile's store.
GATEWAY_IMPORT_SETTING = "allow_gateway_import"

USAGE = """\
/share -- share sessions as a redacted, revocable secret gist, and import them back

  /share                        share this session
  /share <id|title> [...]       share the named sessions (up to 12)
  /share --last N               share the N most recent sessions
  /share --format md|jsonl|both which files the gist carries (default: both)
  /share confirm                upload the preview as a SECRET gist
  /share cancel                 delete the preview and forget it
  /share list                   gists this profile has shared
  /share import <gist|file>     read a shared bundle and show what is inside
  /share import confirm         import it (Claude Code, Codex, or a Hermes export)
  /unshare [id|last]            delete a shared gist and mark it revoked

Redaction is always on and cannot be switched off: credentials become non-reusable sentinels, home
paths become ~, and your account and host names are replaced. Nothing is uploaded without `confirm`.
"""


class RedactUnavailable(RuntimeError):
    """The host does not expose ``ctx.redact_text``; sharing would upload raw text."""


class Runtime:
    """What the handlers need from the host, bound once at registration."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def redactor(self) -> bundle_mod.Redactor:
        method = getattr(self.ctx, "redact_text", None)
        if not callable(method):
            raise RedactUnavailable(
                "this Hermes does not expose ctx.redact_text(), and without it there is no way to "
                "scrub a bundle except by importing an internal module, which this plugin does "
                "not do. Nothing was written or uploaded.\n"
                "  Update Hermes to a version that includes ctx.redact_text()."
            )

        def _redact(text: str) -> str:
            return method(text, force=True)

        return _redact

    def setting(self, name: str, default: Any = None) -> Any:
        getter = getattr(self.ctx, "get_config", None)
        if not callable(getter):
            return default
        try:
            return getter(name, default)
        except Exception:
            return default


def _now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bytes(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / (1024 * 1024):.1f} MB"


def is_local(platform: str) -> bool:
    return platform in LOCAL_SURFACES


def _pending_key(session_key: str, session_id: str) -> str:
    return session_key or session_id or ""


# ---------------------------------------------------------------- sharing

def _check_format(token: str) -> str:
    value = (token or "").strip().lower()
    if value not in ("md", "jsonl", "both"):
        raise ValueError(f"unknown format {value!r} - use md, jsonl or both")
    return value


def _parse_share_args(raw: str) -> dict[str, Any]:
    tokens = (raw or "").split()
    parsed: dict[str, Any] = {"sub": "", "selectors": [], "last": 0, "format": "both", "rest": ""}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--last" and index + 1 < len(tokens):
            try:
                parsed["last"] = max(1, int(tokens[index + 1]))
            except ValueError:
                raise ValueError(f"--last needs a number, got {tokens[index + 1]!r}")
            index += 2
            continue
        if token.startswith("--last="):
            try:
                parsed["last"] = max(1, int(token.split("=", 1)[1]))
            except ValueError:
                raise ValueError(f"--last needs a number, got {token!r}")
            index += 1
            continue
        if token == "--format" and index + 1 < len(tokens):
            parsed["format"] = _check_format(tokens[index + 1])
            index += 2
            continue
        if token.startswith("--format="):
            parsed["format"] = _check_format(token.split("=", 1)[1])
            index += 1
            continue
        if not parsed["sub"] and token in ("confirm", "cancel", "list", "help", "import"):
            parsed["sub"] = token
            parsed["rest"] = " ".join(tokens[index + 1:])
            return parsed
        parsed["selectors"].append(token)
        index += 1
    return parsed


def _bundle_files(root: Path, bundle: dict[str, Any], fmt: str) -> list[Path]:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = root / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    if fmt in ("jsonl", "both"):
        path = directory / f"hermes-sessions-{stamp}.jsonl"
        path.write_text(bundle["jsonl"], encoding="utf-8")
        paths.append(path)
    if fmt in ("md", "both"):
        path = directory / f"hermes-sessions-{stamp}.md"
        path.write_text(bundle["markdown"], encoding="utf-8")
        paths.append(path)
    for path in paths:
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return paths


def _preview_text(bundle: dict[str, Any], paths: list[Path], description: str,
                  warning: str) -> str:
    totals = bundle["totals"]
    total_bytes = len(bundle["jsonl"].encode("utf-8")) + len(bundle["markdown"].encode("utf-8"))
    lines = ["/share - preview ready, nothing uploaded", "", f"  sessions   {len(bundle['sessions'])}"]
    lines.extend(bundle_mod.summary_lines(bundle))
    lines += [
        "",
        (f"  redacted   {totals['secrets']} secret mark(s), {totals['paths']} home path(s), "
         f"{totals['identities']} account/host name(s)"),
        f"  files      {', '.join(path.name for path in paths)} ({_bytes(total_bytes)} total)",
        f"  gist desc  {description}",
        "",
        "Next: /share confirm   uploads it as a SECRET gist (unlisted, not private)",
        "      /share cancel    deletes the preview and forgets it",
    ]
    if warning:
        lines += ["", f"  ! {warning}"]
    return "\n".join(lines)


def _resolve_ids(selectors: list[str], last: int, current: str) -> list[str]:
    if last:
        with hostcli.open_session_index() as db:
            ids = hostcli.recent_session_ids(db, last)
        if not ids:
            raise bundle_mod.BundleError("no sessions found to share")
        return ids
    if not selectors:
        if not current:
            raise bundle_mod.BundleError(
                "no session is bound to this command, so there is nothing to share"
            )
        return [current]
    resolved: list[str] = []
    missing: list[str] = []
    with hostcli.open_session_index() as db:
        for selector in selectors:
            found = hostcli.resolve_selector(db, selector)
            if found:
                if found not in resolved:
                    resolved.append(found)
            else:
                missing.append(selector)
    if missing:
        raise bundle_mod.BundleError(
            "no single session matches " + ", ".join(repr(item) for item in missing)
            + " (pass an id, an unambiguous id prefix, or a distinctive title fragment)"
        )
    return resolved


def _do_preview(rt: Runtime, *, session_id: str, pending_key: str, parsed: dict[str, Any]) -> str:
    redact = rt.redactor()
    ids = _resolve_ids(parsed["selectors"], parsed["last"], session_id)
    built = bundle_mod.build(ids, redact)
    root = data_dir()
    paths = _bundle_files(root, built, parsed["format"])
    store.trim_previews(root)

    titles = ", ".join(item["title"] for item in built["sessions"][:2])
    if len(built["sessions"]) > 2:
        titles += ", ..."
    description, _counts = bundle_mod.redact_text(
        f"Hermes sessions: {titles} ({len(built['sessions'])})", redact, bundle_mod.identity_tokens()
    )
    description = " ".join(description.split())[:200]
    warning = gists.missing_gh_hint()
    store.save_pending(
        root,
        pending_key,
        {
            "kind": "share",
            "paths": [str(path) for path in paths],
            "format": parsed["format"],
            "description": description,
            "sessions": built["sessions"],
        },
    )
    return _preview_text(built, paths, description, warning)


def _do_confirm(rt: Runtime, *, pending_key: str, platform: str) -> str:
    root = data_dir()
    pending = store.load_pending(root, pending_key)
    if not pending or pending.get("kind") != "share":
        return (
            "There is no pending preview for this session.\n"
            "  Run /share to write one first. Previews expire after 15 minutes."
        )
    paths = [Path(str(item)) for item in pending.get("paths") or []]
    missing = [path.name for path in paths if not path.is_file()]
    if missing or not paths:
        store.clear_pending(root, pending_key)
        return (
            "The preview file is gone (" + (", ".join(missing) or "none recorded") + "), so nothing "
            "was uploaded.\n  Run /share again to write a fresh one."
        )
    redact = rt.redactor()
    problem = gists.auth_problem(redact=redact)
    if problem:
        return problem

    result = gists.create_secret_gist(
        str(pending.get("description") or "Hermes sessions"), [str(path) for path in paths],
        redact=redact,
    )
    row = {
        "gist_id": result["gist_id"],
        "url": result["url"],
        "sessions": pending.get("sessions") or [],
        "files": [path.name for path in paths],
        "format": str(pending.get("format") or ""),
        "created": _now_utc(),
        "status": "shared",
    }
    store.append_share(root, row)
    store.clear_pending(root, pending_key)
    total = sum(path.stat().st_size for path in paths)
    lines = [
        "Shared as a secret gist:",
        f"  {result['url']}",
        "",
        f"  {len(paths)} file(s), {_bytes(total)}: {', '.join(path.name for path in paths)}",
        f"  sessions: {len(row['sessions'])}",
        "  Secret means unlisted and searchable only by URL - it is not access-controlled.",
        "",
        "Import it in another Hermes with: /share import <url>",
    ]
    if is_local(platform):
        lines.append("Revoke with:                     /unshare last")
    return "\n".join(lines)


# ---------------------------------------------------------------- import

def _gateway_import_allowed(rt: Runtime, platform: str) -> Optional[str]:
    if is_local(platform):
        return None
    if rt.setting(GATEWAY_IMPORT_SETTING, False) is True:
        return None
    return (
        f"/share import is refused on {platform or 'this platform'}: an import writes sessions into "
        "this Hermes's own store, so it needs explicit owner consent.\n"
        f"  Set plugins.entries.{PLUGIN_NAME}.settings.{GATEWAY_IMPORT_SETTING}: true to allow it. "
        "Sharing and /unshare work here without that setting."
    )


def _do_import_preview(rt: Runtime, *, target: str, pending_key: str, platform: str) -> str:
    refusal = _gateway_import_allowed(rt, platform)
    if refusal:
        return refusal
    files = importing.fetch(target)
    found = importing.preview(files)
    path = importing.write_import_file(found["file"].name, found["file"].text, imports_dir())
    root = data_dir()
    store.trim_imports(root)
    store.save_pending(
        root,
        f"{pending_key}:import",
        {"kind": "import", "path": str(path), "source": found["source"],
         "sessions": found["sessions"], "target": target},
    )
    label = {"hermes": "Hermes export", "claude": "Claude Code", "codex": "Codex CLI"}[found["source"]]
    lines = [
        "/share import - preview, nothing imported yet",
        "",
        f"  source     {label}",
        f"  file       {found['file'].name}",
        f"  sessions   {len(found['sessions'])}",
    ]
    for item in found["sessions"]:
        lines.append(
            f"  {str(item['id'])[:24]:<24}  {item['messages']:>4} msg  {str(item['title'])[:60]}"
        )
    if found["source"] == "hermes" and not hostcli.import_help_offers_hermes():
        lines += [
            "",
            "  ! this Hermes cannot import a Hermes export yet: `hermes sessions import --from`",
            "    offers only claude and codex. It needs a Hermes that includes session import from",
            "    Hermes exports, NousResearch/hermes-agent#87988. Confirming will refuse.",
        ]
    lines += [
        "",
        "Next: /share import confirm   imports it into this Hermes",
        "      /share cancel            forgets it",
    ]
    return "\n".join(lines)


def _do_import_confirm(rt: Runtime, *, pending_key: str, platform: str) -> str:
    refusal = _gateway_import_allowed(rt, platform)
    if refusal:
        return refusal
    root = data_dir()
    pending = store.load_pending(root, f"{pending_key}:import")
    if not pending or pending.get("kind") != "import":
        return (
            "There is no pending import for this session.\n"
            "  Run /share import <gist-url-or-file> first."
        )
    source = str(pending.get("source") or "")
    path = Path(str(pending.get("path") or ""))
    if not path.is_file():
        store.clear_pending(root, f"{pending_key}:import")
        return "The downloaded file is gone, so nothing was imported.\n  Run /share import again."
    if source == "hermes" and not hostcli.import_help_offers_hermes():
        return (
            "This Hermes cannot import a Hermes export: `hermes sessions import --from` offers only "
            "claude and codex.\n"
            "  It needs a Hermes that includes session import from Hermes exports, "
            "NousResearch/hermes-agent#87988.\n"
            "  Nothing was imported. /share cancel clears the pending bundle."
        )
    hostcli.import_file(source, str(path))
    store.clear_pending(root, f"{pending_key}:import")
    sessions = pending.get("sessions") or []
    lines = [f"Imported {len(sessions)} session(s) into this Hermes from {source}:"]
    for item in sessions:
        lines.append(f"  {str(item['id'])[:24]:<24}  {str(item['title'])[:60]}")
    lines += ["", "Continue one with:  hermes --resume <session-id>"]
    return "\n".join(lines)


# ---------------------------------------------------------------- list / unshare

def _do_list() -> str:
    root = data_dir()
    rows = store.load_ledger(root)["shares"]
    if not rows:
        return "No gists have been shared from this profile yet."
    out = [f"Shared gists for this profile ({len(rows)}):", ""]
    for row in rows:
        status = "revoked" if row.get("status") == "revoked" else "shared "
        sessions = row.get("sessions") or []
        out.append(
            f"  {status}  {row.get('created', '?')}  {len(sessions):>2} session(s)  "
            f"{row.get('gist_id', '?')}"
        )
        out.append(f"            {row.get('url', '')}")
    out += ["", "Delete one with: /unshare <gist-id>   or   /unshare last"]
    return "\n".join(out)


def _do_cancel(pending_key: str) -> str:
    root = data_dir()
    removed = 0
    for suffix in ("", ":import"):
        pending = store.load_pending(root, f"{pending_key}{suffix}")
        if not pending:
            continue
        for item in list(pending.get("paths") or []) + [pending.get("path")]:
            if item:
                try:
                    Path(str(item)).unlink()
                except OSError:
                    pass
        store.clear_pending(root, f"{pending_key}{suffix}")
        removed += 1
    return "Preview deleted; nothing was uploaded." if removed else "No pending preview to cancel."


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
                "  /share list shows the ids this plugin created."
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

def handle_share(raw_args: str = "", *, rt: Runtime, session_id: str = "", session_key: str = "",
                 platform: str = "", source: Any = None, **_kwargs: Any) -> str:
    """``/share`` -- preview, then confirm."""
    try:
        parsed = _parse_share_args(raw_args)
    except ValueError as exc:
        return f"{exc}\n\n{USAGE}"
    sub = parsed["sub"]
    pending_key = _pending_key(session_key, session_id)
    try:
        if sub == "help":
            return USAGE
        if sub == "list":
            return _do_list()
        if sub == "confirm":
            return _do_confirm(rt, pending_key=pending_key, platform=platform)
        if sub == "cancel":
            return _do_cancel(pending_key)
        if sub == "import":
            target = parsed["rest"].strip()
            if target == "confirm":
                return _do_import_confirm(rt, pending_key=pending_key, platform=platform)
            if not target:
                return "Usage: /share import <gist-url-or-file>   (then /share import confirm)"
            return _do_import_preview(rt, target=target, pending_key=pending_key, platform=platform)
        return _do_preview(rt, session_id=session_id, pending_key=pending_key, parsed=parsed)
    except RedactUnavailable as exc:
        return f"Cannot share: {exc}"
    except bundle_mod.BundleError as exc:
        return f"Cannot build the bundle: {exc}. Nothing was written or uploaded."
    except hostcli.HostCliError as exc:
        return f"Cannot read the session: {exc}. Nothing was uploaded."
    except importing.ImportError_ as exc:
        return f"Cannot import: {exc}"
    except gists.GistError as exc:
        return f"{exc}"
    except StateRootUnavailable as exc:
        return f"Cannot use the plugin-data directory: {exc}. Nothing was written or uploaded."
    except Exception as exc:  # never let a handler traceback reach the terminal uncaught
        return f"/share failed ({type(exc).__name__}: {exc}). Nothing was uploaded."


def handle_unshare(raw_args: str = "", *, rt: Runtime, session_id: str = "", session_key: str = "",
                   platform: str = "", source: Any = None, **_kwargs: Any) -> str:
    """``/unshare [gist-id|last]`` -- delete a shared gist."""
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


__all__ = ["handle_share", "handle_unshare", "Runtime", "USAGE", "GATEWAY_IMPORT_SETTING",
           "PLUGIN_NAME"]
