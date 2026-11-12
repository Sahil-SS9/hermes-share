"""Review of b389b21 (NousResearch/hermes-agent#150473).

2. On a chat platform, selectors and ``--last`` read the whole profile's session store (every
   platform, every user, CLI sessions too), so a second allowed user in a shared chat could publish
   someone else's transcripts. Off the local surfaces only the calling session is shared unless the
   owner turns on ``allow_gateway_selectors``.
3. ``/unshare <id>`` from a chat could delete any gist in the ledger. Each row records the creating
   ``session_key``; a chat removes (and lists) only its own rows unless the owner setting is on.
4. URL import accepts ``https://`` only, redirects included.
"""

from __future__ import annotations

import urllib.request

import pytest
from conftest import SESSION_A, SESSION_B, FakeCtx

from hermes_share import commands, importing, store
from hermes_share.commands import Runtime

OWNER_ON = {"allow_gateway_selectors": True}


def _share(raw: str = "", *, platform: str = "cli", key: str = SESSION_A, settings=None):
    return commands.handle_share(
        raw,
        rt=Runtime(FakeCtx(settings=settings)),
        session_id=key,
        session_key=key,
        platform=platform,
    )


def _unshare(raw: str = "", *, platform: str = "cli", key: str = SESSION_A, settings=None):
    return commands.handle_unshare(
        raw, rt=Runtime(FakeCtx(settings=settings)), session_id=key, session_key=key, platform=platform
    )


def _two_shares(export_double) -> tuple[str, str]:
    """One gist made from the CLI (session A) and one from a Discord chat (session B)."""
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "cli work")
    export_double.state[SESSION_B] = export_double.record(SESSION_B, "chat work")
    _share(platform="cli", key=SESSION_A)
    _share("confirm", platform="cli", key=SESSION_A)
    first = store.shares()[0]["gist_id"]
    _share(platform="discord", key=SESSION_B)
    _share("confirm", platform="discord", key=SESSION_B)
    second = store.shares()[0]["gist_id"]
    return first, second


# ---------------------------------------------------------------- 2. selectors on chat platforms


@pytest.mark.parametrize("raw", [SESSION_A, "--last 2", f"{SESSION_A} {SESSION_B}"])
def test_a_chat_cannot_select_other_sessions_by_default(seeded_store, recording_gh, export_double, raw):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "someone else's work")
    out = _share(raw, platform="discord", key=SESSION_B)
    assert "allow_gateway_selectors" in out
    assert "preview ready" not in out
    assert store.load_pending(SESSION_B, "share") is None


def test_a_chat_can_still_share_its_own_session(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_B] = export_double.record(SESSION_B, "mine")
    assert "preview ready" in _share(platform="discord", key=SESSION_B)


def test_the_owner_setting_allows_selectors_on_a_chat(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "a")
    assert "preview ready" in _share(SESSION_A, platform="discord", key=SESSION_B, settings=OWNER_ON)


def test_selectors_still_work_locally(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_B] = export_double.record(SESSION_B, "b")
    assert "preview ready" in _share(SESSION_B, platform="cli", key=SESSION_A)


# ---------------------------------------------------------------- 3. unshare / list scope


def test_each_ledger_row_records_the_creating_session_key(seeded_store, recording_gh, export_double):
    _two_shares(export_double)
    keys = [row.get("session_key") for row in store.shares()]
    assert keys == [SESSION_B, SESSION_A]


def test_a_chat_cannot_unshare_a_gist_it_did_not_create(seeded_store, recording_gh, export_double):
    cli_gist, _ = _two_shares(export_double)
    out = _unshare(cli_gist, platform="discord", key=SESSION_B)
    assert "not one this chat shared" in out
    assert not [c for c in recording_gh.calls if len(c) > 2 and c[2] == "delete"]
    assert {r["gist_id"]: r["status"] for r in store.shares()}[cli_gist] == "shared"


def test_unshare_last_on_a_chat_means_this_chats_last(seeded_store, recording_gh, export_double):
    cli_gist, chat_gist = _two_shares(export_double)
    # Make the CLI gist the newest row: "last" must still pick the chat's own.
    rows = store.shares()
    store._save_shares([rows[1], rows[0]])
    _unshare("last", platform="discord", key=SESSION_B)
    status = {r["gist_id"]: r["status"] for r in store.shares()}
    assert status == {cli_gist: "shared", chat_gist: "revoked"}


def test_a_chat_can_unshare_its_own_gist(seeded_store, recording_gh, export_double):
    _, chat_gist = _two_shares(export_double)
    assert "404" in _unshare(chat_gist, platform="discord", key=SESSION_B)


def test_the_owner_setting_lets_a_chat_unshare_any_gist(seeded_store, recording_gh, export_double):
    cli_gist, _ = _two_shares(export_double)
    assert "404" in _unshare(cli_gist, platform="discord", key=SESSION_B, settings=OWNER_ON)


def test_a_row_without_a_session_key_is_local_only(seeded_store, recording_gh, export_double):
    cli_gist, _ = _two_shares(export_double)
    rows = store.shares()
    for row in rows:
        row.pop("session_key", None)  # a ledger written by 0.2.0
    store._save_shares(rows)
    assert "not one this chat shared" in _unshare(cli_gist, platform="discord", key=SESSION_B)
    assert "404" in _unshare(cli_gist, platform="cli", key=SESSION_A)


def test_a_chat_lists_only_its_own_gists(seeded_store, recording_gh, export_double):
    cli_gist, chat_gist = _two_shares(export_double)
    chat = _share("list", platform="discord", key=SESSION_B)
    assert chat_gist in chat and cli_gist not in chat
    local = _share("list", platform="cli", key=SESSION_A)
    assert chat_gist in local and cli_gist in local


# ---------------------------------------------------------------- 4. https only


def test_plain_http_import_is_refused():
    with pytest.raises(importing.ImportFailed, match="https"):
        importing.fetch("http://example.invalid/bundle.jsonl")


def test_a_redirect_to_plain_http_is_refused():
    handler = importing._HttpsOnlyRedirects()
    request = urllib.request.Request("https://example.invalid/a")
    with pytest.raises(importing.ImportFailed, match="https"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://example.invalid/b")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://example.invalid/b") is not None
