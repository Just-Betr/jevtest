"""The things that can go wrong, as jevtest's own exception types.

Adapters catch the exceptions of the tools they wrap (subprocess, HTTP, YAML, ...) and raise one of these
instead, so nothing outside an adapter ever handles a tool's exception. Every message says what to fix.
"""

from __future__ import annotations


class JevtestError(Exception):
    """Base class: something jevtest could not do. The message says why and what to fix."""


class TestFileError(JevtestError):
    """A test file, a file it includes, its ``.env``, or the command line is wrong."""

    __test__ = False  # not a pytest test class


class DeviceError(JevtestError):
    """The device, its tools, or jevtest's on-device agent couldn't do something."""


class ModelError(JevtestError):
    """The decision model (Jev) couldn't be asked, or answered something unusable."""


class NotRecorded(ModelError):
    """``--lock frozen``: the screen and question aren't in the lockfile.

    The screen may still be changing, so a step that waits looks again until its timeout.
    """


class StepFailed(JevtestError):
    """A step or check didn't get the result the test expects: the test fails, the run goes on."""
