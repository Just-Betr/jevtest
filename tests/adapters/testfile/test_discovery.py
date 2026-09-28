import pytest

from jevtest.adapters.testfile.discovery import find_test_files
from jevtest.domain.failures import TestFileError


def write_test_file(folder, rel):
    f = folder / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("app: a.apk\ndevice: {android: A}\ntests: [{name: T, fresh: true, steps: [back]}]\n")
    return f


def test_finds_every_yaml_in_a_folder(tmp_path):
    a = write_test_file(tmp_path, "suite/login.yaml")
    b = write_test_file(tmp_path, "suite/deep/cart.yml")
    c = write_test_file(tmp_path, "suite/.hidden/x.yaml")  # hidden folders are not skipped
    lib = tmp_path / "suite" / "shared.yaml"
    lib.write_text("tests: [{name: S, fresh: true, steps: [back]}]\n")
    (tmp_path / "suite" / "notes.txt").write_text("")
    (tmp_path / "suite" / ".github").mkdir()
    (tmp_path / "suite" / ".github" / "ci.yml").write_text("on: push\n")  # not ours: no stray-file error
    files, others = find_test_files([str(tmp_path / "suite")])
    assert files == [c.resolve(), b.resolve(), a.resolve()] and others == [lib.resolve()]
    assert find_test_files([str(a), str(tmp_path / "suite")])[0][0] == a.resolve()  # each once, as given first


def test_errors(tmp_path):
    with pytest.raises(TestFileError, match="Test file not found"):
        find_test_files([str(tmp_path / "nope.yaml")])
    with pytest.raises(TestFileError, match="No test files"):
        find_test_files([str(tmp_path)])
    (tmp_path / "notes.txt").write_text("")
    with pytest.raises(TestFileError, match="notes.txt is not a test file: test files are .yaml or .yml"):
        find_test_files([str(tmp_path / "notes.txt")])
