"""Plugin manifest sanity checks.

`plugin/.claude-plugin/plugin.json` is the entry point Claude Code reads
when `/plugin install coach-claw@./path` runs. Bad JSON or missing
required fields → install fails silently. These tests catch that at
build time, not at install time.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json"


def _manifest():
    return json.loads(MANIFEST.read_text())


def test_manifest_is_valid_json():
    # If this throws, json.loads above already raised — the test exists
    # to make the failure mode crystal clear in the report.
    _manifest()


def test_manifest_has_required_fields():
    m = _manifest()
    for key in ("name", "version", "description"):
        assert key in m, f"plugin.json missing required field: {key!r}"


def test_manifest_name_is_kebab_case():
    name = _manifest()["name"]
    assert re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name), (
        f"plugin name {name!r} must be kebab-case (Claude Code convention "
        f"for plugin name → skill namespace prefix)"
    )


def test_manifest_name_is_coach_claw():
    """Pin the namespace prefix. Changing this is a breaking change for
    every existing user — skill commands rename from /coach-claw:* to
    /<new-name>:*. Don't rename without a deliberate migration."""
    assert _manifest()["name"] == "coach-claw"


def test_manifest_version_is_semver():
    version = _manifest()["version"]
    # Loose semver: MAJOR.MINOR.PATCH with optional pre-release suffix.
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[\w.]+)?", version), (
        f"version {version!r} must be semver (x.y.z, optional -prerelease)"
    )


def test_manifest_author_is_object_with_name():
    """Plugin manifest schema requires `author` to be an OBJECT with
    a `name` field — strings are rejected by `claude plugin validate`.

    Regression guard: 2026-05-08 the manifest shipped initially with
    `author: "Ryan Monroe"` (string) and the official validator
    flagged it as `Invalid input: expected object, received string`.
    """
    author = _manifest().get("author")
    assert isinstance(author, dict), (
        f"author must be an object (got {type(author).__name__}). "
        f"Schema: {{name: required, email: optional}}"
    )
    assert "name" in author and isinstance(author["name"], str) and author["name"].strip(), (
        f"author.name must be a non-empty string; got {author!r}"
    )
