"""`typing.override`, which Python adds in 3.12; from `typing_extensions` on 3.11."""

import sys

if sys.version_info >= (3, 12):
    from typing import override
else:  # pragma: no cover - the tests run on the newest Python; CI covers 3.11
    from typing_extensions import override

__all__ = ["override"]
