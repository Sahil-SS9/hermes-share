"""Bundle building: parsing, redaction, rendering and the caps."""

from __future__ import annotations

import getpass
import json

import pytest
from conftest import FakeCtx

from hermes_share import bundle as bundle_mod
from hermes_share.commands import Runtime


def _record(session_id: str, text: str, title: str = "T") -> dict:
    return {
        "id": session_id, "source": "cli", "title": title, "model": "m",
        "started_at": 1762400000.0,
        "messages": [{"role": "user", "content": text, "timestamp": 1762400001.0}],
    }


def _jsonl(session_id: str, text: str, title: str = "T") -> str:
    return json.dumps(_record(session_id, text, title), ensure_ascii=False)


def _redactor():
    return Runtime(FakeCtx()).redactor()


def test_malformed_line_is_refused_by_name():
    with pytest.raises(bundle_mod.BundleError) as excinfo:
        bundle_mod.parse_export_jsonl("{not json}\n", origin="bundle.jsonl")
    assert "bundle.jsonl" in str(excinfo.value)
    assert "line 1" in str(excinfo.value)


def test_a_record_without_messages_is_refused():
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.parse_export_jsonl('{"id": "x"}\n')


def test_empty_input_is_refused():
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.parse_export_jsonl("\n\n")


def test_redaction_removes_planted_credentials(fake_home, planted_text, planted):
    record, _marks = bundle_mod.redact_session(json.loads(_jsonl("s1", planted_text)), _redactor())
    text = json.dumps(record, ensure_ascii=False)

    assert planted["openai"] not in text
    assert planted["github"] not in text
    assert planted["env_value"] not in text
    assert "«redacted" in text


def test_redaction_leaves_no_readable_key_fragment(fake_home, planted, planted_text):
    record, _ = bundle_mod.redact_session(json.loads(_jsonl("s1", planted_text)), _redactor())
    text = json.dumps(record, ensure_ascii=False)
    assert "sk-pro" not in text, "the readable head of the key survived"
    assert planted["openai"][-4:] not in text, "the readable tail of the key survived"


def test_home_paths_and_account_name_are_scrubbed(fake_home, planted_text):
    text, counts = bundle_mod.redact_text(planted_text, _redactor(), bundle_mod.identity_tokens())
    user = getpass.getuser()
    assert f"/home/{user}" not in text
    assert "~" in text
    assert user not in text
    assert counts["paths"] >= 1
    assert counts["identities"] >= 1


def test_markdown_renders_every_session(fake_home):
    sessions = [json.loads(_jsonl("a", "hello there", title="First")),
                json.loads(_jsonl("b", "second one", title="Second"))]
    md = bundle_mod.render_markdown(sessions)
    assert "# First" in md and "# Second" in md
    assert "hello there" in md and "second one" in md
    assert "Shared Hermes sessions (2)" in md


def test_markdown_handles_block_content(fake_home):
    record = {"id": "x", "title": "Blocks", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "from a block"}], "timestamp": 1.0}
    ]}
    assert "from a block" in bundle_mod.render_markdown([record])


def test_bundle_refuses_more_than_the_cap(fake_home):
    too_many = [f"s{n}" for n in range(bundle_mod.MAX_BUNDLE_SESSIONS + 1)]
    with pytest.raises(bundle_mod.BundleError) as excinfo:
        bundle_mod.build(too_many, _redactor())
    assert str(bundle_mod.MAX_BUNDLE_SESSIONS) in str(excinfo.value)


def test_build_produces_valid_jsonl_and_counts(fake_home, export_double, planted_text, planted):
    export_double.state["s1"] = export_double.record("s1", planted_text)
    export_double.state["s2"] = export_double.record("s2", "nothing secret here")

    built = bundle_mod.build(["s1", "s2"], _redactor())
    assert len(built["sessions"]) == 2
    assert built["totals"]["secrets"] >= 1
    assert planted["openai"] not in built["jsonl"]
    assert planted["openai"] not in built["markdown"]
    for line in built["jsonl"].strip().splitlines():
        json.loads(line)


def test_summary_lines_are_one_per_session(fake_home, export_double):
    export_double.state["s1"] = export_double.record("s1", "hi")
    built = bundle_mod.build(["s1"], _redactor())
    lines = bundle_mod.summary_lines(built)
    assert len(lines) == 1
    assert "s1" in lines[0]
