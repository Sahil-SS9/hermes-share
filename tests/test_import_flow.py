"""The /share import flow: content detection, preview, the #87988 gate, and the gateway consent gate."""

from __future__ import annotations

import pytest
from conftest import SESSION_A, FakeCtx

from hermes_share import commands, hostcli, importing, store
from hermes_share.commands import Runtime


def _do(raw: str, *, ctx=None, platform: str = "cli", session_id: str = SESSION_A):
    return commands.handle_share(
        raw, rt=Runtime(ctx or FakeCtx()), session_id=session_id, session_key=session_id, platform=platform
    )


def test_detects_a_hermes_export(hermes_fixture):
    assert importing.detect(hermes_fixture.read_text(encoding="utf-8")) == "hermes"


def test_detects_a_claude_code_transcript(claude_fixture):
    assert importing.detect(claude_fixture.read_text(encoding="utf-8")) == "claude"


def test_detects_a_codex_rollout(codex_fixture):
    assert importing.detect(codex_fixture.read_text(encoding="utf-8")) == "codex"


def test_markdown_is_not_a_session_bundle(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# hello\n\nsome prose\n", encoding="utf-8")
    assert importing.detect(path.read_text(encoding="utf-8")) is None


def test_preview_counts_a_hermes_bundle(fake_home, hermes_fixture):
    found = importing.preview([(hermes_fixture.name, hermes_fixture.read_text(encoding="utf-8"))])
    assert found["source"] == "hermes"
    assert found["sessions"][0]["title"] == "Exported one"
    assert found["sessions"][0]["messages"] == 1


def test_preview_counts_a_claude_file(fake_home, claude_fixture):
    found = importing.preview([(claude_fixture.name, claude_fixture.read_text(encoding="utf-8"))])
    assert found["source"] == "claude"
    assert found["sessions"][0]["messages"] == 2
    assert "fix the build" in found["sessions"][0]["title"]


def test_unreadable_target_is_refused(fake_home, tmp_path):
    with pytest.raises(importing.ImportFailed):
        importing.fetch(str(tmp_path / "missing.jsonl"))


def test_non_bundle_target_is_refused(fake_home, tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# prose only\n", encoding="utf-8")
    with pytest.raises(importing.ImportFailed) as excinfo:
        importing.preview(importing.fetch(str(path)))
    assert "none of those files" in str(excinfo.value)


def test_import_preview_saves_a_pending_bundle(fake_home, claude_fixture, monkeypatch):
    monkeypatch.setattr(hostcli, "import_offers_hermes", lambda: False)
    out = _do(f"import {claude_fixture}")
    assert "preview, nothing imported yet" in out
    assert "Claude Code" in out
    pending = store.load_pending(f"{SESSION_A}:import", "import")
    assert pending and pending["source"] == "claude"


def test_import_confirm_runs_the_host_import(fake_home, claude_fixture, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(hostcli, "import_file", lambda source, path: seen.append((source, path)))
    monkeypatch.setattr(hostcli, "import_offers_hermes", lambda: False)

    _do(f"import {claude_fixture}")
    out = _do("import confirm")
    assert "Imported 1 session(s)" in out
    assert seen and seen[0][0] == "claude"
    assert store.load_pending(f"{SESSION_A}:import", "import") is None


def test_hermes_import_is_refused_by_name_when_the_host_lacks_it(fake_home, hermes_fixture, monkeypatch):
    monkeypatch.setattr(hostcli, "import_offers_hermes", lambda: False)
    out = _do(f"import {hermes_fixture}")
    assert "87988" in out

    called: list = []
    monkeypatch.setattr(hostcli, "import_file", lambda source, path: called.append(source))
    refused = _do("import confirm")
    assert "87988" in refused
    assert called == []
    assert "Nothing was imported" in refused


def test_hermes_import_runs_when_the_host_offers_it(fake_home, hermes_fixture, monkeypatch):
    monkeypatch.setattr(hostcli, "import_offers_hermes", lambda: True)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(hostcli, "import_file", lambda source, path: seen.append((source, path)))

    assert "87988" not in _do(f"import {hermes_fixture}")
    out = _do("import confirm")
    assert "Imported 1 session(s)" in out
    assert seen[0][0] == "hermes"


def test_import_from_a_gist_picks_the_machine_readable_file(fake_home, recording_gh, monkeypatch):
    monkeypatch.setattr(hostcli, "import_offers_hermes", lambda: True)
    out = _do("import https://gist.github.com/testuser/abc123def456")
    assert "bundle.jsonl" in out
    assert "Hermes export" in out


def test_import_without_a_target_shows_usage(fake_home):
    assert "Usage: /share import" in _do("import")


def test_import_confirm_without_a_preview_is_refused(fake_home):
    assert "no pending import" in _do("import confirm")


def test_gateway_import_needs_owner_consent(fake_home, claude_fixture):
    out = _do(f"import {claude_fixture}", platform="discord")
    assert "refused on discord" in out
    assert "allow_gateway_import" in out


def test_gateway_import_runs_with_the_setting_on(fake_home, claude_fixture, monkeypatch):
    monkeypatch.setattr(hostcli, "import_file", lambda source, path: None)
    ctx = FakeCtx(settings={"allow_gateway_import": True})
    out = _do(f"import {claude_fixture}", ctx=ctx, platform="discord")
    assert "preview, nothing imported yet" in out
    assert "Imported 1 session(s)" in _do("import confirm", ctx=ctx, platform="discord")


def test_gateway_import_confirm_is_gated_too(fake_home, claude_fixture, monkeypatch):
    monkeypatch.setattr(hostcli, "import_file", lambda source, path: None)
    _do(f"import {claude_fixture}")  # prepared locally
    out = _do("import confirm", platform="telegram")
    assert "refused on telegram" in out


def test_download_size_cap_is_enforced(fake_home, monkeypatch):
    class _Huge:
        def __init__(self, *a, **k):
            pass

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"x" * (importing.MAX_DOWNLOAD_BYTES + 1)

        def __call__(self, *a, **k):
            return _Huge._Resp()

    monkeypatch.setattr(importing.urllib.request, "urlopen", _Huge())
    with pytest.raises(importing.ImportFailed) as excinfo:
        importing.fetch("https://example.invalid/big.jsonl")
    assert "cap" in str(excinfo.value)
