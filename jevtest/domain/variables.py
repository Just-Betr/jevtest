"""``${NAME}`` values: written as names in test files, filled in only where the app needs them."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .words import nfc

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A ``${NAME}`` reference."""


def names_in(text: str) -> list[str]:
    """The variable names `text` refers to, in order."""
    return VARIABLE.findall(text)


def fill(text: str, variables: Mapping[str, str]) -> str:
    """`text` with each ``${NAME}`` replaced by its value.

    Used only for what the app sees (typed text, compared text, searched-for elements, URLs). Logs, reports and
    everything sent to Jev keep the names (see `hide`), so secrets stay out of them.

    Raises:
        KeyError: `text` names a variable that `variables` doesn't have.
    """
    return VARIABLE.sub(lambda m: variables[m.group(1)], text)


def hide(text: str, variables: Mapping[str, str]) -> str:
    """`text` with every variable's value replaced by its ``${NAME}``: the reverse of `fill`.

    Used on everything read from the screen before it leaves the device's side: what Jev is sent, and what
    jevtest prints. An app that shows a value (a signed-in user's email) keeps it out of Jev's requests, logs and
    lockfile keys. Longer values are replaced first, so a value inside another is never left half hidden. Both are
    compared in one Unicode encoding (NFC): an é shown as an e and an accent is still the value's é.
    """
    text = nfc(text)
    for name, value in sorted(variables.items(), key=lambda item: -len(item[1])):
        if value:
            text = text.replace(nfc(value), f"${{{name}}}")
    return text
