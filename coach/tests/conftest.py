"""Shared pytest fixtures + path setup for the coach suite.

Adds coach/bin/ to sys.path so `import reward_hints, scoring, merge` works
whether you run pytest from ~/.claude/coach/ or from the shareable bundle.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent / "bin"
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))
