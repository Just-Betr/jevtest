import json

import pytest

from jevtest.jev import JevError
from jevtest.lock import VERSION, LockedJev, request_key

from .conftest import FakeJev

A1 = {"q": {"type": "noul", "noul": 0.9}}
A2 = {"q": {"type": "noul", "noul": 0.1}}
Q = {"q": {"type": "noul", "instructions": "?"}}


def locked(tmp_path, mode="record", *answers):
    made = []

    def make():
        made.append(FakeJev(*answers))
        return made[-1]
    return LockedJev("m", tmp_path / "t.lock.json", mode, make), made


def test_key_is_canonical():
    assert request_key("m", {"a": 1, "b": 2}, Q) == request_key("m", {"b": 2, "a": 1}, Q)
    assert request_key("m", "s", Q) != request_key("other", "s", Q)
    assert request_key("m", "s", Q) != request_key("m", "t", Q)


def test_record_then_replay_is_identical_and_offline(tmp_path):
    first, made = locked(tmp_path, "record", A1)
    assert first.ask("screen", Q) == A1
    assert (first.hits, first.misses) == (0, 1)
    first.save()
    data = json.loads((tmp_path / "t.lock.json").read_text())
    assert data["version"] == VERSION and len(data["decisions"]) == 1

    replay, made2 = locked(tmp_path, "record")  # no answers scripted: any live call would fail
    assert replay.ask("screen", Q) == A1
    assert replay.calls[0]["cached"] is True and replay.hits == 1
    assert not made2  # Jev was never even created


def test_new_screen_is_asked_live_and_added(tmp_path):
    lock, _ = locked(tmp_path, "record", A1, A2)
    lock.ask("screen 1", Q)
    lock.ask("screen 2", Q)
    lock.ask("screen 1", Q)
    assert lock.misses == 2 and lock.hits == 1
    lock.save()
    assert len(json.loads((tmp_path / "t.lock.json").read_text())["decisions"]) == 2


def test_frozen_fails_on_new_screen(tmp_path):
    lock, made = locked(tmp_path, "frozen")
    with pytest.raises(JevError, match="--lock frozen only replays recorded decisions. Run with --lock record"):
        lock.ask("new", Q)
    assert not made


def test_frozen_replays_recorded(tmp_path):
    rec, _ = locked(tmp_path, "record", A1)
    rec.ask("s", Q)
    rec.save()
    frozen, _ = locked(tmp_path, "frozen")
    assert frozen.ask("s", Q) == A1


def test_refresh_asks_again_and_overwrites(tmp_path):
    rec, _ = locked(tmp_path, "record", A1)
    rec.ask("s", Q)
    rec.save()
    refresh, _ = locked(tmp_path, "refresh", A2)
    assert refresh.ask("s", Q) == A2
    refresh.save()
    again, _ = locked(tmp_path, "record")
    assert again.ask("s", Q) == A2


def test_off_never_touches_the_file(tmp_path):
    (tmp_path / "t.lock.json").write_text("not json")  # would fail to load in any other mode
    lock, _ = locked(tmp_path, "off", A1, A1)
    lock.ask("s", Q)
    lock.ask("s", Q)
    lock.save()
    assert lock.misses == 2
    assert (tmp_path / "t.lock.json").read_text() == "not json"


def test_save_without_changes_writes_nothing(tmp_path):
    lock, _ = locked(tmp_path)
    lock.save()
    assert not (tmp_path / "t.lock.json").exists()


def test_jev_created_once(tmp_path):
    lock, made = locked(tmp_path, "record", A1, A2)
    lock.ask("a", Q)
    lock.ask("b", Q)
    assert len(made) == 1


@pytest.mark.parametrize("content,message", [
    ("{", "not valid JSON"), ("[]", "not a jevtest v1 lockfile"), ('{"version": 99}', "not a jevtest v1")])
def test_bad_lockfile(tmp_path, content, message):
    (tmp_path / "t.lock.json").write_text(content)
    with pytest.raises(JevError, match=message):
        locked(tmp_path)


def test_bad_mode(tmp_path):
    with pytest.raises(ValueError):
        LockedJev("m", tmp_path / "x", "sometimes", lambda: None)


def test_forks_share_decisions_and_keep_their_own_calls(tmp_path):
    path = tmp_path / "t.lock.json"
    parent = LockedJev("m", path, "record", lambda: FakeJev(A1, A2))
    a, b = parent.fork(), parent.fork()
    a.ask({"s": 1}, Q)
    b.ask({"s": 1}, Q)  # recorded by a moments ago: no Jev call
    b.ask({"s": 2}, Q)
    assert (len(a.calls), len(b.calls), b.hits, parent.calls) == (1, 2, 1, [])
    parent.save()
    assert len(json.loads(path.read_text())["decisions"]) == 2
    mtime = path.stat().st_mtime_ns
    parent.save()  # nothing new
    assert path.stat().st_mtime_ns == mtime and not a.dirty


def test_fork_keeps_the_mode(tmp_path):
    assert LockedJev("m", tmp_path / "x.json", "frozen", None).fork().mode == "frozen"


def test_prune_drops_what_this_run_did_not_use(tmp_path):
    path = tmp_path / "t.lock.json"
    first = LockedJev("m", path, "record", lambda: FakeJev(A1, A2))
    first.ask({"s": "old"}, Q)
    first.ask({"s": "kept"}, Q)
    first.save()
    second = LockedJev("m", path, "record", lambda: FakeJev())
    second.fork().ask({"s": "kept"}, Q)  # uses through forks count
    assert second.prune() == 1
    second.save()
    assert len(json.loads(path.read_text())["decisions"]) == 1
    assert second.prune() == 0
