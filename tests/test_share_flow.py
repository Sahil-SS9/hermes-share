"""The /share and /unshare command flow: selectors, preview, confirm gate, ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SESSION_A, SESSION_B, FakeCtx

from hermes_share import commands, gists, store
from hermes_share.commands import Runtime


def _share(raw: str = "", *, ctx=None, **kwargs):
    return commands.handle_share(
        raw, rt=Runtime(ctx or FakeCtx()), session_id=kwargs.pop("session_id", SESSION_A),
        session_key=kwargs.pop("session_key", SESSION_A), **kwargs,
    )


def _unshare(raw: str = "", *, ctx=None, session_id: str = SESSION_A):
    return commands.handle_unshare(raw, rt=Runtime(ctx or FakeCtx()), session_id=session_id,
                                   session_key=session_id, platform="cli")


def test_current_session_preview_writes_both_files(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hello")
    out = _share()
    assert "preview ready, nothing uploaded" in out
    assert ".jsonl" in out and ".md" in out

    pending = store.load_pending(commands.data_dir(), SESSION_A)
    assert pending and pending["kind"] == "share"
    for item in pending["paths"]:
        assert Path(item).is_file()


def test_preview_starts_no_child_process_and_uploads_nothing(seeded_store, monkeypatch, export_double):
    from hermes_share import hostcli

    spawned: list[list[str]] = []
    monkeypatch.setattr(hostcli, "export_session_jsonl", lambda sid: export_double.record(sid, "x") + "\n")
    created: list = []
    monkeypatch.setattr(gists, "find_gh", lambda: None)
    monkeypatch.setattr(gists, "_run", lambda argv, *, timeout: created.append(argv))

    out = _share()
    assert "not installed" in out  # the preview warns early, from a PATH lookup only
    assert created == []


def test_confirm_uploads_one_multi_file_gist(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hello")
    _share()
    out = _share("confirm")
    assert "https://gist.github.com/testuser/abc123def456" in out
    creates = [c for c in recording_gh.calls if len(c) > 2 and c[2] == "create"]
    assert len(creates) == 1
    argv = creates[0]
    assert "--public" not in argv and "-p" not in argv
    assert sum(1 for part in argv if part.endswith((".jsonl", ".md"))) == 2


def test_confirm_without_preview_uploads_nothing(seeded_store, recording_gh):
    out = _share("confirm")
    assert "no pending preview" in out
    assert recording_gh.calls == []


def test_cancel_deletes_the_files(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hello")
    _share()
    pending = store.load_pending(commands.data_dir(), SESSION_A)
    paths = [Path(item) for item in pending["paths"]]
    assert all(path.is_file() for path in paths)

    assert "nothing was uploaded" in _share("cancel")
    assert not any(path.exists() for path in paths)
    assert recording_gh.calls == []


def test_last_n_shares_a_bundle(seeded_store, recording_gh, export_double):
    for session_id in (SESSION_A, SESSION_B):
        export_double.state[session_id] = export_double.record(session_id, "hi")
    out = _share("--last 2")
    assert "sessions   2" in out
    pending = store.load_pending(commands.data_dir(), SESSION_A)
    assert len(pending["sessions"]) == 2


def test_selectors_accept_an_id(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_B] = export_double.record(SESSION_B, "hi")
    out = _share(SESSION_B)
    assert "sessions   1" in out
    pending = store.load_pending(commands.data_dir(), SESSION_A)
    assert pending["sessions"][0]["id"] == SESSION_B


def test_selectors_accept_a_title_fragment(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_B] = export_double.record(SESSION_B, "hi")
    out = _share("Second")
    assert "sessions   1" in out


def test_unknown_selector_is_refused_by_name(seeded_store, recording_gh, export_double):
    out = _share("no-such-session")
    assert "no single session matches" in out
    assert store.load_pending(commands.data_dir(), SESSION_A) is None


def test_format_jsonl_ships_one_file(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    out = _share("--format jsonl")
    assert ".md" not in out


def test_bad_format_is_refused(seeded_store, recording_gh, export_double):
    assert "unknown format" in _share("--format pdf")


def test_ledger_records_the_bundle(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    _share()
    _share("confirm")
    rows = store.load_ledger(commands.data_dir())["shares"]
    assert len(rows) == 1
    assert len(rows[0]["sessions"]) == 1
    assert rows[0]["status"] == "shared"
    assert rows[0]["files"]


def test_list_shows_the_bundle(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    _share()
    _share("confirm")
    out = _share("list")
    assert "1 session(s)" in out
    assert "abc123def456" in out


def test_unshare_last_deletes_and_marks_revoked(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    _share()
    _share("confirm")
    out = _unshare("last")
    assert "404" in out
    deleted = [c for c in recording_gh.calls if len(c) > 2 and c[2] == "delete"]
    assert deleted and deleted[0][3] == "abc123def456"
    assert store.find_share(commands.data_dir(), "abc123def456")["status"] == "revoked"


def test_unshare_refuses_an_unknown_id(seeded_store, recording_gh):
    assert "not in this profile's ledger" in _unshare("deadbeef")
    assert recording_gh.calls == []


def test_expired_preview_cannot_be_confirmed(seeded_store, recording_gh, export_double):
    import json
    import time

    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    _share()
    root = commands.data_dir()
    path = store.pending_path(root, SESSION_A)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["created"] = time.time() - (store.PENDING_TTL_SECONDS + 5)
    path.write_text(json.dumps(data), encoding="utf-8")

    assert "no pending preview" in _share("confirm")
    assert recording_gh.calls == []


def test_missing_files_are_reported(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    _share()
    pending = store.load_pending(commands.data_dir(), SESSION_A)
    for item in pending["paths"]:
        Path(item).unlink()
    out = _share("confirm")
    assert "preview file is gone" in out
    assert recording_gh.calls == []


def test_help_lists_every_subcommand(seeded_store):
    out = _share("help")
    for token in ("/share confirm", "/share cancel", "/share list", "/share import", "/unshare"):
        assert token in out


def test_output_has_no_ansi(seeded_store, recording_gh, export_double):
    export_double.state[SESSION_A] = export_double.record(SESSION_A, "hi")
    assert "\x1b" not in _share()
