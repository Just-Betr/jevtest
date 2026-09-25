import sys

import pytest

from jevtest.drivers.base import DriverError, run


def py(code):
    return [sys.executable, "-c", code]


def test_returns_text_or_bytes():
    assert run(py("print('hé')")) == "hé\n"
    assert run(py("import sys; sys.stdout.buffer.write(b'\\x89PNG')"), binary=True) == b"\x89PNG"


def test_undecodable_output_does_not_crash():
    assert run(py("import sys; sys.stdout.buffer.write(b'\\xff')")) == "�"


def test_failure_includes_stderr_or_stdout():
    with pytest.raises(DriverError, match=r"failed \(3\): bad thing"):
        run(py("import sys; print('bad thing', file=sys.stderr); sys.exit(3)"))
    with pytest.raises(DriverError, match="only stdout"):
        run(py("print('only stdout'); raise SystemExit(1)"))


def test_unchecked_failure_returns_output():
    assert run(py("print('x'); raise SystemExit(1)"), check=False) == "x\n"


def test_missing_command():
    with pytest.raises(DriverError, match="Command not found: no-such-tool-xyz"):
        run(["no-such-tool-xyz"])


def test_timeout():
    with pytest.raises(DriverError, match="Timed out after 0.2s"):
        run(py("import time; time.sleep(5)"), timeout=0.2)
