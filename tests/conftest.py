"""Fixtures: a throwaway HOME/HERMES_HOME, a real session store, and a faithful plugin context.

The planted credentials are invented here and assembled from parts, so no contiguous key-shaped
literal sits in the source. ``redact_text`` delegates to the host's own scrubber in the strongest
mode, which is exactly what ``ctx.redact_text`` does -- a stand-in that quietly masked less would
make every redaction test prove nothing.
"""

from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLANTED = {
    "openai": "sk-" + "proj-" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lz",
    "github": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "bearer": "Bearer " + "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "c2ln",
    "env_name": "DEPLOY_" + "TOKEN",
    "env_value": "zq8" + "Vt7" + "Lm2" + "Wp5" + "Rn9" + "Kd4",
}

SESSION_A = "share-a-0001"
SESSION_B = "share-b-0002"


def _host_redact(text: str) -> str:
    from agent.redact import redact_sensitive_text

    return redact_sensitive_text(text, force=True, file_read=True, secret_file=True)


class FakeCtx:
    """A plugin context with only the public surface this plugin uses."""

    def __init__(self, *, redact: bool = True, settings: dict | None = None) -> None:
        self.commands: dict[str, object] = {}
        self._settings = settings or {}
        self._redact = _host_redact if redact else None

    def redact_text(self, text: str, *, force: bool = False) -> str:  # pragma: no cover - opt-in
        assert self._redact is not None
        return self._redact(text)

    def __getattribute__(self, name):
        # ``redact=False`` must look like a host that has no such method at all.
        if name == "redact_text" and object.__getattribute__(self, "_redact") is None:
            raise AttributeError(name)
        return object.__getattribute__(self, name)

    def get_config(self, key: str, default=None):
        return self._settings.get(key, default)

    def register_command(self, name, handler, description="", args_hint="", **_kw):
        self.commands[name] = handler


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "user-home"
    hermes = tmp_path / "hermes-home"
    home.mkdir()
    hermes.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    return hermes


@pytest.fixture
def planted():
    return PLANTED


@pytest.fixture
def planted_text(planted):
    user = getpass.getuser()
    return (
        f"key {planted['openai']} token {planted['github']}\n"
        f"curl -H 'Authorization: {planted['bearer']}' https://example.invalid/api\n"
        f"{planted['env_name']}={planted['env_value']}\n"
        f"repo at /home/{user}/work/secret-project and I am {user}\n"
    )


@pytest.fixture
def seeded_store(fake_home, planted_text):
    """Two real sessions in the temporary home, both holding the planted text."""
    from hermes_state import SessionDB

    with SessionDB(db_path=Path(fake_home) / "state.db") as db:
        for session_id, title in ((SESSION_A, "Deploy notes"), (SESSION_B, "Second session")):
            db.create_session(session_id, source="cli", model="share-test-model")
            db.set_session_title(session_id, title)
            db.append_message(session_id, "user", content=f"setup\n{planted_text}")
            db.append_message(session_id, "assistant", content=f"ack\n{planted_text}")
    return [SESSION_A, SESSION_B]


@pytest.fixture
def export_double(monkeypatch):
    """Serve ``hermes sessions export`` from an in-memory session record instead of a child process."""
    from hermes_share import hostcli

    def _record(session_id: str, text: str) -> str:
        return json.dumps(
            {
                "id": session_id,
                "source": "cli",
                "title": f"session {session_id}",
                "model": "m",
                "started_at": 1762400000.0,
                "messages": [
                    {"role": "user", "content": f"hello\n{text}", "timestamp": 1762400001.0},
                    {"role": "assistant", "content": f"reply\n{text}", "timestamp": 1762400002.0},
                ],
            },
            ensure_ascii=False,
        )

    calls: list[str] = []
    html_calls: list[tuple[str, str]] = []
    state: dict[str, str] = {}

    def _fake(session_id: str) -> str:
        calls.append(session_id)
        return state.get(session_id, _record(session_id, "")) + "\n"

    def _fake_html(session_id: str, destination) -> None:
        """Stand in for the CLI's own HTML writer: same shape, a real file."""
        from pathlib import Path

        html_calls.append((session_id, str(destination)))
        body = state.get(session_id, _record(session_id, ""))
        Path(destination).write_text(
            "<!DOCTYPE html><html><body><pre>" + body.replace("<", "&lt;") + "</pre></body></html>",
            encoding="utf-8",
        )

    monkeypatch.setattr(hostcli, "export_session_jsonl", _fake)
    monkeypatch.setattr(hostcli, "export_session_html", _fake_html)
    return SimpleNamespace(calls=calls, html_calls=html_calls, state=state, record=_record)


@pytest.fixture
def recording_gh(monkeypatch):
    """A gh that records every argv and reports success for the verbs the plugin uses."""
    from hermes_share import gists

    calls: list[list[str]] = []

    def _verb(argv):
        if len(argv) > 1 and argv[1] == "auth":
            return "auth"
        return argv[2] if len(argv) > 2 else ""

    def _fake_run(argv, **_kw):
        calls.append(list(argv))
        verb = _verb(argv)
        if verb in ("auth", "delete"):
            return SimpleNamespace(returncode=0, stdout="Logged in" if verb == "auth" else "", stderr="")
        if verb == "create":
            return SimpleNamespace(
                returncode=0, stdout="https://gist.github.com/testuser/abc123def456\n", stderr=""
            )
        if verb == "view":
            if "--files" in argv:
                return SimpleNamespace(returncode=0, stdout="bundle.jsonl\nbundle.md\n", stderr="")
            name = argv[argv.index("--filename") + 1]
            body = {
                "bundle.jsonl": '{"id":"x","messages":[{"role":"user","content":"hi"}]}\n',
                "bundle.md": "# shared\n",
            }[name]
            return SimpleNamespace(returncode=0, stdout=body, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown verb")

    monkeypatch.setattr(gists, "shutil", SimpleNamespace(which=lambda name: f"/usr/bin/{name}"))
    monkeypatch.setattr(gists, "run", _fake_run)
    return SimpleNamespace(calls=calls, verbs=lambda: [_verb(c) for c in calls])


@pytest.fixture
def claude_fixture(tmp_path):
    path = tmp_path / "claude-session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "c1",
                        "message": {"role": "user", "content": "fix the build"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "c1",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def codex_fixture(tmp_path):
    path = tmp_path / "rollout-2026-11-06.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"record_type": "state", "payload": {}}),
                json.dumps({"type": "response_item", "payload": {"role": "user", "content": "hi"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def hermes_fixture(tmp_path, planted_text):
    path = tmp_path / "hermes-bundle.jsonl"
    record = {
        "id": "exported-0001",
        "source": "cli",
        "title": "Exported one",
        "model": "m",
        "started_at": 1762400000.0,
        "messages": [{"role": "user", "content": f"hello\n{planted_text}", "timestamp": 1762400001.0}],
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path
