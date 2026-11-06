"""The confirm gate: nothing is uploaded, and no child process runs, before /share confirm."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_share import commands, gists, store


def test_preview_starts_no_process_at_all(seeded_session, monkeypatch):
    """A preview is local-only: it must not even run `gh auth status`."""
    spawned: list[list[str]] = []

    def _explode(argv, *, timeout):
        spawned.append(list(argv))
        raise AssertionError(f"preview spawned {argv!r}")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _explode)

    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "preview ready, nothing uploaded" in out
    assert spawned == []


def test_no_gist_is_created_without_confirm(seeded_session, recording_gh):
    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    assert recording_gh.calls == []
    assert store.load_ledger(commands.data_dir())["shares"] == []


def test_confirm_uploads_exactly_once(seeded_session, recording_gh):
    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    out = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "https://gist.github.com/testuser/abc123def456" in out
    assert recording_gh.verbs() == ["auth", "create"]

    create = [c for c in recording_gh.calls if len(c) > 2 and c[2] == "create"][0]
    assert "--public" not in create and "-p" not in create
    assert "--desc" in create


def test_confirm_clears_the_pending_preview(seeded_session, recording_gh):
    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    assert store.load_pending(commands.data_dir(), seeded_session) is None
    again = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                  platform="cli")
    assert "no pending preview" in again
    assert recording_gh.verbs().count("create") == 1


def test_confirm_without_a_preview_uploads_nothing(seeded_session, recording_gh):
    out = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "no pending preview" in out
    assert recording_gh.calls == []


def test_cancel_deletes_the_preview_and_uploads_nothing(seeded_session, recording_gh):
    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    pending = store.load_pending(commands.data_dir(), seeded_session)
    path = Path(pending["path"])
    assert path.is_file()

    cancelled = commands.handle_share("cancel", session_id=seeded_session,
                                      session_key=seeded_session, platform="cli")
    assert "nothing was uploaded" in cancelled
    assert not path.exists()
    assert recording_gh.calls == []


def test_confirm_uploaded_file_contains_no_planted_secret(seeded_session, recording_gh,
                                                           monkeypatch, planted):
    uploaded: dict[str, str] = {}

    def _capture(argv, *, timeout):
        if len(argv) > 2 and argv[1] == "gist" and argv[2] == "create":
            from types import SimpleNamespace

            uploaded["body"] = Path(argv[3]).read_text(encoding="utf-8")
            uploaded["desc"] = argv[argv.index("--desc") + 1]
            return SimpleNamespace(returncode=0,
                                   stdout="https://gist.github.com/testuser/abc123def456\n",
                                   stderr="")
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="Logged in", stderr="")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _capture)

    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")

    assert planted["openai"] not in uploaded["body"]
    assert planted["github"] not in uploaded["body"]
    assert planted["openai"] not in uploaded["desc"]
