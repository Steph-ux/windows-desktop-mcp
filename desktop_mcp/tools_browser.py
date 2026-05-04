from __future__ import annotations

import sys

from .tools import browser_sessions as _impl


sys.modules[__name__] = _impl
