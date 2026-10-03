"""Moved to ``matrx_coding_history.scope`` (packages/matrx-coding-history), shared with Matrx 2.

This path stays an alias of that module — the SAME module object — so every import, isinstance
check, monkeypatch and private name that used this path keeps working unchanged.
"""

import sys
from typing import TYPE_CHECKING

import matrx_coding_history.scope as _moved

if TYPE_CHECKING:  # what static checkers see; at run time this module IS the package module
    from matrx_coding_history.scope import *  # noqa: F403

sys.modules[__name__] = _moved
