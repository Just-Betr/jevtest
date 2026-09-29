"""``python -m jevtest``: the same as the ``jevtest`` command."""

import sys

from .cli import command

sys.exit(command())
