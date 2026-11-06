"""Shared fixtures: a throwaway HOME/HERMES_HOME and a session with planted fake secrets.

Every test runs against a temporary Hermes home. Nothing here touches the operator's profile, and
the planted credentials are invented by this file -- they are assembled from parts so no
contiguous key-shaped literal sits in the source.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:  # the plugin package is imported by its repo name, never installed
    sys.path.insert(0, str(ROOT))

SESSION_ID = "sess-share-0001"

# Assembled from parts: the runtime strings are realistic, the source contains no key-shaped literal.
PLANTED = {
    "openai": "sk-" + "proj-" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lz",
    "github": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "bearer": "Bearer " + "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "." + "cGF5bG9hZA" + "." + "c2lnbmF0dXJl",
    "env_name": "DEPLOY_" + "TOKEN",
    "env_value": "zq8" + "Vt7" + "Lm2" + "Wp5" + "Rn9" + "Kd4",
}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A temporary HOME plus HERMES_HOME, so no test can see or write the real profile."""
    home = tmp_path / "user-home"
    hermes = tmp_path / "hermes-home"
    home.mkdir()
    hermes.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    return hermes


@pytest.fixture
def planted() -> dict[str, str]:
    """The planted fake credentials, so no test module has to import conftest."""
    return PLANTED


@pytest.fixture
def planted_text(planted) -> str:
    user = getpass.getuser()
    return (
        f"my key is {PLANTED['openai']} and the bot token is {PLANTED['github']}\n"
        f"curl -H 'Authorization: {PLANTED['bearer']}' https://example.invalid/api\n"
        f"{PLANTED['env_name']}={PLANTED['env_value']}\n"
        f"the repo lives at /home/{user}/work/secret-project and I am {user}\n"
        f"windows path C:\\Users\\{user}\\work\n"
    )


@pytest.fixture
def seeded_session(fake_home, planted_text):
    """A real SessionDB under the temporary home, holding one conversation with the planted text."""
    from hermes_state import SessionDB

    with SessionDB(db_path=Path(fake_home) / "state.db") as db:
        db.create_session(SESSION_ID, source="cli", model="share-test-model")
        db.append_message(SESSION_ID, "user", content=f"here is my setup\n{planted_text}")
        db.append_message(
            SESSION_ID, "assistant",
            content="Noted. I ran the deploy and it printed the token back:\n" + planted_text,
        )
        db.append_message(SESSION_ID, "tool", content=planted_text, tool_name="terminal")
    return SESSION_ID


@pytest.fixture
def no_gh(monkeypatch):
    """No gh on PATH."""
    from hermes_share import gists

    monkeypatch.setattr(gists, "find_gh", lambda: None)
    return gists


@pytest.fixture
def recording_gh(monkeypatch):
    """A gh that records every argv it is asked to run and reports success."""
    from types import SimpleNamespace

    from hermes_share import gists

    calls: list[list[str]] = []

    def _verb(argv: list[str]) -> str:
        """``gh auth status`` vs ``gh gist create``: the subcommand sits after the group."""
        if len(argv) > 1 and argv[1] == "auth":
            return "auth"
        return argv[2] if len(argv) > 2 else ""

    def _fake_run(argv, *, timeout):
        calls.append(list(argv))
        verb = _verb(argv)
        if verb == "auth":
            return SimpleNamespace(returncode=0, stdout="Logged in", stderr="")
        if verb == "create":
            return SimpleNamespace(
                returncode=0,
                stdout="https://gist.github.com/testuser/abc123def456\n",
                stderr="",
            )
        if verb == "delete":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown verb")

    monkeypatch.setattr(gists, "find_gh", lambda: "/usr/bin/gh")
    monkeypatch.setattr(gists, "_run", _fake_run)
    return SimpleNamespace(calls=calls, verbs=lambda: [_verb(c) for c in calls])
