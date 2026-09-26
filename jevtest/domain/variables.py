"""``${NAME}`` values: written as names in test files, filled in only where the app needs them."""

from __future__ import annotations

import re
from collections.abc import Mapping

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
"""A ``${NAME}`` reference."""


def names_in(text: str) -> list[str]:
    """The variable names `text` refers to, in order."""
    return VARIABLE.findall(text)


def fill(text: str, variables: Mapping[str, str]) -> str:
    """`text` with each ``${NAME}`` replaced by its value.

    Used only for what the app sees (typed text, compared text, searched-for elements, URLs). Logs, reports and
    Jev's goals keep the names, so secrets stay out of them.

    Raises:
        KeyError: `text` names a variable that `variables` doesn't have.
    """
    return VARIABLE.sub(lambda m: variables[m.group(1)], text)
