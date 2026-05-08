"""marketplace/.claude-plugin/marketplace.json — schema validity.

The marketplace catalog is what Claude Code reads when a user runs
`/plugin marketplace add rm0nroe/coach-claw-plugin-marketplace`. Bad
JSON or schema violations break discovery for every user.

Schema reference: https://code.claude.com/docs/en/plugin-marketplaces#marketplace-schema
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MARKETPLACE = REPO_ROOT / "marketplace" / ".claude-plugin" / "marketplace.json"

# Reserved marketplace names per the docs — third-party marketplaces
# CANNOT use these.
RESERVED_NAMES = {
    "claude-code-marketplace",
    "claude-code-plugins",
    "claude-plugins-official",
    "anthropic-marketplace",
    "anthropic-plugins",
    "agent-skills",
    "knowledge-work-plugins",
    "life-sciences",
}


def _data():
    return json.loads(MARKETPLACE.read_text())


def test_marketplace_json_is_valid():
    _data()  # raises if malformed


def test_required_top_level_fields():
    data = _data()
    for key in ("name", "owner", "plugins"):
        assert key in data, f"marketplace.json missing required field: {key!r}"


def test_marketplace_name_is_kebab_case():
    name = _data()["name"]
    assert re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name), (
        f"marketplace name {name!r} must be kebab-case (Claude Code "
        f"convention; users see it as @{name} in install commands)"
    )


def test_marketplace_name_not_reserved():
    name = _data()["name"]
    assert name not in RESERVED_NAMES, (
        f"marketplace name {name!r} is reserved for Anthropic. "
        f"Reserved names: {sorted(RESERVED_NAMES)}"
    )
    # Anti-impersonation: names containing 'official', 'anthropic',
    # 'claude-code-' prefix likely get rejected by Anthropic's sync
    # even if not literally on the reserved list.
    assert "anthropic" not in name.lower(), (
        f"marketplace name {name!r} contains 'anthropic' — likely "
        f"blocked by impersonation policy"
    )


def test_owner_has_name():
    owner = _data()["owner"]
    assert isinstance(owner, dict), "owner must be an object"
    assert "name" in owner and isinstance(owner["name"], str) and owner["name"].strip()


def test_plugins_is_nonempty_list():
    plugins = _data()["plugins"]
    assert isinstance(plugins, list), "plugins must be an array"
    assert len(plugins) >= 1, "marketplace must list at least one plugin"


def test_each_plugin_has_required_fields():
    for plugin in _data()["plugins"]:
        assert isinstance(plugin, dict)
        assert "name" in plugin, f"plugin missing name: {plugin}"
        assert "source" in plugin, f"plugin {plugin.get('name')!r} missing source"
        # Plugin names must also be kebab-case.
        assert re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", plugin["name"]), (
            f"plugin name {plugin['name']!r} must be kebab-case"
        )


def test_no_duplicate_plugin_names():
    names = [p["name"] for p in _data()["plugins"]]
    assert len(names) == len(set(names)), (
        f"duplicate plugin names: {[n for n in names if names.count(n) > 1]}"
    )


def test_coach_claw_plugin_present():
    """The whole point of this marketplace. Pin it."""
    plugins = _data()["plugins"]
    assert any(p["name"] == "coach-claw" for p in plugins), (
        "marketplace does not list a 'coach-claw' plugin — that's the "
        "only thing this marketplace exists to distribute"
    )


def test_coach_claw_source_is_git_subdir():
    """We deliberately use git-subdir so the plugin lives inside the
    monorepo (coach-claw) alongside the npm CLI source. No
    separate plugin repo. Pin this choice."""
    plugin = next(p for p in _data()["plugins"] if p["name"] == "coach-claw")
    src = plugin["source"]
    assert isinstance(src, dict), "source must be an object for git-subdir"
    assert src.get("source") == "git-subdir", (
        f"coach-claw source must be 'git-subdir' (we ship from the "
        f"plugin/ subdir of the main repo, not a separate plugin repo); "
        f"got {src.get('source')!r}"
    )
    assert "url" in src and "path" in src, (
        f"git-subdir source requires url + path; got {src}"
    )
    assert src["path"] == "plugin", (
        f"path must be 'plugin' (subdir name in coach-claw); "
        f"got {src['path']!r}"
    )


def test_coach_claw_source_url_is_canonical():
    """Pin the source repo URL. If we ever fork/rename, this test
    fails loudly so the marketplace doesn't silently ship from the
    wrong source."""
    plugin = next(p for p in _data()["plugins"] if p["name"] == "coach-claw")
    url = plugin["source"]["url"]
    assert "rm0nroe/coach-claw" in url, (
        f"source URL {url!r} should reference rm0nroe/coach-claw"
    )
