"""skill_inventory.py: SKILL.md frontmatter parsing + `projects` field
coercion.

Covers the path that tags skills with their declared project scope.
The hook consumes this list and uses it as a hard filter, so misreads
here turn into silently-wrong filter decisions at runtime."""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

import skill_inventory as si   # provided by coach/tests/conftest.py


@pytest.fixture
def isolated_argv(monkeypatch):
    """``si.main()`` calls ``argparse.parse_args()`` which reads
    ``sys.argv``. Inside pytest that's pytest's own argv (``tests/``,
    ``-v``, etc.) and argparse bails. Pin it to a clean single-element
    list so the parser sees no flags — the tests rely on defaults."""
    monkeypatch.setattr(sys, "argv", ["skill_inventory.py"])


# --- _parse_frontmatter_text ----------------------------------------------

def test_parse_frontmatter_reads_scalar_fields():
    text = """---
name: demo
description: "Do a demo thing"
---

Body here.
"""
    fm = si._parse_frontmatter_text(text)
    assert fm["name"] == "demo"
    assert fm["description"] == "Do a demo thing"


def test_parse_frontmatter_reads_inline_list():
    text = """---
name: demo
projects: [service, acme-app]
---

Body.
"""
    fm = si._parse_frontmatter_text(text)
    assert fm["projects"] == ["service", "acme-app"]


def test_parse_frontmatter_reads_block_list():
    text = """---
name: demo
projects:
  - service
  - acme-app
  - widget
---
"""
    fm = si._parse_frontmatter_text(text)
    assert fm["projects"] == ["service", "acme-app", "widget"]


def test_parse_frontmatter_no_frontmatter():
    assert si._parse_frontmatter_text("just a markdown file\n") == {}


def test_parse_frontmatter_malformed_yaml_is_silent():
    """The deterministic insights pass runs unattended on cron; a single
    broken SKILL.md must not crash the whole inventory pass. Bad YAML →
    empty dict → skill is skipped for lack of description (its own
    graceful failure further up)."""
    text = """---
this is: not: valid: yaml
  - because
     indentation:
---
body
"""
    assert si._parse_frontmatter_text(text) == {}


def test_parse_frontmatter_non_dict_root_returns_empty():
    """If a SKILL.md author writes a YAML list at the top level instead
    of a mapping, we still degrade to empty rather than crash."""
    text = """---
- alpha
- beta
---
"""
    assert si._parse_frontmatter_text(text) == {}


# --- _coerce_projects ------------------------------------------------------

def test_coerce_projects_list():
    assert si._coerce_projects(["service", "widget"]) == ["service", "widget"]


def test_coerce_projects_scalar_string_becomes_single_list():
    """Single-project shorthand: ``projects: service`` is common user
    shorthand and shouldn't be rejected just because it isn't a list."""
    assert si._coerce_projects("service") == ["service"]


def test_coerce_projects_none_is_empty():
    assert si._coerce_projects(None) == []


def test_coerce_projects_strips_empty_and_whitespace():
    assert si._coerce_projects(["service", "", "  ", "widget  "]) == ["service", "widget"]


def test_coerce_projects_ignores_non_strings():
    # A number or dict snuck into the list must not crash and must be
    # filtered — we only emit strings downstream.
    assert si._coerce_projects(["service", 42, {"x": 1}, "widget"]) == ["service", "widget"]


def test_coerce_projects_unknown_type_is_empty():
    assert si._coerce_projects(42) == []
    assert si._coerce_projects({"a": 1}) == []


# --- end-to-end: main() emits the expected shape ---------------------------

def _write_skill(root: Path, sid: str, frontmatter: str) -> None:
    d = root / sid
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\nBody.\n")


def test_inventory_emits_projects_field_when_present(
        tmp_path, monkeypatch, isolated_argv):
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "deploy-staging",
                 "description: Iterate on the avatar\nprojects: [service]")
    _write_skill(skills, "capability-loop",
                 "description: Continuous improvement loop")
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    by_id = {h["id"]: h for h in hints}
    assert by_id["deploy-staging"]["projects"] == ["service"]
    # Untagged skill: empty list, not missing.
    assert by_id["capability-loop"]["projects"] == []


def test_inventory_skips_skill_without_description(
        tmp_path, monkeypatch, isolated_argv):
    """Pre-existing behavior: no description → skill dropped. Adding
    `projects` support must not change the drop rule."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "mystery", "projects: [service]")   # no description
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    assert hints == []


# --- _infer_projects (rolling-history scope inference) ---------------------

def test_infer_projects_zero_history_returns_empty():
    """Cold-start: no skill has been invoked anywhere yet. Inference
    must return [] so the hook falls through to the untagged rules."""
    assert si._infer_projects("deploy-staging", {}) == []


def test_infer_projects_below_threshold_returns_empty():
    """A single invocation in one project is noise — could be an
    experiment from the wrong cwd. Don't tag until ≥2 hits prove
    intent. Threshold default is INFER_THRESHOLD = 2."""
    by_project = {"service": {"deploy-staging": 1}}
    assert si._infer_projects("deploy-staging", by_project) == []


def test_infer_projects_single_project_at_threshold_tags():
    """≥2 invocations in exactly one project → tagged with that
    project. This is the canonical inference success case."""
    by_project = {"service": {"deploy-staging": 2}}
    assert si._infer_projects("deploy-staging", by_project) == ["service"]


def test_infer_projects_one_project_above_threshold_other_below_tags():
    """The project with ≥2 hits counts as observed; the project with
    1 hit doesn't. So the skill is still considered single-project
    scoped — the noisy one-off doesn't graduate it to global."""
    by_project = {
        "service":    {"deploy-staging": 5},
        "widget": {"deploy-staging": 1},   # below threshold; ignored
    }
    assert si._infer_projects("deploy-staging", by_project) == ["service"]


def test_infer_projects_two_projects_above_threshold_graduates():
    """Cross-cutting tool: invoked ≥2× in ≥2 projects → return [],
    treating the skill as global. /design-style skills auto-graduate
    once the user proves they're using the skill across projects."""
    by_project = {
        "service":    {"design": 3},
        "widget": {"design": 4},
    }
    assert si._infer_projects("design", by_project) == []


def test_infer_projects_only_other_skills_count():
    """A project whose only history is a DIFFERENT skill must not
    count toward this skill's observed set — guards the dict shape
    against accidental cross-talk between skills."""
    by_project = {
        "service":    {"design": 5},                # not deploy-staging
        "widget": {"deploy-staging": 3},
    }
    assert si._infer_projects("deploy-staging", by_project) == ["widget"]


def test_infer_projects_tolerates_bad_shapes():
    """The cron path can't crash on garbled history. Non-dict project
    buckets, non-numeric counts → silently ignored."""
    by_project = {
        "service":    {"deploy-staging": "lots"},   # bad count
        "broken": "not-a-dict",
        "widget": {"deploy-staging": 3},
    }
    assert si._infer_projects("deploy-staging", by_project) == ["widget"]


def test_infer_projects_threshold_param_actually_changes_behavior():
    """Lock the threshold parameter against drift by using data where
    different threshold values produce GENUINELY DIFFERENT outputs —
    not the same `[]` for every value with a misleading comment.
    A single project at count=1: invisible at threshold=2, observed
    (and tagged) at threshold=1, still invisible at threshold=3."""
    by_project = {"service": {"x": 1}}
    assert si._infer_projects("x", by_project, threshold=2) == []   # below bar
    assert si._infer_projects("x", by_project, threshold=1) == ["service"]  # ≥ bar
    assert si._infer_projects("x", by_project, threshold=3) == []   # below bar


# --- end-to-end: inventory consumes by_project, frontmatter wins -----------

def test_inventory_uses_inference_when_frontmatter_missing(
        tmp_path, monkeypatch, isolated_argv):
    """Skills without frontmatter `projects:` should pick up scope
    from invocation history, threaded through the --skills-by-project
    CLI flag."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "deploy-staging",
                 "description: Iterate on the avatar")   # no projects
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    sbp = tmp_path / "sbp.json"
    sbp.write_text(json.dumps({"service": {"deploy-staging": 5}}))
    monkeypatch.setattr(
        "sys.argv",
        ["skill_inventory.py", "--skills-by-project", str(sbp)])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    by_id = {h["id"]: h for h in hints}
    assert by_id["deploy-staging"]["projects"] == ["service"]


def test_inventory_frontmatter_supersedes_inference(
        tmp_path, monkeypatch, isolated_argv):
    """Explicit frontmatter `projects:` is authoritative — even when
    the rolling history would suggest a different (or graduated)
    scope. Users get deterministic control when they ask for it."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(
        skills, "deploy-staging",
        "description: Iterate on the avatar\nprojects: [service]")
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    # History would normally graduate the skill to global (≥2 in ≥2
    # projects), but the frontmatter must win.
    sbp = tmp_path / "sbp.json"
    sbp.write_text(json.dumps({
        "service":    {"deploy-staging": 5},
        "widget": {"deploy-staging": 5},
    }))
    monkeypatch.setattr(
        "sys.argv",
        ["skill_inventory.py", "--skills-by-project", str(sbp)])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    by_id = {h["id"]: h for h in hints}
    assert by_id["deploy-staging"]["projects"] == ["service"]


def test_inventory_no_history_file_falls_back_to_frontmatter_only(
        tmp_path, monkeypatch, isolated_argv):
    """If --skills-by-project is omitted (e.g., first run before any
    history exists), the inventory still works — inference returns
    [] for everything and only frontmatter-tagged skills get scope."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "untagged", "description: A skill")
    _write_skill(
        skills, "tagged",
        "description: A scoped skill\nprojects: [service]")
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    by_id = {h["id"]: h for h in hints}
    assert by_id["untagged"]["projects"] == []
    assert by_id["tagged"]["projects"] == ["service"]


def test_inventory_explicit_empty_projects_blocks_inference(
        tmp_path, monkeypatch, isolated_argv):
    """A user writing `projects: []` in frontmatter is making a
    deliberate "this skill is cross-project / global" declaration.
    History inference must NOT silently re-tag it to a single project,
    which would reverse the user's intent and quietly hard-block the
    skill outside that wrongly-inferred scope.

    Lock in the distinction: ``projects: []`` (key present, empty list)
    is authoritative. Only ``projects:`` absent entirely falls through
    to inference. Regression for review-finding #1 (2026-04-24)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(
        skills, "global-by-declaration",
        "description: A cross-project skill\nprojects: []")
    monkeypatch.setattr(si, "SKILLS_DIR", skills)

    # Set up a history that WOULD infer scope `[service]` if inference ran.
    sbp = tmp_path / "sbp.json"
    sbp.write_text(json.dumps(
        {"service": {"global-by-declaration": 5}}))
    monkeypatch.setattr(
        "sys.argv",
        ["skill_inventory.py", "--skills-by-project", str(sbp)])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        si.main()
    hints = json.loads(buf.getvalue())
    by_id = {h["id"]: h for h in hints}
    assert by_id["global-by-declaration"]["projects"] == [], (
        "explicit `projects: []` in frontmatter must beat history "
        "inference — otherwise a user declaration silently reverses "
        "into a hard project-scoped filter"
    )
