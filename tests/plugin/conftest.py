"""Shared setup for plugin-build tests.

Tests in this directory exercise the plugin distribution
artifact (`plugin/`) — manifest, skill namespacing, build-script sync,
PyYAML bootstrap. They run from the bundle only; the live coach install
under `~/.claude/coach/` doesn't ship `plugin/`, so this whole tree is
absent there and pytest never collects it.

Adds `coach/bin/` to sys.path so tests can `import statusline_self_patch`
etc. — the modules are bundled from the canonical CLI source-of-truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent.parent / "coach" / "bin"
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))
