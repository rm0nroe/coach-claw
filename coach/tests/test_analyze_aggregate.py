"""analyze.py aggregator: per-project skill invocation breakdown.

Locks in the data shape that flows through insights.sh → merge.py and
ultimately becomes the rolling accumulator the inventory inference
reads. Pre-2026-04-24 the aggregator collapsed all projects into a
flat `skills_used` Counter and lost the project association — this
file guards that the new `skills_by_project` field stays correct
across the canonical cases.
"""
from __future__ import annotations

import analyze


# --- _project_name_from_slug -----------------------------------------------

def test_project_name_from_simple_slug():
    """The common case: `~/Desktop/dev/widget` becomes the slug
    `-Users-alice-Desktop-dev-widget`. Last segment wins."""
    assert analyze._project_name_from_slug(
        "-Users-alice-Desktop-dev-widget") == "widget"


def test_project_name_from_hyphenated_slug_collapses_to_last_segment():
    """Documented limitation: hyphens in original project names
    collide with the slash-to-dash separator. `acme-app` becomes
    `app`. The hook tokenizer compensates by splitting cwd anchors
    on dashes too, so the partial still matches at filter time."""
    assert analyze._project_name_from_slug(
        "-Users-r-Desktop-dev-acme-app") == "app"


def test_project_name_from_empty_or_garbage():
    assert analyze._project_name_from_slug("") == ""
    assert analyze._project_name_from_slug(None or "") == ""
    # Trailing dashes stripped before split.
    assert analyze._project_name_from_slug("-Users-x-foo--") == "foo"


def test_project_name_lowercases():
    """Anchor-token comparison is lowercase on the hook side; emit
    lowercase at source so they line up without the consumer needing
    to re-normalize."""
    assert analyze._project_name_from_slug(
        "-Users-r-Desktop-dev-MyProject") == "myproject"


# --- aggregate(): skills_by_project shape ----------------------------------

def _make_session(*, project: str, skills: dict[str, int],
                  assistant_turns: int = 5) -> dict:
    """Minimal session shape sufficient for aggregate() to consume.
    aggregate() only reads project, skills_invoked, and a few fields
    from the detection branches; we provide the per-skill counts and
    fill the rest with neutral defaults."""
    return {
        "project": project,
        "skills_invoked": dict(skills),
        "session_hash": "abcd1234",
        "tool_counts": {},
        "user_turns": 1,
        "assistant_turns": assistant_turns,
        "first_ts": None,
        "last_ts": None,
        "first_user_ts": None,
        "first_edit_ts": None,
        "first_plan_ts": None,
        "task_create_count": 0,
        "exit_plan_count": 0,
        "edit_count": 0,
        "write_count": 0,
        "bash_count": 0,
        "commit_count": 0,
        "test_run_count": 0,
        "has_any_commit": False,
        "has_any_test_run": False,
        "bash_rm_rf_count": 0,
        "read_count": 0,
        "grep_count": 0,
        "glob_count": 0,
        "agent_count": 0,
        "skill_count": sum(skills.values()),
        "sec_first_user_to_first_edit": None,
        "plan_before_edit": False,
    }


def test_aggregate_emits_skills_by_project():
    """The new emit. Single session, single project, single skill —
    smallest case that proves the pipe is open."""
    sessions = [_make_session(
        project="-Users-r-Desktop-dev-service",
        skills={"deploy-staging": 3})]
    _detections, summary = analyze.aggregate(sessions)
    assert summary["skills_by_project"] == {"service": {"deploy-staging": 3}}


def test_aggregate_sums_across_sessions_in_same_project():
    sessions = [
        _make_session(project="-Users-r-Desktop-dev-service",
                      skills={"deploy-staging": 2}),
        _make_session(project="-Users-r-Desktop-dev-service",
                      skills={"deploy-staging": 1, "design": 1}),
    ]
    _detections, summary = analyze.aggregate(sessions)
    assert summary["skills_by_project"]["service"]["deploy-staging"] == 3
    assert summary["skills_by_project"]["service"]["design"] == 1


def test_aggregate_separates_distinct_projects():
    sessions = [
        _make_session(project="-Users-r-Desktop-dev-service",
                      skills={"deploy-staging": 2}),
        _make_session(project="-Users-r-Desktop-dev-widget",
                      skills={"widget-build": 4}),
    ]
    _detections, summary = analyze.aggregate(sessions)
    sbp = summary["skills_by_project"]
    assert sbp["service"] == {"deploy-staging": 2}
    assert sbp["widget"] == {"widget-build": 4}


def test_aggregate_skips_sessions_with_no_project():
    """If a session somehow lacks a project (corrupted state, edge
    case from a transcript without a parent dir), it must not crash
    or attribute its invocations to an empty-string project key."""
    sessions = [
        _make_session(project="", skills={"deploy-staging": 1}),
        _make_session(project="-Users-r-Desktop-dev-service",
                      skills={"deploy-staging": 1}),
    ]
    _detections, summary = analyze.aggregate(sessions)
    assert "" not in summary["skills_by_project"]
    assert summary["skills_by_project"] == {"service": {"deploy-staging": 1}}


def test_aggregate_keeps_skills_used_in_sync_with_skills_by_project():
    """The flat skills_used Counter and the per-project breakdown are
    derived from the same source. Their totals must match — drift
    here would be a sign of a bookkeeping error in aggregate()."""
    sessions = [
        _make_session(project="-Users-r-Desktop-dev-service",
                      skills={"deploy-staging": 2, "design": 1}),
        _make_session(project="-Users-r-Desktop-dev-widget",
                      skills={"design": 3}),
    ]
    _detections, summary = analyze.aggregate(sessions)
    flat_total = summary["skills_used"]
    by_proj_totals: dict[str, int] = {}
    for proj_skills in summary["skills_by_project"].values():
        for sid, count in proj_skills.items():
            by_proj_totals[sid] = by_proj_totals.get(sid, 0) + count
    assert flat_total == by_proj_totals
