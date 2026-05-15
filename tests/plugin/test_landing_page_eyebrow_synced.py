"""CI gate: `docs/index.html` hero eyebrow must show the current npm
version from `package.json:version`.

The /ship Phase 7 wiring patches the eyebrow line via a single-match
grep. If a future template edit either (a) renames the `.eyebrow
.reveal` class, (b) introduces a second matching line, or (c) drifts
the version out of lockstep with `package.json`, the ship pipeline's
single-match assertion silently breaks AND the public GitHub Pages
site at https://rm0nroe.github.io/coach-claw/ shows a stale release.

Three invariants checked:
  1. `package.json:version` is parseable.
  2. `docs/index.html` contains exactly ONE line matching the eyebrow
     class signature `class="eyebrow reveal"`.
  3. That single line embeds `v<package.json:version>` verbatim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_JSON = REPO_ROOT / "package.json"
LANDING_PAGE = REPO_ROOT / "docs" / "index.html"

EYEBROW_CLASS_PATTERN = re.compile(r'class="eyebrow reveal"')


def _read_npm_version() -> str:
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    version = data.get("version")
    assert version, f"{PACKAGE_JSON} missing 'version' field"
    return version


def _eyebrow_lines() -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(LANDING_PAGE.read_text(encoding="utf-8").splitlines(), start=1):
        if EYEBROW_CLASS_PATTERN.search(line):
            hits.append((lineno, line))
    return hits


def test_landing_page_eyebrow_is_unique():
    """Exactly one eyebrow line — ship Phase 7's grep-then-edit relies
    on this invariant. Zero matches = template renamed; multiple =
    ambiguous patch target."""
    hits = _eyebrow_lines()
    assert len(hits) == 1, (
        f"expected exactly 1 line matching `class=\"eyebrow reveal\"` in "
        f"{LANDING_PAGE.relative_to(REPO_ROOT)}, found {len(hits)}. "
        f"If the template was intentionally restructured, update /ship "
        f"Phase 7 and this test together.\n"
        + "\n".join(f"  {LANDING_PAGE.relative_to(REPO_ROOT)}:{n}: {l.strip()}" for n, l in hits)
    )


def test_landing_page_eyebrow_matches_npm_version():
    """The eyebrow line must contain `v<package.json:version>` so the
    public landing page never drifts from the npm release."""
    hits = _eyebrow_lines()
    if not hits:
        pytest.skip("eyebrow line not found — covered by sibling uniqueness test")
    lineno, line = hits[0]
    expected = f"v{_read_npm_version()}"
    assert expected in line, (
        f"{LANDING_PAGE.relative_to(REPO_ROOT)}:{lineno} eyebrow drifted from "
        f"package.json. expected `{expected}` to appear in the line, got:\n"
        f"  {line.strip()}\n"
        f"Bump the eyebrow as part of /ship Phase 7, or re-run /ship if a "
        f"prior pipeline aborted before this step."
    )
