import json

import pytest

from jevtest.adapters.jev.client import Reply
from jevtest.adapters.jev.lockfile import VERSION, LockedModel, LockMode, request_key
from jevtest.domain.failures import ModelError
from jevtest.domain.model import Probability, YesNo

A1 = {"q": {"type": "noul", "noul": 0.9}}
A2 = {"q": {"type": "noul", "noul": 0.1}}
Q = {"q": YesNo({"question": "?"})}
WIRE_Q = {"q": {"type": "noul", "instructions": {"question": "?"}}}


class FakeClient:
    """Answers with scripted Jev replies, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def ask(self, state, questions):
        self.asked.append((state, questions))
        return Reply(self.answers.pop(0), ms=12, served_by="jev-1.13.0", cost=0.0002)


def locked(tmp_path, mode=LockMode.RECORD, *answers):
    made = []

    def connect():
        made.append(FakeClient(*answers))
        return made[-1]

    return LockedModel("m", tmp_path / "t.lock.json", mode, connect), made


def test_key_is_canonical():
    assert request_key("m", {"a": 1, "b": 2}, WIRE_Q) == request_key("m", {"b": 2, "a": 1}, WIRE_Q)
    assert request_key("m", {"screen": "s"}, WIRE_Q) != request_key("other", {"screen": "s"}, WIRE_Q)
    assert request_key("m", {"screen": "s"}, WIRE_Q) != request_key("m", {"screen": "t"}, WIRE_Q)


def test_the_key_hashes_exactly_what_jev_is_sent(tmp_path):
    lock, made = locked(tmp_path, LockMode.RECORD, A1)
    lock.ask({"screen": "screen"}, Q)
    assert made[0].asked == [({"screen": "screen"}, WIRE_Q)]
    lock.save()
    assert list(json.loads((tmp_path / "t.lock.json").read_text())["decisions"]) == [
        request_key("m", {"screen": "screen"}, WIRE_Q)
    ]


def test_record_then_replay_is_identical_and_offline(tmp_path):
    first, _ = locked(tmp_path, LockMode.RECORD, A1)
    assert first.ask({"screen": "screen"}, Q) == {"q": Probability(0.9)}
    call = first.calls[0]
    assert (call.recorded, call.ms, call.cost, call.served_by) == (False, 12, 0.0002, "jev-1.13.0")
    first.save()
    data = json.loads((tmp_path / "t.lock.json").read_text())
    assert data["version"] == VERSION and len(data["decisions"]) == 1

    replay, made = locked(tmp_path, LockMode.RECORD)  # no answers scripted: any live call would fail
    assert replay.ask({"screen": "screen"}, Q) == {"q": Probability(0.9)}
    assert replay.calls[0].recorded is True and replay.calls[0].served_by == "jev-1.13.0"
    assert not made  # Jev was never even connected to


def test_new_screen_is_asked_live_and_added(tmp_path):
    lock, _ = locked(tmp_path, LockMode.RECORD, A1, A2)
    lock.ask({"screen": "screen 1"}, Q)
    lock.ask({"screen": "screen 2"}, Q)
    lock.ask({"screen": "screen 1"}, Q)
    assert [c.recorded for c in lock.calls] == [False, False, True]
    lock.save()
    assert len(json.loads((tmp_path / "t.lock.json").read_text())["decisions"]) == 2


def test_frozen_fails_on_new_screen(tmp_path):
    lock, made = locked(tmp_path, LockMode.FROZEN)
    with pytest.raises(ModelError, match="--lock frozen only replays recorded decisions. Run with --lock record"):
        lock.ask({"screen": "new"}, Q)
    assert not made


def test_frozen_replays_recorded(tmp_path):
    rec, _ = locked(tmp_path, LockMode.RECORD, A1)
    rec.ask({"screen": "s"}, Q)
    rec.save()
    frozen, _ = locked(tmp_path, LockMode.FROZEN)
    assert frozen.ask({"screen": "s"}, Q) == {"q": Probability(0.9)}


def test_refresh_asks_again_and_overwrites(tmp_path):
    rec, _ = locked(tmp_path, LockMode.RECORD, A1)
    rec.ask({"screen": "s"}, Q)
    rec.save()
    refresh, _ = locked(tmp_path, LockMode.REFRESH, A2)
    assert refresh.ask({"screen": "s"}, Q) == {"q": Probability(0.1)}
    refresh.save()
    again, _ = locked(tmp_path, LockMode.RECORD)
    assert again.ask({"screen": "s"}, Q) == {"q": Probability(0.1)}


def test_off_never_touches_the_file(tmp_path):
    (tmp_path / "t.lock.json").write_text("not json")  # would fail to load in any other mode
    lock, _ = locked(tmp_path, LockMode.OFF, A1, A1)
    lock.ask({"screen": "s"}, Q)
    lock.ask({"screen": "s"}, Q)
    lock.save()
    assert [c.recorded for c in lock.calls] == [False, False]
    assert (tmp_path / "t.lock.json").read_text() == "not json"


def test_save_without_changes_writes_nothing(tmp_path):
    lock, _ = locked(tmp_path)
    lock.save()
    assert not (tmp_path / "t.lock.json").exists()


def test_jev_connected_to_once(tmp_path):
    lock, made = locked(tmp_path, LockMode.RECORD, A1, A2)
    lock.ask({"screen": "a"}, Q)
    lock.ask({"screen": "b"}, Q)
    assert len(made) == 1


def test_a_recorded_answer_that_no_longer_fits_the_question_is_an_error(tmp_path):
    rec, _ = locked(tmp_path, LockMode.RECORD, {"q": {"type": "noul", "noul": 7}})
    with pytest.raises(ModelError, match="yes/no question q"):
        rec.ask({"screen": "s"}, Q)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "not valid JSON"),
        ("[]", "not a jevtest v1 lockfile"),
        ('{"version": 99}', "not a jevtest v1"),
        ('{"version": 1, "decisions": []}', "not a jevtest v1"),
        ('{"version": 1, "decisions": {"k": "answers"}}', "not a jevtest v1"),
        ('{"version": 1, "decisions": {"k": {"answers": {"q": 1}}}}', "not a jevtest v1"),
    ],
)
def test_bad_lockfile(tmp_path, content, message):
    (tmp_path / "t.lock.json").write_text(content)
    with pytest.raises(ModelError, match=message):
        locked(tmp_path)


def test_forks_share_decisions_and_keep_their_own_calls(tmp_path):
    path = tmp_path / "t.lock.json"
    parent = LockedModel("m", path, LockMode.RECORD, lambda: FakeClient(A1, A2))
    a, b = parent.fork(), parent.fork()
    a.ask({"s": 1}, Q)
    b.ask({"s": 1}, Q)  # recorded by `a` moments ago: no Jev call
    b.ask({"s": 2}, Q)
    assert (len(a.calls), [c.recorded for c in b.calls], parent.calls) == (1, [True, False], [])
    assert b.mode is LockMode.RECORD and b.path == path
    parent.save()
    assert len(json.loads(path.read_text())["decisions"]) == 2
    mtime = path.stat().st_mtime_ns
    parent.save()  # nothing new
    assert path.stat().st_mtime_ns == mtime


def test_prune_drops_what_this_run_did_not_use(tmp_path):
    path = tmp_path / "t.lock.json"
    first = LockedModel("m", path, LockMode.RECORD, lambda: FakeClient(A1, A2))
    first.ask({"s": "old"}, Q)
    first.ask({"s": "kept"}, Q)
    first.save()
    second = LockedModel("m", path, LockMode.RECORD, FakeClient)
    second.fork().ask({"s": "kept"}, Q)  # uses through forks count
    assert second.prune() == 1
    second.save()
    assert len(json.loads(path.read_text())["decisions"]) == 1
    assert second.prune() == 0


def test_a_recorded_model_version_that_is_not_text_is_not_trusted(tmp_path):
    key = request_key("m", {"screen": "s"}, WIRE_Q)
    (tmp_path / "t.lock.json").write_text(
        json.dumps({"version": VERSION, "decisions": {key: {"served_by": 3, "answers": A1}}})
    )
    model, _ = locked(tmp_path, LockMode.FROZEN)
    assert model.ask({"screen": "s"}, Q) == {"q": Probability(0.9)} and model.calls[0].served_by is None
