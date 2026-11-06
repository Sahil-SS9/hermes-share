"""gh missing, gh not signed in, argv shape, and the pending-preview lifecycle."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_share import commands, gists, store


def test_missing_gh_says_what_to_run_and_uploads_nothing(seeded_session, no_gh, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(gists, "_run", lambda argv, *, timeout: spawned.append(list(argv)))

    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "not installed" in out
    assert "gh auth login" in out

    blocked = commands.handle_share("confirm", session_id=seeded_session,
                                    session_key=seeded_session, platform="cli")
    assert "not installed" in blocked
    assert "gh auth login" in blocked
    assert spawned == []


def test_not_signed_in_refuses_and_names_the_command(seeded_session, monkeypatch):
    def _unauthenticated(argv, *, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="To get started with GitHub CLI, please run: gh auth login")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _unauthenticated)

    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    out = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "not signed in" in out
    assert "gh auth login" in out


def test_create_never_asks_for_a_public_gist(monkeypatch, tmp_path):
    seen: list[list[str]] = []

    def _run(argv, *, timeout):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0,
                               stdout="https://gist.github.com/testuser/abc123def456\n",
                               stderr="")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _run)

    target = tmp_path / "doc.md"
    target.write_text("hello\n", encoding="utf-8")
    result = gists.create_secret_gist("Hermes session: test", str(target))

    argv = seen[0]
    assert "--public" not in argv
    assert "-p" not in argv
    assert result["gist_id"] == "abc123def456"
    assert result["url"] == "https://gist.github.com/testuser/abc123def456"


def test_delete_uses_yes_so_it_cannot_prompt_a_terminal(monkeypatch):
    seen: list[list[str]] = []

    def _run(argv, *, timeout):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _run)

    gists.delete_gist("abc123")
    assert seen[0][-1] == "--yes"
    assert seen[0][1:4] == ["gist", "delete", "abc123"]


def test_gist_id_is_rejected_when_it_is_not_an_id(monkeypatch):
    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    with pytest.raises(gists.GistError):
        gists.delete_gist("../../etc/passwd")


def test_child_env_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-children")
    monkeypatch.setenv("HERMES_TOKEN", "also-not")
    env = gists.child_env()
    assert "OPENAI_API_KEY" not in env
    assert "HERMES_TOKEN" not in env
    assert "PATH" in env


def test_expired_preview_cannot_be_confirmed(seeded_session, recording_gh, fake_home):
    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    root = commands.data_dir()
    path = store.pending_path(root, seeded_session)
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    data["created"] = time.time() - (store.PENDING_TTL_SECONDS + 5)
    path.write_text(json.dumps(data), encoding="utf-8")

    out = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "no pending preview" in out
    assert recording_gh.calls == []


def test_missing_preview_file_is_reported_and_cleared(seeded_session, recording_gh):
    commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                          platform="cli")
    root = commands.data_dir()
    pending = store.load_pending(root, seeded_session)
    Path(pending["path"]).unlink()

    out = commands.handle_share("confirm", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "preview file is gone" in out
    assert store.load_pending(root, seeded_session) is None
    assert recording_gh.calls == []


def test_pending_previews_are_capped(seeded_session, fake_home):
    root = commands.data_dir()
    for n in range(store.PENDING_MAX_FILES + 10):
        store.save_pending(root, f"session-{n}", {"path": "/dev/null", "format": "md"})
    remaining = [p for p in (root / "pending").iterdir() if p.suffix == ".json"]
    assert len(remaining) <= store.PENDING_MAX_FILES


def test_previews_are_capped(seeded_session, fake_home):
    root = commands.data_dir()
    (root / "previews").mkdir(parents=True, exist_ok=True)
    for n in range(store.PREVIEW_MAX_FILES + 5):
        (root / "previews" / f"p{n}.md").write_text("x", encoding="utf-8")
    store.trim_previews(root)
    assert len(list((root / "previews").iterdir())) <= store.PREVIEW_MAX_FILES
