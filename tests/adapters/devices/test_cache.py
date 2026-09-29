"""The cache folder: agent builds one per version, and what runs mark as in use, across processes."""

import fcntl
import os
import subprocess
import sys
import threading

import pytest

from jevtest.adapters.devices import cache
from jevtest.adapters.devices.cache import AgentBuilds, in_use_dir, lock, try_lock


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    return tmp_path


def use_in_another_process(folder, prefix, suffix, version):
    """Another jevtest run, of another version: it uses its build until its stdin closes."""
    code = (
        "import sys; from jevtest.adapters.devices.cache import AgentBuilds; "
        f"b = AgentBuilds({prefix!r}, {suffix!r}).use({version!r}); print('using', flush=True); sys.stdin.read()"
    )
    p = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JEVTEST_CACHE": str(folder)},
    )
    assert p.stdout is not None and p.stdout.readline().strip() == "using"
    return p


def test_the_cache_is_jevtest_cache_or_the_home_cache(folder, monkeypatch):
    assert cache.cache_dir() == folder
    assert in_use_dir() == folder / "in-use" and (folder / "in-use").is_dir()
    monkeypatch.delenv("JEVTEST_CACHE")
    assert cache.cache_dir().parts[-2:] == (".cache", "jevtest")


def test_older_builds_of_one_kind_go_and_nothing_else(folder):
    for name in (
        "ios-agent-aaa",
        "ios-agent-bbb",
        "ios-agent-aaa-TEAM1",
        "ios-agent-bbb-TEAM1",
        "ios-agent-A1B2.log",
        "ios-agent-8123.log",
        "android-agent-aaa.apk",
        "android-agent-bbb.apk",
        "android-agent-bbb.apk.idsig",
        "android-agent-ccc.apk.part",  # a build that stopped partway
    ):
        (folder / name).mkdir() if name.startswith("ios") and "." not in name else (folder / name).write_text("")
    (folder / "ios-agent-bbb" / "Build").mkdir()  # a build folder, with what's in it
    AgentBuilds("ios-agent-").drop_older(keep="aaa")
    AgentBuilds("android-agent-", ".apk", beside=(".idsig", ".part")).drop_older(keep="aaa")
    assert sorted(p.name for p in folder.iterdir()) == [
        "android-agent-aaa.apk",  # the current one
        "in-use",
        "ios-agent-8123.log",  # not a build (see remove_port_logs)
        "ios-agent-A1B2.log",  # a device's log
        "ios-agent-aaa",  # the current one
        "ios-agent-aaa-TEAM1",  # iPhone builds: another kind
        "ios-agent-bbb-TEAM1",
    ]
    assert list((folder / "in-use").iterdir()) == []  # no lock files left behind


def test_a_build_another_run_uses_is_not_removed_until_it_is_done(folder):
    old = folder / "ios-agent-bbb"
    old.mkdir()
    other = use_in_another_process(folder, "ios-agent-", "", "bbb")
    try:
        AgentBuilds("ios-agent-").drop_older(keep="aaa")
        assert old.is_dir()  # the other run's xcodebuild is running its agent from it
    finally:
        other.communicate("")  # the other run ends: the operating system lets go of its mark
    AgentBuilds("ios-agent-").drop_older(keep="aaa")
    assert not old.exists()


def test_a_run_that_marks_a_build_while_it_is_removed_waits_and_finds_it_gone(folder):
    builds = AgentBuilds("ios-agent-")
    (folder / "ios-agent-bbb").mkdir()
    remover = try_lock(in_use_dir() / "ios-agent-bbb.lock")  # a remover mid-removal
    assert remover is not None
    marked: list[bool] = []

    def mark() -> None:
        with builds.use("bbb") as build:  # waits for the remover
            marked.append(build.path.exists())
            assert try_lock(in_use_dir() / "ios-agent-bbb.lock") is None  # its mark is on the file at the path

    waiting = threading.Thread(target=mark)
    waiting.start()
    waiting.join(0.2)
    assert waiting.is_alive()  # still waiting
    (folder / "ios-agent-bbb").rmdir()
    (in_use_dir() / "ios-agent-bbb.lock").unlink()
    remover.close()
    waiting.join(5)
    assert marked == [False]  # so it builds it again


def test_a_lock_is_shared_or_for_one_holder(folder):
    path = folder / "x.lock"
    first, second = lock(path, shared=True), lock(path, shared=True)
    assert try_lock(path) is None
    first.close()
    second.close()
    alone = try_lock(path)
    assert alone is not None
    alone.close()


def test_a_lock_that_fails_closes_its_file(folder, monkeypatch):
    opened = []
    real_open = type(folder).open

    def open_and_note(self, *args, **kwargs):
        f = real_open(self, *args, **kwargs)
        opened.append(f)
        return f

    monkeypatch.setattr(type(folder), "open", open_and_note)
    monkeypatch.setattr(fcntl, "flock", lambda f, mode: (_ for _ in ()).throw(OSError(5, "I/O error")))
    with pytest.raises(OSError, match="I/O error"):
        lock(folder / "x.lock")
    assert opened and all(f.closed for f in opened)
