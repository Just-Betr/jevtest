import os

import pytest

from jevtest.adapters.testfile.env import read_env
from jevtest.domain.failures import TestFileError


def test_read_env(tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n\nexport A_KEY='one'\nB_KEY=\"two\"\nC_KEY=x=y\nD_KEY='a\"\n  # indented comment\nSAME=1\n"
        "E_KEY=a#b\nF_KEY='p #w'\n"
    )
    environ = {"SAME": "1", "PATH": "/bin"}  # set in both, to the same value: fine
    env = read_env(tmp_path, environ)
    assert (env["A_KEY"], env["B_KEY"], env["C_KEY"], env["D_KEY"]) == ("one", "two", "x=y", "'a\"")
    assert (env["E_KEY"], env["F_KEY"]) == ("a#b", "p #w")
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
        (
            "HOST=a.test\nURL=https://${HOST}/x\n",
            r"\.env:2: URL uses \$\{HOST\}, but \.env values aren't filled in from other values: write the whole value",
        ),
        ("A=1\nB=pw # the password\n", r"\.env:2: is ' #…' a comment or part of B\? .*quote the value"),
        ("T_KEY=file\n", "T_KEY is set in the environment and in .*/.env to different values"),
    ],
)
def test_bad_env_files(tmp_path, body, message):
    (tmp_path / ".env").write_text(body)
    with pytest.raises(TestFileError, match=message):
        read_env(tmp_path, {"T_KEY": "env"})


def test_a_bom_and_windows_line_endings_are_fine(tmp_path):
    """Windows Notepad saves UTF-8 with a byte-order mark, and CRLF line ends."""
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfNAME=Ren\xc3\xa9e\r\nOTHER=x\r\n")
    env = read_env(tmp_path, {})
    assert (env["NAME"], env["OTHER"]) == ("Renée", "x")


def test_a_file_that_isnt_utf8_says_so(tmp_path):
    (tmp_path / ".env").write_bytes(b"NAME=Ren\xe9e\n")  # Latin-1
    with pytest.raises(TestFileError, match=r"\.env isn't UTF-8 text: save it as UTF-8"):
        read_env(tmp_path, {})
