"""Surfaces, registration, and the session-context seam."""

from __future__ import annotations

import inspect

import pytest

from hermes_share import commands


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack", "whatsapp", "matrix"])
def test_messaging_platforms_are_refused_with_a_reason(seeded_session, platform):
    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform=platform)
    assert "not available on" in out
    assert "/save" in out


@pytest.mark.parametrize("platform", ["local", "cli", "tui", "desktop", "acp"])
def test_local_surfaces_are_served(seeded_session, platform):
    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform=platform)
    assert "preview ready, nothing uploaded" in out


def test_help_is_available_on_every_surface(seeded_session):
    out = commands.handle_share("help", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "/share confirm" in out
    assert "Redaction is always on" in out


def test_helpers_render_without_a_terminal(seeded_session):
    """No ANSI escapes: the same string is printed by the CLI, the TUI and the gateway."""
    out = commands.handle_share("md", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "\x1b" not in out


def test_handlers_accept_the_host_session_context_keywords():
    """``plugin_command_dispatch`` passes only the keywords a handler declares."""
    from hermes_cli.plugin_command_dispatch import invoke_plugin_command

    seen: dict[str, str] = {}

    def _handler(raw_args, *, session_id="", session_key="", platform="", source=None, **_kw):
        seen.update(raw_args=raw_args, session_id=session_id, session_key=session_key,
                    platform=platform)
        return "ok"

    result = invoke_plugin_command(_handler, "md", source=None, session_id="sid",
                                   session_key="key", platform="cli")
    assert result == "ok"
    assert seen == {"raw_args": "md", "session_id": "sid", "session_key": "key", "platform": "cli"}


def test_registration_declares_both_commands_and_nothing_else():
    from hermes_share.plugin import register

    class _Ctx:
        def __init__(self):
            self.commands: dict[str, object] = {}
            self.tools: list[str] = []
            self.hooks: list[str] = []

        def register_command(self, name, handler, description="", args_hint="", **_kw):
            self.commands[name] = handler

        def register_tool(self, name, **_kw):
            self.tools.append(name)

        def register_hook(self, name, *_a, **_kw):
            self.hooks.append(name)

    ctx = _Ctx()
    register(ctx)
    assert set(ctx.commands) == {"share", "unshare"}
    assert ctx.commands["share"] is commands.handle_share
    assert ctx.commands["unshare"] is commands.handle_unshare
    assert ctx.tools == []
    assert ctx.hooks == []


def test_registered_handlers_take_raw_args_positionally():
    """The host calls ``handler(raw_args)``; a keyword-only first parameter would TypeError."""
    for handler in (commands.handle_share, commands.handle_unshare):
        params = list(inspect.signature(handler).parameters.values())
        assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def test_usage_lists_every_subcommand(seeded_session):
    out = commands.handle_share("help", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "/share confirm" in out
    assert "/share cancel" in out
    assert "/share list" in out
    assert "/unshare last" in out
