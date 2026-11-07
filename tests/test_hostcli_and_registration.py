"""The host bridge, registration and the feature-detection boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import SESSION_A, FakeCtx

from hermes_share import commands, hostcli, plugin, store
from hermes_share.commands import Runtime


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_child_env_is_an_allowlist(fake_home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    env = hostcli.child_env()
    assert "OPENAI_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "PATH" in env or os.name == "nt"
    assert env["HERMES_HOME"] == str(fake_home)


def test_child_env_never_inherits_a_session_binding(fake_home, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "chat:123")
    assert "HERMES_SESSION_KEY" not in hostcli.child_env()


def test_import_help_detects_the_hermes_choice(fake_home, monkeypatch):
    monkeypatch.setattr(hostcli, "run", lambda *a, **k: _completed(stdout="  --from {claude,codex,hermes}\n"))
    assert hostcli.import_help_offers_hermes() is True


def test_import_help_says_no_when_only_foreign_sources(fake_home, monkeypatch):
    monkeypatch.setattr(hostcli, "run", lambda *a, **k: _completed(stdout="  --from {claude,codex}\n"))
    assert hostcli.import_help_offers_hermes() is False


def test_import_help_is_false_when_the_cli_cannot_run(fake_home, monkeypatch):
    def _boom(*a, **k):
        raise hostcli.HostCliError("nope")

    monkeypatch.setattr(hostcli, "run", _boom)
    assert hostcli.import_help_offers_hermes() is False


def test_export_failure_is_named(fake_home, monkeypatch):
    monkeypatch.setattr(hostcli, "run", lambda *a, **k: _completed(returncode=1, stderr="no such session\n"))
    with pytest.raises(hostcli.HostCliError) as excinfo:
        hostcli.export_session_jsonl("missing")
    assert "no such session" in str(excinfo.value)


def test_export_of_nothing_is_refused(fake_home, monkeypatch):
    monkeypatch.setattr(hostcli, "run", lambda *a, **k: _completed(stdout="   \n"))
    with pytest.raises(hostcli.HostCliError):
        hostcli.export_session_jsonl("empty")


def test_registration_declares_two_commands_and_nothing_else():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert set(ctx.commands) == {"share", "unshare"}


def test_registered_handlers_keep_the_host_calling_convention():
    """The host calls ``handler(raw_args, **context)``; a partial must not break that."""
    import functools

    from hermes_cli.plugin_command_dispatch import invoke_plugin_command

    ctx = FakeCtx()
    plugin.register(ctx)
    handler = ctx.commands["share"]
    assert isinstance(handler, functools.partial)
    assert handler.func is commands.handle_share

    # Prove the real dispatcher still delivers the context keywords through the partial.
    seen: dict[str, object] = {}

    def _probe(raw_args, *, rt, **kwargs):
        seen.update(raw_args=raw_args, bound=rt, **kwargs)

    probe = functools.partial(_probe, rt=handler.keywords["rt"])
    invoke_plugin_command(probe, "help", source=None, session_id="s", session_key="k", platform="cli")
    assert seen["raw_args"] == "help"
    assert seen["session_id"] == "s"
    assert seen["session_key"] == "k"
    assert seen["platform"] == "cli"
    assert seen["bound"] is handler.keywords["rt"]


def test_registered_handler_runs_through_the_real_dispatcher(fake_home, monkeypatch):
    from hermes_cli.plugin_command_dispatch import invoke_plugin_command

    ctx = FakeCtx()
    plugin.register(ctx)
    out = invoke_plugin_command(ctx.commands["share"], "help", source=None, session_id=SESSION_A,
                               session_key=SESSION_A, platform="cli")
    assert "/share confirm" in out


def test_missing_redact_text_refuses_before_any_child_process(fake_home, seeded_store, monkeypatch):
    spawned: list = []
    monkeypatch.setattr(hostcli, "export_session_jsonl", lambda sid: spawned.append(sid) or "")
    out = commands.handle_share("", rt=Runtime(FakeCtx(redact=False)), session_id=SESSION_A,
                                session_key=SESSION_A, platform="cli")
    assert "ctx.redact_text()" in out
    assert "Nothing was written or uploaded" in out
    assert spawned == []
    assert store.load_pending(commands.data_dir(), SESSION_A) is None


def test_settings_are_read_through_the_context():
    assert Runtime(FakeCtx(settings={"allow_gateway_import": True})).setting("allow_gateway_import", False) is True
    assert Runtime(FakeCtx()).setting("allow_gateway_import", False) is False


@pytest.mark.skipif(not os.environ.get("HERMES_TEST_HOST_BIN"),
                    reason="set HERMES_TEST_HOST_BIN to a built hermes to run the live-CLI bridge test")
def test_export_through_the_real_cli(fake_home, seeded_store, monkeypatch):
    """The one test that spawns the real host binary, so the bridge is proven end to end."""
    bin_dir = str(Path(os.environ["HERMES_TEST_HOST_BIN"]).parent)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))
    text = hostcli.export_session_jsonl(SESSION_A)
    assert '"messages"' in text
    assert SESSION_A in text
