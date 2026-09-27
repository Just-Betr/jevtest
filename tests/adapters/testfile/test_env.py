import os

import pytest

from jevtest.adapters.testfile.env import read_env
from jevtest.domain.failures import TestFileError


def test_read_env(tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n\nexport A_KEY='one'\nB_KEY=\"two\"\nC_KEY=x=y\nD_KEY='a\"\n  # indented comment\nSAME=1\n"
    )
    environ = {"SAME": "1", "PATH": "/bin"}  # set in both, to the same value: fine
    env = read_env(tmp_path, environ)
    assert (env["A_KEY"], env["B_KEY"], env["C_KEY"], env["D_KEY"]) == ("one", "two", "x=y", "'a\"")
    assert env["SAME"] == "1" and env["PATH"] == "/bin"
    assert "A_KEY" not in environ  # nothing leaks back: each file gets its own values


def test_the_process_environment_by_default(tmp_path):
    assert read_env(tmp_path) == dict(os.environ)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("A=1\nnot a pair\n", r"\.env:2 is not a KEY=value line"),
        ("A 1\n", r"\.env:1 is not a KEY=value line"),
        ("A=1\nA=2\n", r"\.env:2 sets A a second time"),
        ("T_KEY=file\n", "T_KEY is set in the environment and in .*/.env to different values"),
    ],
)
def test_bad_env_files(tmp_path, body, message):
    (tmp_path / ".env").write_text(body)
    with pytest.raises(TestFileError, match=message):
        read_env(tmp_path, {"T_KEY": "env"})
