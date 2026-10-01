import json

import pytest

from jevtest.cli import inspect as inspect_cli
from jevtest.cli import main as cli
from tests.cli.test_main import Fakes
from tests.conftest import FakeDevice, screen_with


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.apk").write_bytes(b"")
    return tmp_path / "app.apk"


PROMPTS: list[str] = []  # what each inspect asked, cleared as it starts


def inspect(fakes, *args, answers=()):
    said = list(answers)

    def read_line(prompt):
        PROMPTS.append(prompt)
        answer = said.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    PROMPTS.clear()
    return cli.main(
        ["inspect", *args],
        devices=fakes._device,
        find=fakes.find,
        client=fakes.client,
        clock=fakes.clock,
        read_line=read_line,
    )


def given(fakes, device):
    """Make the command use `device`."""

    def make_device(platform, name, app, progress):
        fakes.devices.append(device)
        return device

    fakes.make_device = make_device


def saved(tmp_path):
    (folder,) = (tmp_path / "res").iterdir()
    return folder, sorted(p.name for p in folder.iterdir())


def test_once_saves_the_screen_with_its_names_and_stops(app, tmp_path, capsys):
    fakes = Fakes()
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", "--once") == 0
    folder, names = saved(tmp_path)
    assert names == ["001.html", "001.json", "001.png", "001.txt"]
    dump = json.loads((folder / "001.json").read_text())
    assert [e["find_by"] for e in dump["elements"]][:2] == ["Email", "Password"]
    out = capsys.readouterr().out
    assert "dev.fake on Pixel" in out and "'Email'" in out and "001.html" in out
    (device,) = fakes.devices
    assert device.names()[:2] == ["install", "launch"]
    assert not {"clear_data", "reinstall"} & set(device.names())  # the app keeps its data
    assert device.closed and PROMPTS == [] and fakes.looked_for == [("android", "Pixel")]


def test_each_enter_saves_the_screen_again_until_q(app, tmp_path):
    fakes = Fakes()
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", answers=["", " Q "]) == 0
    assert saved(tmp_path)[1][::4] == ["001.html", "002.html"]
    assert PROMPTS == [inspect_cli.PROMPT] * 2


def test_the_end_of_input_stops_it(app, tmp_path):
    fakes = Fakes()
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", answers=[EOFError()]) == 0
    assert len(saved(tmp_path)[1]) == 4 and fakes.devices[0].closed


def test_ctrl_c_puts_the_device_back(app, capsys):
    fakes = Fakes()
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", answers=[KeyboardInterrupt()]) == 130
    assert fakes.devices[0].closed
    assert "stopped (SIGINT): devices put back" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("build", "message"),
    [
        ("missing.apk", "error: App not found: missing.apk"),
        ("app.txt", "error: Unknown app type 'app.txt'"),
    ],
)
def test_a_build_that_isnt_one_is_an_error(app, tmp_path, capsys, build, message):
    (tmp_path / "app.txt").write_text("")
    assert inspect(Fakes(), build, "--device", "Pixel", "--out", "res") == 2
    assert capsys.readouterr().err.startswith(message)


def test_a_device_that_isnt_running_is_an_error(app, capsys):
    fakes = Fakes()
    fakes.missing.add("Pixel")
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res") == 2
    assert "No connected device called 'Pixel'" in capsys.readouterr().err and not fakes.devices


def test_device_and_out_must_be_given(app, capsys):
    with pytest.raises(SystemExit):
        inspect(Fakes(), "app.apk")
    assert "--device" in capsys.readouterr().err


def test_the_screen_is_saved_once_it_stopped_moving(app, tmp_path):
    """Measured: saved at once, an app just launched was caught mid-animation, its numbers still loading."""
    loading, loaded = screen_with("Loading..."), screen_with("3 tasks")
    fakes = Fakes()
    given(fakes, FakeDevice(loading, loaded, loaded))
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", "--once") == 0
    folder, _ = saved(tmp_path)
    assert "'3 tasks'" in (folder / "001.txt").read_text()
    assert fakes.clock.slept == [inspect_cli.SETTLE.interval] * 2


def test_a_screen_that_never_stops_moving_is_saved_as_it_was(app, tmp_path, capsys):
    fakes = Fakes()
    device = FakeDevice()
    device.drawn = [str(i) for i in range(100)]  # drawn differently at every read: an animation that never ends
    given(fakes, device)
    assert inspect(fakes, "app.apk", "--device", "Pixel", "--out", "res", "--once") == 0
    assert "the screen didn't stop moving within 10s: saved as it was" in capsys.readouterr().out
    assert len(saved(tmp_path)[1]) == 4
