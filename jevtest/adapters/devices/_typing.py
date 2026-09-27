"""`typing.override`, which Python adds in 3.12; from `typing_extensions` on 3.11."""

import sys

# Each branch runs only on its own Python version, so neither counts toward one version's coverage.
if sys.version_info >= (3, 12):  # pragma: no cover
    from typing import override
else:  # pragma: no cover
    from typing_extensions import override

__all__ = ["override"]
