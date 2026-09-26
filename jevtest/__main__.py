"""``python -m jevtest``: the same as the ``jevtest`` command."""

import sys

from .cli.main import main

sys.exit(main())
