"""The redaction pass, against a real SessionDB in a throwaway Hermes home."""

from __future__ import annotations

import getpass
from pathlib import Path

from hermes_share import commands, store


def _preview(seeded_session, fmt: str = "md"):
    out = commands.handle_share(fmt, session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    pending = store.load_pending(commands.data_dir(), seeded_session)
    assert pending is not None, out
    return out, Path(pending["path"])


def test_planted_secrets_never_reach_the_preview(seeded_session, planted):
    out, path = _preview(seeded_session)
    document = path.read_text(encoding="utf-8")

    assert planted["openai"] not in document
    assert planted["github"] not in document
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in document
    assert planted["env_value"] not in document
    assert "secret-project" not in document or "~" in document  # the path prefix is gone

    # The preview itself must not be the leak: it echoes the document head.
    assert planted["openai"] not in out
    assert planted["github"] not in out


def test_home_paths_and_account_name_are_scrubbed(seeded_session):
    _out, path = _preview(seeded_session)
    document = path.read_text(encoding="utf-8")
    user = getpass.getuser()
    assert f"/home/{user}" not in document
    assert f"/Users/{user}" not in document
    assert "~" in document


def test_credentials_become_non_reusable_sentinels(seeded_session, planted):
    """A shared document must not carry the head/tail mask a terminal can afford."""
    _out, path = _preview(seeded_session)
    document = path.read_text(encoding="utf-8")
    assert "sk-pro" not in document, "the readable head of the key survived"
    assert planted["openai"][-4:] not in document, "the readable tail of the key survived"
    assert "«redacted" in document


def test_preview_reports_how_much_it_removed(seeded_session):
    out, _path = _preview(seeded_session)
    assert "secret mark(s)" in out
    assert "0 secret mark(s), 0 home path(s), 0 account/host name(s)" not in out


def test_redaction_cannot_be_turned_off(seeded_session, planted):
    """Every format goes through the same pass; there is no opt-out argument."""
    for fmt in ("md", "html", "json", "jsonl"):
        out = commands.handle_share(fmt, session_id=seeded_session, session_key=seeded_session,
                                    platform="cli")
        pending = store.load_pending(commands.data_dir(), seeded_session)
        assert pending is not None, out
        document = Path(pending["path"]).read_text(encoding="utf-8")
        assert planted["openai"] not in document, fmt
        assert planted["github"] not in document, fmt


def test_unknown_format_is_refused(seeded_session):
    out = commands.handle_share("pdf", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "unknown format" in out
    assert "md, html, json or jsonl" in out


def test_preview_file_is_owner_only(seeded_session):
    _out, path = _preview(seeded_session)
    assert path.stat().st_mode & 0o077 == 0, "preview must not be world/group readable"


def test_session_with_no_rows_is_refused(fake_home):
    from hermes_state import SessionDB

    with SessionDB(db_path=Path(fake_home) / "state.db") as db:
        db.create_session("empty-session", source="cli")
    out = commands.handle_share("", session_id="empty-session", session_key="empty-session",
                                platform="cli")
    assert "no stored messages" in out


def test_missing_session_id_is_refused(fake_home):
    out = commands.handle_share("", session_id="", session_key="", platform="cli")
    assert "no session is bound" in out


def test_export_is_the_same_document_save_would_write(seeded_session):
    """Same renderer as /save: a shared session is what /save would have written, redacted."""
    import re

    from hermes_cli.session_export import render_session_for_save

    from hermes_share import session as session_mod

    # The HTML renderer mints fresh CSP nonces per call, so compare with both nonce forms normalised.
    nonce = re.compile(r'nonce(?:-|=")[^"\s>]+')

    data = session_mod.load_session(seeded_session, "md")
    assert session_mod.render_session(data, "md") == render_session_for_save(data, "md")
    assert session_mod.render_session(data, "json") == render_session_for_save(data, "json")
    assert nonce.sub("nonce-X", session_mod.render_session(data, "html")) == nonce.sub(
        "nonce-X", render_session_for_save(data, "html")
    )


def test_preview_filename_does_not_carry_the_session_id(seeded_session):
    _out, path = _preview(seeded_session)
    assert seeded_session not in path.name
