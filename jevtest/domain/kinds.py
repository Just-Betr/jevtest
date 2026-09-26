"""Small closed sets of values, as enums instead of loose strings."""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    """A mobile platform jevtest can test on."""

    ANDROID = "android"
    IOS = "ios"


class AppState(StrEnum):
    """Where the app under test is, as the device reports it."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"
    NOT_RUNNING = "not_running"


class Direction(StrEnum):
    """A direction to scroll or swipe."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class Orientation(StrEnum):
    """A device orientation."""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    LANDSCAPE_RIGHT = "landscape_right"
    PORTRAIT_UPSIDE_DOWN = "portrait_upside_down"


class Gesture(StrEnum):
    """A touch on one point of the screen."""

    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"


class Status(StrEnum):
    """The outcome of a test, step or check."""

    PASS = "pass"
    FAIL = "fail"
