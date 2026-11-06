"""The ledger stays bounded, newest first, and unshare walks it correctly."""

from __future__ import annotations

from hermes_share import commands, store


def _row(n: int, status: str = "shared") -> dict:
    return {
        "gist_id": f"gist{n:04d}",
        "url": f"https://gist.github.com/testuser/gist{n:04d}",
        "session": "0123456789abcdef",
        "format": "md",
        "created": f"2026-11-01T00:{n % 60:02d}:00Z",
        "status": status,
    }


def test_ledger_is_capped(fake_home):
    root = commands.data_dir()
    for n in range(store.LEDGER_MAX_ROWS + 25):
        store.append_share(root, _row(n))
    ledger = store.load_ledger(root)
    assert len(ledger["shares"]) == store.LEDGER_MAX_ROWS


def test_ledger_keeps_the_newest_first(fake_home):
    root = commands.data_dir()
    for n in range(5):
        store.append_share(root, _row(n))
    shares = store.load_ledger(root)["shares"]
    assert shares[0]["gist_id"] == "gist0004"
    assert [s["gist_id"] for s in shares] == [f"gist{n:04d}" for n in (4, 3, 2, 1, 0)]


def test_reappending_the_same_gist_does_not_duplicate_it(fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(1))
    store.append_share(root, _row(1))
    assert len(store.load_ledger(root)["shares"]) == 1


def test_ledger_file_is_owner_only(fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(1))
    assert store.ledger_path(root).stat().st_mode & 0o077 == 0


def test_unshare_last_deletes_the_newest_live_gist(seeded_session, recording_gh, fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(1))
    store.append_share(root, _row(2))

    out = commands.handle_unshare("last", session_id=seeded_session, session_key=seeded_session,
                                  platform="cli")
    assert "gist0002" in out
    assert "404" in out
    deleted = [c for c in recording_gh.calls if len(c) > 2 and c[2] == "delete"]
    assert deleted and deleted[0][3] == "gist0002"
    assert store.find_share(root, "gist0002")["status"] == "revoked"


def test_unshare_last_skips_already_revoked_rows(seeded_session, recording_gh, fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(1))
    store.append_share(root, _row(2, status="revoked"))

    out = commands.handle_unshare("last", session_id=seeded_session, session_key=seeded_session,
                                  platform="cli")
    assert "gist0001" in out


def test_unshare_by_id(seeded_session, recording_gh, fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(7))
    out = commands.handle_unshare("gist0007", session_id=seeded_session,
                                  session_key=seeded_session, platform="cli")
    assert "gist0007" in out


def test_unshare_accepts_a_full_url(seeded_session, recording_gh, fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(7))
    commands.handle_unshare("https://gist.github.com/testuser/gist0007",
                            session_id=seeded_session, session_key=seeded_session, platform="cli")
    assert store.find_share(root, "gist0007")["status"] == "revoked"


def test_unshare_refuses_an_unknown_id(seeded_session, recording_gh):
    out = commands.handle_unshare("deadbeef", session_id=seeded_session,
                                  session_key=seeded_session, platform="cli")
    assert "not in this profile's ledger" in out
    assert recording_gh.calls == []


def test_unshare_with_nothing_shared_is_a_no_op(seeded_session, recording_gh):
    out = commands.handle_unshare("", session_id=seeded_session, session_key=seeded_session,
                                  platform="cli")
    assert "No live shared gist" in out
    assert recording_gh.calls == []


def test_share_list_shows_status(seeded_session, recording_gh, fake_home):
    root = commands.data_dir()
    store.append_share(root, _row(1))
    store.append_share(root, _row(2, status="revoked"))
    out = commands.handle_share("list", session_id=seeded_session, session_key=seeded_session,
                                platform="cli")
    assert "gist0002" in out and "revoked" in out
    assert "gist0001" in out and "shared" in out
