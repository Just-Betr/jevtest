import subprocess
import sys

import pytest

from jevtest.adapters.devices import common
from jevtest.adapters.devices._typing import override
from jevtest.adapters.devices.cache import cache_dir, digest
from jevtest.adapters.devices.common import log_errors, run, run_bytes, start_process, stop_process
from jevtest.domain.failures import DeviceError


def py(code):
    return [sys.executable, "-c", code]


def test_returns_text_or_bytes():
    assert run(py("print('hé')")) == "hé\n"
    assert run_bytes(py("import sys; sys.stdout.buffer.write(b'\\x89PNG')")) == b"\x89PNG"


def test_undecodable_output_does_not_crash():
    assert run(py("import sys; sys.stdout.buffer.write(b'\\xff')")) == "�"


def test_failure_includes_stderr_or_stdout():
    with pytest.raises(DeviceError, match=r"failed \(3\): bad thing"):
        run(py("import sys; print('bad thing', file=sys.stderr); sys.exit(3)"))
    with pytest.raises(DeviceError, match="only stdout"):
        run(py("print('only stdout'); raise SystemExit(1)"))


def test_unchecked_failure_returns_output():
    assert run(py("print('x'); raise SystemExit(1)"), check=False) == "x\n"


def test_missing_command():
    with pytest.raises(DeviceError, match="Command not found: no-such-tool-xyz"):
        run(["no-such-tool-xyz"])


def test_timeout():
    with pytest.raises(DeviceError, match="Timed out after 0.2s"):
        run(py("import time; time.sleep(5)"), timeout=0.2)


def test_a_tool_gets_no_input_so_it_cant_take_what_is_typed_to_jevtest():
    """Measured: adb, run from `jevtest inspect` with Enter and q piped in, read them, and inspect saw no input."""
    assert run(py("import sys; print(repr(sys.stdin.read()))")).strip() == "''"


# --- agents: start on their ready signal, stop cleanly ------------------------------------------


def test_start_process_returns_when_ready(tmp_path):
    log = tmp_path / "logs" / "agent.log"
    proc = start_process(
        py("import time; print('booting'); print('READY now', flush=True); time.sleep(30)"),
        ready="READY",
        log=log,
        timeout=10,
    )
    try:
        assert proc.poll() is None
        assert "booting" in log.read_text()
    finally:
        stop_process(proc)
    assert proc.poll() is not None


def test_a_helper_gets_no_input(tmp_path):
    log = tmp_path / "agent.log"
    proc = start_process(
        py("import sys; print('read', repr(sys.stdin.read()), 'READY', flush=True); import time; time.sleep(30)"),
        ready="READY",
        log=log,
        timeout=10,
    )
    try:
        assert "read '' READY" in log.read_text()
    finally:
        stop_process(proc)


def test_start_process_reports_an_early_exit(tmp_path):
    with pytest.raises(DeviceError, match=r"(?s)exited before it was ready. Its log says:\n  boom\nFull log: .*a.log"):
        start_process(py("print('boom')"), ready="READY", log=tmp_path / "a.log", timeout=10)


def test_log_errors_shows_the_error_lines(tmp_path):
    log = tmp_path / "a.log"
    log.write_text(
        "building\n    t = 1.0s Ignoring failure to get hierarchy\nTesting failed:\n\terror: no signing\n"
        "Testing failed:\nnoise\n"
    )
    assert log_errors(log) == f"Its log says:\n  Testing failed:\n  error: no signing\nFull log: {log}"


def test_start_process_times_out(tmp_path):
    with pytest.raises(DeviceError, match="did not report ready within 0.3s"):
        start_process(py("import time; time.sleep(30)"), ready="READY", log=tmp_path / "a.log", timeout=0.3)


def test_stop_process_kills_what_ignores_terminate():
    proc = subprocess.Popen(
        py(
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('x', flush=True); time.sleep(60)"
        ),
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    proc.stdout.readline()  # handler installed

    class Impatient:  # the same process, with a shorter wait so the test is fast
        def poll(self) -> int | None:
            return proc.poll()

        def terminate(self) -> None:
            proc.terminate()

        def kill(self) -> None:
            proc.kill()

        def wait(self, timeout: float | None = None) -> int:
            return proc.wait(0.2)

    stop_process(Impatient())
    assert proc.wait(5) is not None
    proc.stdout.close()


def test_stop_process_ignores_none_and_finished():
    stop_process(None)
    done = subprocess.Popen(py("pass"))
    done.wait()
    stop_process(done)


def test_digest_tracks_source_changes(tmp_path):
    (tmp_path / "a.swift").write_text("1")
    first = digest(tmp_path)
    (tmp_path / "xcuserdata").mkdir()
    (tmp_path / "xcuserdata" / "x").write_text("ignored")
    assert digest(tmp_path) == first
    (tmp_path / "a.swift").write_text("2")
    assert digest(tmp_path) != first


def test_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVTEST_CACHE", str(tmp_path))
    assert cache_dir() == tmp_path
    monkeypatch.delenv("JEVTEST_CACHE")
    assert cache_dir().name == "jevtest"


def test_a_device_must_say_how_it_puts_back_goes_home_and_closes():
    from jevtest.adapters.devices.common import BaseDevice

    class Partial(BaseDevice):
        @override
        def screen(self):
            raise NotImplementedError

        @override
        def drag(self, x1, y1, x2, y2):
            raise NotImplementedError

    # Python words this message differently between versions; the method names are what matter.
    with pytest.raises(TypeError, match="_press_home'?, '?_put_back'?, '?app_state'?, '?close'?, '?prepare_for_test"):
        Partial()  # type: ignore[abstract]  # instantiating it is what this test checks


def test_undo_is_kept_on_disk_until_everything_is_put_back():
    undo = common.Undo("dev-1")
    assert undo.left_by_a_stopped_run() == {}
    undo.remember("dark mode", lambda: "cmd uimode night no")
    undo.remember("network", lambda: "svc wifi enable")
    undo.remember("dark mode", lambda: pytest.fail("read again after the first change"))
    assert "dark mode" in undo and "rotation" not in undo
    assert undo.entries() == {"dark mode": "cmd uimode night no", "network": "svc wifi enable"}
    # a run killed now leaves both for the next run on the device
    assert common.Undo("dev-1").left_by_a_stopped_run() == undo.entries()
    undo.forget_all()
    assert undo.entries() == {} and common.Undo("dev-1").left_by_a_stopped_run() == {}
    undo.path.write_text("not json")
    assert undo.left_by_a_stopped_run() == {}
    undo.path.write_text("[1]")
    assert undo.left_by_a_stopped_run() == {}


def test_nothing_is_remembered_when_reading_the_setting_fails():
    undo = common.Undo("dev-1")

    def unreadable() -> str:
        raise DeviceError("can't read it")

    with pytest.raises(DeviceError):
        undo.remember("dark mode", unreadable)
    assert "dark mode" not in undo and not undo.path.exists()


def test_a_failed_tool_carries_its_exit_code_and_everything_it_printed():
    with pytest.raises(common.ToolFailed) as e:
        run([sys.executable, "-c", "import sys; print('on stdout'); print('on stderr', file=sys.stderr); sys.exit(3)"])
    assert e.value.returncode == 3
    assert "on stdout" in e.value.output and "on stderr" in e.value.output


def test_a_change_that_cant_be_noted_on_disk_is_refused_before_it_is_made(monkeypatch):
    undo = common.Undo("dev-1")

    def full(self, text, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(type(undo.path), "write_text", full)
    with pytest.raises(
        DeviceError,
        match=r"^can't note how to put back dark mode in .* \(No space left on device\), so it wasn't changed: make",
    ):
        undo.remember("dark mode", lambda: "cmd uimode night no")
    assert "dark mode" not in undo  # nothing to put back: the caller never changed it


def test_forgetting_what_was_put_back_says_when_it_cant(monkeypatch):
    undo = common.Undo("dev-1")

    def locked(self, missing_ok=False):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(type(undo.path), "unlink", locked)
    with pytest.raises(DeviceError, match=r"^can't forget what was put back in .* \(Operation not permitted\)"):
        undo.forget_all()
