"""``jevtest inspect``: what's on the app's screen, as steps name it, for writing a test.

It installs and starts the app on the device, as a run does but keeping the app's data, then saves the screen:
a screenshot, and beside it the elements with the names steps find them by, as a list (.txt), for an agent (.json)
and as a page that boxes each element on the screenshot (.html). Then it waits: use the app on the device, press
Enter to save the screen again, or type q to stop. With --once it saves the screen once and stops, for scripts and
agents.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jevtest.adapters.reports.screen_notes import ScreenNotesFiles
from jevtest.adapters.testfile.loader import platform_of
from jevtest.domain.failures import TestFileError
from jevtest.domain.inspection import notes
from jevtest.domain.ports import Clock, Device
from jevtest.domain.screen import Screen
from jevtest.domain.settings import Settings

from .run import FindDevice, MakeDevice, results_folder

PROMPT = "Enter: save the screen again · q: stop "

SETTLE = Settings()
"""How long, and how often, to wait for the screen to stop moving before saving it: a step's defaults."""


@dataclass(frozen=True)
class InspectOptions:
    """What ``jevtest inspect`` was given.

    Attributes:
        app: The build: .apk/.aab for Android, .app/.zip/.ipa for iOS.
        device: The running device to use, by name (as a test file names it).
        out: Where to save (each inspect adds a timestamped folder in it).
        once: Save the screen once and stop, instead of waiting for Enter.
    """

    app: Path
    device: str
    out: Path
    once: bool


def inspect_command(
    options: InspectOptions,
    make_device: MakeDevice,
    find_device: FindDevice,
    *,
    clock: Clock,
    read_line: Callable[[str], str],
    say: Callable[[str], None],
) -> int:
    """Install and start the app, then save the screen, again on each Enter until q; 0 when done.

    Raises:
        JevtestError: Something needs fixing first (the message says what).
    """
    if not options.app.exists():
        raise TestFileError(f"App not found: {options.app}")
    platform = platform_of(options.app)
    find_device(platform, options.device, options.app)
    out = results_folder(options.out, time.strftime("%Y%m%d-%H%M%S"))
    device = make_device(platform, options.device, options.app, lambda message: say(f"  {message}..."))
    files = ScreenNotesFiles()
    try:
        app_id = device.install(options.app)
        device.prepare_for_test()
        device.launch()
        say(f"{app_id} on {options.device}: use the app on the device, and save each screen you want to name")
        n = 0
        while True:
            n += 1
            shot = out / f"{n:03d}.png"
            still = _still_screen(device, clock)
            if still is None:
                say(f"the screen didn't stop moving within {SETTLE.timeout:g}s: saved as it was")
            device.screenshot(shot)
            screen = notes(still or device.screen(), lambda text: text)
            files.write(shot, screen)
            say(screen.text().rstrip("\n"))
            say(f"saved {shot}, with {shot.with_suffix('.html').name} (open it to see each element boxed), .json, .txt")
            if options.once:
                break
            try:
                answer = read_line(PROMPT)
            except EOFError:
                break
            if answer.strip().lower() == "q":
                break
    finally:
        device.close()
    return 0


def _still_screen(device: Device, clock: Clock) -> Screen | None:
    """The screen once it stopped moving, as a `screenshot:` step waits for it; None if it didn't within `SETTLE`.

    Stopped moving: the screen, and how its text is drawn, the same at two reads in a row (measured: saved at once,
    an app just launched was caught mid-animation, its numbers still loading).
    """
    end = clock.now() + SETTLE.timeout
    previous: tuple[Screen, str] | None = None
    while True:
        screen = device.screen()
        drawn = (screen, device.looks([e for e in screen.elements if e.text and not e.editable]))
        if drawn == previous:
            return screen
        if clock.now() >= end:
            return None
        previous = drawn
        clock.sleep(SETTLE.interval)
