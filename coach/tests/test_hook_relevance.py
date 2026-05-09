"""coach-user-prompt.py: session-relevance filtering for skill hints.

Regression guard for the bug where an off-topic skill (a frontend-animation
skill during backend debugging, or similar mismatch) was proposed for
sessions that had nothing to do with it, producing coach-tip reward lines
disconnected from the work being done.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cup():
    """Load coach-user-prompt.py as a module. It's not a package, so we go
    via importlib rather than a direct import statement."""
    repo_path = Path(__file__).resolve().parents[2] / "hooks" / "coach-user-prompt.py"
    path = repo_path if repo_path.exists() else Path.home() / ".claude" / "hooks" / "coach-user-prompt.py"
    if not path.exists():
        pytest.skip(f"hook not installed at {path}")
    spec = importlib.util.spec_from_file_location("cup_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_transcript(path: Path, tool_uses: list[dict]) -> None:
    lines = [
        json.dumps({"message": {"content": [dict(tu, type="tool_use")]}})
        for tu in tool_uses
    ]
    path.write_text("\n".join(lines) + "\n")


def _write_timed_transcript(path: Path, tool_uses: list[dict]) -> None:
    lines = []
    for i, tu in enumerate(tool_uses):
        lines.append(json.dumps({
            "timestamp": f"2026-01-01T00:00:{i:02d}+00:00",
            "message": {"content": [dict(tu, type="tool_use")]},
        }))
    path.write_text("\n".join(lines) + "\n")


def test_find_transcript_accepts_project_transcript(cup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    transcript = home / ".claude/projects/acme/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    monkeypatch.setenv("HOME", str(home))

    assert cup._find_transcript({"transcript_path": str(transcript)}) == transcript.resolve()


def test_find_transcript_accepts_camelcase_payload_key(cup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    transcript = home / ".claude/projects/acme/session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    monkeypatch.setenv("HOME", str(home))

    assert cup._find_transcript({"transcriptPath": str(transcript)}) == transcript.resolve()


def test_find_transcript_rejects_outside_projects(cup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    outside = home / ".ssh/config"
    outside.parent.mkdir(parents=True)
    outside.write_text("Host example\n")
    monkeypatch.setenv("HOME", str(home))

    assert cup._find_transcript({"transcript_path": str(outside)}) is None


def test_find_transcript_rejects_missing_path(cup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    missing = home / ".claude/projects/acme/missing.jsonl"
    monkeypatch.setenv("HOME", str(home))

    assert cup._find_transcript({"transcript_path": str(missing)}) is None


# --- tokenizer unit ---------------------------------------------------------

def test_tokenize_splits_file_paths(cup):
    toks = cup._tokenize("animation.ts")
    assert "animation" in toks
    assert "ts" in toks


def test_tokenize_strips_noise(cup):
    toks = cup._tokenize("the user uses a tool for the task")
    assert "task" not in toks   # in noise list
    assert "tool" not in toks


def test_tokenize_applies_min_length(cup):
    # bare 2-char words are dropped unless in the preserved-shorts list
    assert "is" not in cup._tokenize("is a bigword")
    # common code/ext tokens are kept even at 2 chars
    assert "py" in cup._tokenize("edit a py file")
    assert "ui" in cup._tokenize("a ui baseline")


# --- signal + fit integration ----------------------------------------------

def test_python_work_filters_off_topic_frontend_skill(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest tests/"}},
        {"name": "Edit", "input": {"file_path": "/p/.claude/coach/bin/merge.py"}},
        {"name": "Edit", "input": {"file_path": "/p/.claude/coach/bin/scoring.py"}},
        {"name": "Edit", "input": {"file_path": "/p/.claude/coach/tests/test_merge.py"}},
    ])
    signal, anchors = cup._session_signal(t, "/p/.claude/coach")

    frontend_anim = {"id": "frontend-anim",
                     "short_tip": "Scroll-linked animations, pinning, scrub."}
    # Exactly the bug this fix targets: frontend skill must NOT fit a Python session.
    assert cup._skill_fits_session(frontend_anim, signal, anchors) is False


def test_frontend_work_keeps_matching_skill(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_transcript(t, [
        {"name": "Edit", "input": {"file_path": "/a/components/Hero.tsx"}},
        {"name": "Bash", "input": {"command": "pnpm dev"}},
        {"name": "Edit", "input": {"file_path": "/a/lib/animations.ts"}},
        {"name": "Edit", "input": {"file_path": "/a/components/ScrollTriggers.tsx"}},
        {"name": "Edit", "input": {"file_path": "/a/lib/scrub.ts"}},
    ])
    signal, anchors = cup._session_signal(t, "/a")
    frontend_anim = {"id": "frontend-anim",
                     "short_tip": "Scroll-linked animations, pinning, scrub triggers."}
    # Overlap is distinctive and multi-token: animations, scrolltriggers, scrub.
    assert cup._skill_fits_session(frontend_anim, signal, anchors) is True


def test_thin_signal_is_strict(cup):
    """When the signal is thin (<3 tokens), skill tips are suppressed rather
    than blindly kept. The cost of an off-topic skill reward line is high;
    the cost of a missed skill tip is low (another turn comes along).
    Weakness/strength tips are unaffected — they're about user behavior,
    not an installed-skill catalog."""
    frontend_anim = {"id": "frontend-anim", "short_tip": "Scroll animations."}
    assert cup._skill_fits_session(frontend_anim, set()) is False
    assert cup._skill_fits_session(frontend_anim, {"only", "two"}) is False


def test_single_common_token_not_enough(cup):
    """A single overlap on a common-dev-vocab token (e.g. 'test', 'file')
    doesn't prove relevance — those show up in nearly every session."""
    frontend_anim = {"id": "frontend-anim",
                     "short_tip": "Scroll-linked animations test code."}
    # Session has three tokens but the only skill overlap is 'test'
    # (in _COMMON_DEV_VOCAB).
    signal = {"test", "backend", "daemon"}
    assert cup._skill_fits_session(frontend_anim, signal) is False


def test_single_distinctive_token_is_not_enough(cup):
    """A single distinctive-token overlap is NOT enough. Words like
    `scroll` / `mobile` / `ssh` are distinctive in the vocabulary sense
    but in the real world they span unrelated projects (an asset-pipeline
    skill and an AI-agents skill can both mention `mobile` without being
    about the same work). The policy after the 2026-04-24 fix: require
    ≥2 distinctive tokens, or a direct project-anchor overlap."""
    frontend_anim = {"id": "frontend-anim",
                     "short_tip": "Scroll-linked animations."}
    signal = {"scroll", "backend", "daemon"}   # 'scroll' is distinctive
    assert cup._skill_fits_session(frontend_anim, signal) is False


def test_two_distinctive_tokens_clear_the_bar(cup):
    """Two distinctive-token overlaps pass. The previous rule accepted a
    single distinctive token; the new rule requires two, so an accidental
    cross-project overlap on one plumbing-ish word can no longer fire."""
    skill = {"id": "frontend-anim",
             "short_tip": "Scroll-linked animations and pinning."}
    signal = {"scroll", "animations", "coach", "bin"}
    # Overlap = {scroll, animations}; both distinctive. Count ≥ 2 → passes.
    assert cup._skill_fits_session(skill, signal) is True


def test_two_common_tokens_overlap_not_enough(cup):
    """Previously a pair of common-vocab overlaps (e.g. {markdown, test})
    could pass the bar via the ≥2-token fallback. Under the new policy,
    common-vocab tokens contribute nothing to the threshold — only
    distinctive tokens count toward the ≥2 requirement. This closes a
    back door where any skill whose description happened to share a
    couple of generic dev words with the session would fire."""
    skill = {"id": "update-docs", "short_tip": "Update test docs and run build."}
    signal = {"update", "test", "build", "coach"}
    # Overlap = {update, test, build}; all three are in _COMMON_DEV_VOCAB.
    assert cup._skill_fits_session(skill, signal) is False


def test_python_daemon_session_filters_off_topic_frontend_skill(cup, tmp_path):
    """Reproduces the canonical failure mode: a Python daemon-debugging
    session must NOT surface an off-topic frontend-animation skill as a tip.
    The whole point of this filter."""
    t = tmp_path / "sess.jsonl"
    # User said what they were doing AND ran related commands.
    events = [
        # Recent user message — the strongest domain signal
        {"type": "user", "message": {"role": "user", "content":
            "Fire the restart cycle on the ingest daemon. Need to check the "
            "event journal and the transaction cross-check. Tail daemon.log "
            "over ssh to the worker host."}},
        # Recent tool uses matching the domain
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "ssh worker 'tail -f daemon.log'"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "grep EVENT|TXN journal"}}]}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    signal, anchors = cup._session_signal(t, "/projects/data-pipeline/ingest-worker")
    frontend_anim = {
        "id": "frontend-anim",
        "short_tip": "Animation timelines — position parameter, nesting, playback.",
    }
    assert cup._skill_fits_session(frontend_anim, signal, anchors) is False


def test_session_signal_includes_user_message_tokens(cup, tmp_path):
    """Regression guard — _session_signal must pull tokens from user
    message text, not just tool_uses. Without this, a fresh session with
    few tool calls produces a thin signal and the filter lets through
    irrelevant skills."""
    t = tmp_path / "sess.jsonl"
    t.write_text(json.dumps({
        "type": "user",
        "message": {"role": "user", "content":
            "Please help me debug the ingest daemon's restart "
            "behavior — the journal isn't matching the transaction state."},
    }) + "\n")
    signal, _anchors = cup._session_signal(t, None)
    assert "ingest" in signal
    assert "daemon" in signal
    assert "journal" in signal
    assert "transaction" in signal


def test_session_signal_only_reads_tail_of_long_transcript(cup, tmp_path):
    """Regression guard — _session_signal must read only a bounded tail
    of the transcript, not the whole file. Naively swapping the previous
    fh.readlines() / tail-slice for a deque without dropping the slice
    would crash silently (deques don't slice) and the hook's outer
    try/except would swallow it. This test pins both correctness AND
    tail semantics: 200 stale tool_uses up front MUST be dropped by the
    deque, and 400 fresh tool_uses at the end MUST drive the signal."""
    t = tmp_path / "sess.jsonl"
    lines = []
    # 200 stale tool_uses — these tokens must NOT appear in the signal
    # because the deque(fh, maxlen=max_events*4=400) drops them.
    for _ in range(200):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "markeralpha stale stalelong"}}
            ]},
        }))
    # 400 fresh tool_uses that fit exactly inside the tail window. The
    # tool_use loop caps at max_events=100, which we'll cover via the
    # tail's last 100 entries.
    for _ in range(400):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "markerbeta fresh freshlong"}}
            ]},
        }))
    t.write_text("\n".join(lines) + "\n")
    signal, _anchors = cup._session_signal(t, None)
    assert "markerbeta" in signal, "fresh tail tokens must drive the signal"
    assert "markeralpha" not in signal, (
        "stale tokens beyond the deque's maxlen must NOT leak into signal"
    )


def test_build_tip_pool_filters_skills_by_signal(cup):
    profile = {
        "entries": [],
        "skill_hints": [
            {"id": "frontend-anim", "short_tip": "Scroll-linked animations."},
            {"id": "update-docs",   "short_tip": "Update markdown docs and README."},
        ],
    }
    # Pretend we're editing Python + markdown. Signal overlaps update-docs
    # on two distinctive tokens (`readme`, `markdown`) → passes. Frontend
    # skill has no overlap → filtered.
    signal = {"python3", "pytest", "coach", "bin", "readme", "markdown"}
    pool = cup._build_tip_pool(profile, session_signal=signal)
    skill_ids = {t["entry_id"] for t in pool if t["kind"] == "skill"}
    assert "update-docs" in skill_ids
    assert "frontend-anim" not in skill_ids


def test_build_tip_pool_unfiltered_when_signal_none(cup):
    """Backwards-compatible path: if no signal provided, keep all hints."""
    profile = {
        "entries": [],
        "skill_hints": [
            {"id": "frontend-anim", "short_tip": "Scroll-linked animations."},
        ],
    }
    pool = cup._build_tip_pool(profile, session_signal=None)
    assert {t["entry_id"] for t in pool if t["kind"] == "skill"} == {"frontend-anim"}


def test_behavior_gate_filters_edits_without_testing_after_test(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_transcript(t, [
        {"name": "Edit", "input": {"file_path": "/p/app.py"}},
        {"name": "Bash", "input": {"command": "pytest tests/"}},
    ])
    evidence = cup._session_behavior_evidence(t)
    profile = {"entries": [{
        "id": "edits-without-testing",
        "name": "edits without testing",
        "tier": "active",
        "confidence": 0.9,
        "nudge": "Run tests after edits.",
    }]}
    pool = cup._build_tip_pool(profile, behavior_evidence=evidence)
    assert "edits-without-testing" not in {tip["entry_id"] for tip in pool}


def test_behavior_gate_keeps_edits_without_testing_after_later_edit(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest tests/"}},
        {"name": "Edit", "input": {"file_path": "/p/app.py"}},
    ])
    evidence = cup._session_behavior_evidence(t)
    profile = {"entries": [{
        "id": "edits-without-testing",
        "name": "edits without testing",
        "tier": "active",
        "confidence": 0.9,
        "nudge": "Run tests after edits.",
    }]}
    pool = cup._build_tip_pool(profile, behavior_evidence=evidence)
    assert "edits-without-testing" in {tip["entry_id"] for tip in pool}


def test_completion_detection_uses_shared_collect_only_rule(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_timed_transcript(t, [
        {"name": "Bash", "input": {"command": "pytest --collect-only tests/"}},
        {"name": "Bash", "input": {"command": "mocha"}},
    ])
    fired_at = cup._parse_iso("2026-01-01T00:00:00+00:00")
    assert cup._transcript_matches(t, fired_at, {"action": "test_run"}) is True

    t2 = tmp_path / "collect-only.jsonl"
    _write_timed_transcript(t2, [
        {"name": "Bash", "input": {"command": "pytest --collect-only tests/"}},
    ])
    assert cup._transcript_matches(t2, fired_at, {"action": "test_run"}) is False


def test_completion_detection_handles_dynamic_doc_write(cup, tmp_path):
    t = tmp_path / "sess.jsonl"
    _write_timed_transcript(t, [
        {"name": "Edit", "input": {"file_path": "/p/README.md"}},
    ])
    fired_at = cup._parse_iso("2026-01-01T00:00:00+00:00")
    assert cup._transcript_matches(t, fired_at, {"action": "doc_write"}) is True


def test_common_dev_vocab_contains_skill_catalog_meta_words(cup):
    """Explicit guard: the skill-catalog meta-words added to
    `_COMMON_DEV_VOCAB` after the meta-discussion false-positive bug
    must stay there. Deleting any one of them would silently regress
    the filter — a GSAP-style skill description would again match on a
    single self-referential word like `skill` or `official` during any
    session that happens to mention it (which is every coach-development
    session, for obvious reasons)."""
    for word in ("skill", "skills", "official", "api", "framework",
                 "library", "plugin", "plugins"):
        assert word in cup._COMMON_DEV_VOCAB, (
            f"{word!r} must stay in _COMMON_DEV_VOCAB — it's a skill-"
            f"description meta-word that would otherwise false-positive "
            f"every skill hint in any session mentioning it"
        )


def test_genuine_frontend_work_still_fits_after_meta_vocab_expansion(cup):
    """Pair with `test_meta_discussion_filters_off_topic_skill`: adding
    `skill`/`official`/`plugin` to `_COMMON_DEV_VOCAB` must NOT strand
    legitimate frontend sessions. Id-token matches (e.g. `gsap`) and
    distinctive domain tokens (`scroll`, `animation`, `draggable`) carry
    the relevance signal — the meta-words were never what made the
    filter work in the genuine-work case."""
    # Session signal simulating actual frontend/animation work
    signal = {"gsap", "scroll", "animation", "tween", "draggable",
              "scrolltrigger", "tsx", "components", "pnpm"}
    gsap_plugins = {
        "id": "gsap-plugins",
        "short_tip": "Official GSAP skill for GSAP plugins — registration, "
                     "ScrollToPlugin, ScrollSmoother, Flip, Draggable, "
                     "Inertia, Observer.",
    }
    assert cup._skill_fits_session(gsap_plugins, signal) is True


def test_meta_discussion_filters_off_topic_skill(cup):
    """Regression: a session that's a meta-discussion about Claude Code
    itself — working on the coach, installing skills, debugging hooks —
    must NOT fire off-topic skill hints just because the word 'skill' or
    'official' or 'api' appears in both the session and every skill's
    description. Those are self-referential meta-words, not domain signal.

    Seen in the wild when the canonical `Official GSAP skill for X`
    descriptions kept matching on the word `skill` during sessions where
    the user was literally developing the coach itself (no frontend work
    at all)."""
    # Session is clearly about coach/hook development — no frontend work.
    signal = {"coach", "hook", "install", "skill", "sandbox", "launchd",
              "pytest", "claude", "settings", "python3", "bundle"}

    # A canonical-shape skill description that used to false-positive on
    # the meta-word 'skill' alone.
    gsap_like = {
        "id": "frontend-anim",
        "short_tip": "Official animation skill for the core API — "
                     "scroll-linked animations, pinning, scrub.",
    }
    assert cup._skill_fits_session(gsap_like, signal) is False, (
        "self-referential meta-word overlap ('skill'/'official'/'api') "
        "must not count as distinctive"
    )

    # Sanity: a REAL overlap in the same session (e.g. install-oriented
    # skill) still fits.
    install_skill = {
        "id": "install-helper",
        "short_tip": "Install, configure, and bundle Claude Code hooks "
                     "— settings patching, launchd, sandbox testing.",
    }
    assert cup._skill_fits_session(install_skill, signal) is True


# --- widget/deploy-staging regression + project-anchor route ---------------

def test_widget_session_does_not_fire_deploy_staging(cup, tmp_path):
    """Regression for the cross-project plumbing-token bug: during a
    widget (AI-agents) session doing reconciler + journal work over SSH
    to a remote device, the coach suggested /deploy-staging — a skill
    for a DIFFERENT project (service — an asset deployment pipeline).
    Both projects touched a remote staging device, so the pre-fix
    filter saw one 'distinctive' token overlap (`mobile`) and fired.

    After the fix, `mobile` / `ssh` / `deploy` live in _COMMON_DEV_VOCAB
    (cross-project plumbing, not domain evidence), and a single
    distinctive-token overlap is no longer sufficient on its own. The
    skill must stay filtered here."""
    t = tmp_path / "sess.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content":
            "Let's wire the reconciler to the widget agent journal. "
            "Tail daemon.log on the mobile over ssh and see what the "
            "reconciler is doing with the latest transactions."}},
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "ssh mobile 'tail -f daemon.log'"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Edit",
            "input": {"file_path":
                "/Users/r/Desktop/dev/widget/reconciler/journal.py"}}]}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    signal, anchors = cup._session_signal(t, "/Users/r/Desktop/dev/widget")

    # Description shaped like a real cross-project skill that would
    # share plumbing tokens with the widget session.
    cross_project_skill = {
        "id": "deploy-staging",
        "short_tip": ("Iterate on a deployable artifact. Exports build "
                      "outputs from a content tool, deploys to a staging "
                      "environment, captures screenshots, and compares "
                      "against reference."),
    }
    assert cup._skill_fits_session(cross_project_skill, signal, anchors) is False, (
        "deploy-staging is a service-project skill; sharing the word `mobile` "
        "with a widget session must not be enough to fire it"
    )


def test_project_anchor_shortcut_fires_on_name_match(cup, tmp_path):
    """A skill whose description literally names the current project dir
    should fire easily — that's near-conclusive on-topic evidence, and
    keeps the overall filter from being so strict it silences legitimate
    project-scoped skills. The cwd-derived anchor token is the shortcut."""
    t = tmp_path / "sess.jsonl"
    events = [
        {"message": {"content": [{"type": "tool_use", "name": "Edit",
            "input": {"file_path": "/Users/r/Desktop/dev/widget/agent.py"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "pytest tests/"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Edit",
            "input": {"file_path": "/Users/r/Desktop/dev/widget/README.md"}}]}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    signal, anchors = cup._session_signal(t, "/Users/r/Desktop/dev/widget")
    assert "widget" in anchors

    # Skill explicitly scoped to the widget project — should fire via anchor.
    widget_skill = {
        "id": "widget-build",
        "short_tip": "Perpetual architectural evolution loop for widget.",
    }
    assert cup._skill_fits_session(widget_skill, signal, anchors) is True


def test_single_plumbing_token_not_enough(cup):
    """Explicit guard for the cross-project-plumbing tokens added in
    _COMMON_DEV_VOCAB (mobile, ssh, deploy, iterate, export, …). A skill
    whose only overlap with the session is one of these tokens must not
    fire, because these words span many unrelated projects."""
    signal = {"mobile", "agent", "reconciler", "journal", "widget"}
    skill = {
        "id": "deploy-staging",
        "short_tip": "Deploys to a staging environment and iterates on build outputs.",
    }
    # Overlap = {mobile} (all others in skill are in vocab).
    # No distinctive tokens → False.
    assert cup._skill_fits_session(skill, signal) is False


def test_session_signal_anchors_are_last_cwd_component(cup):
    """The anchor set must be the tokens inside the last cwd path
    component only — not the whole path. This keeps the anchor route
    from matching on ancestor-dir tokens like `desktop` / `dev`, which
    would span every project under that parent."""
    _signal, anchors = cup._session_signal(None, "/Users/r/Desktop/dev/widget")
    assert "widget" in anchors
    # Ancestor components must NOT leak into anchors.
    assert "desktop" not in anchors
    assert "dev" not in anchors


# --- project-scoped gate (SKILL.md frontmatter `projects:` field) -----------

def test_scoped_skill_fires_in_matching_project(cup, tmp_path):
    """A skill declaring ``projects: [widget]`` should fire when the cwd
    anchor resolves to `widget`, as long as there's *some* topic overlap
    with the session — declaring project scope doesn't make the skill
    fire on every turn inside that project."""
    t = tmp_path / "sess.jsonl"
    t.write_text(json.dumps({
        "type": "user",
        "message": {"role": "user", "content":
            "Let's run the evolution loop and swarm some research agents "
            "across the reference codebases."},
    }) + "\n")
    signal, anchors = cup._session_signal(t, "/Users/r/Desktop/dev/widget")
    skill = {
        "id": "widget-build",
        "short_tip": "Perpetual architectural evolution loop. Swarms research "
                     "agents across reference codebases and debates findings.",
        "projects": ["widget"],
    }
    assert cup._skill_fits_session(skill, signal, anchors) is True


def test_scoped_skill_filtered_in_non_matching_project(cup, tmp_path):
    """The whole point: a skill declared for `service` must not fire in
    `widget`, even if the session happens to share some plumbing tokens
    with the skill's description. Hard filter, no token-math override."""
    t = tmp_path / "sess.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content":
            "Ship the reconciler over ssh to the mobile, tail daemon.log, "
            "compare journal events against the transaction feed."}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    signal, anchors = cup._session_signal(t, "/Users/r/Desktop/dev/widget")
    scoped_skill = {
        "id": "deploy-staging",
        "short_tip": "Iterate on a deployable artifact. Exports build outputs "
                     "from a content tool, deploys to a staging environment, "
                     "captures screenshots, compares references.",
        "projects": ["service"],
    }
    assert cup._skill_fits_session(scoped_skill, signal, anchors) is False


def test_scoped_skill_filtered_when_cwd_unknown(cup):
    """Conservative behavior: if the hook has no anchor (fresh session,
    no cwd in payload, etc.) a project-scoped skill cannot be evaluated
    safely. Default to SKIP — this matches the coach's stated principle
    ('default to SKIP when uncertain')."""
    signal = {"some", "session", "tokens", "here"}
    scoped = {
        "id": "deploy-staging",
        "short_tip": "Asset pipeline work.",
        "projects": ["service"],
    }
    assert cup._skill_fits_session(scoped, signal, frozenset()) is False


def test_scoped_skill_handles_compound_project_name(cup, tmp_path):
    """`projects: [acme-cli]` tokenizes to {acme, cli}.
    A cwd of `~/Desktop/dev/acme-app` anchors to {acme, app}.
    The intersection is {acme} → the skill is considered in-project.
    This is the aliasing pathway for repos that share a prefix."""
    t = tmp_path / "sess.jsonl"
    t.write_text(json.dumps({
        "type": "user",
        "message": {"role": "user", "content":
            "Migrate the recon module from cli to app's src-tauri."},
    }) + "\n")
    signal, anchors = cup._session_signal(
        t, "/Users/r/Desktop/dev/acme-app")
    skill = {
        "id": "cli-migrate",
        "short_tip": "Migrate acme-cli Python modules to Rust in "
                     "acme-app's src-tauri.",
        "projects": ["acme-cli", "acme-app"],
    }
    assert cup._skill_fits_session(skill, signal, anchors) is True


def test_scoped_skill_still_needs_topic_overlap(cup, tmp_path):
    """In-project scope is necessary but not sufficient. If the session
    in a `widget` cwd is doing something totally unrelated to the
    skill's topic (e.g. editing the README while the skill is about
    evolution loops), the skill should not fire."""
    t = tmp_path / "sess.jsonl"
    events = [
        {"message": {"content": [{"type": "tool_use", "name": "Edit",
            "input": {"file_path":
                "/Users/r/Desktop/dev/widget/LICENSE"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "git status"}}]}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    signal, anchors = cup._session_signal(t, "/Users/r/Desktop/dev/widget")
    skill = {
        "id": "widget-build",
        "short_tip": "Perpetual architectural evolution loop swarming agents "
                     "across reference codebases.",
        "projects": ["widget"],
    }
    # No overlap between session tokens and the evolve/swarm/reference
    # vocabulary → skipped despite being in-project.
    assert cup._skill_fits_session(skill, signal, anchors) is False


def test_untagged_skill_still_uses_prior_overlap_rules(cup):
    """Adding the project-scoped gate must not change behavior for skills
    that don't declare `projects`. The existing ≥2-distinctive rule
    continues to apply — proving the gate is additive, not replacement."""
    skill_untagged = {
        "id": "frontend-anim",
        "short_tip": "Scroll-linked animations and pinning.",
        # deliberately no `projects` field
    }
    signal = {"scroll", "animations", "hero", "pnpm"}
    assert cup._skill_fits_session(skill_untagged, signal) is True

    skill_untagged_single_hit = {
        "id": "frontend-anim",
        "short_tip": "Scroll-linked animations.",
    }
    # Single-distinctive-token (scroll) remains insufficient (the
    # 2026-04-24 tightening), regardless of the new project gate.
    assert cup._skill_fits_session(
        skill_untagged_single_hit, {"scroll", "backend", "daemon"}) is False


# --- git-root anchor walk --------------------------------------------------

def test_git_root_anchor_from_subdirectory_cwd(cup, tmp_path):
    """Regression guard for the subdirectory-cwd case: if the user is
    working inside a monorepo package (e.g.
    ``~/Desktop/dev/widget/packages/core``), the last-component anchor
    becomes {core, packages} and misses the project name. The git-root
    walk recovers it by finding the nearest ``.git/`` ancestor."""
    repo = tmp_path / "widget"
    subdir = repo / "packages" / "core"
    subdir.mkdir(parents=True)
    (repo / ".git").mkdir()   # fake git root
    _signal, anchors = cup._session_signal(None, str(subdir))
    assert "widget" in anchors, (
        "git-root walk must recover the project name when cwd is a "
        "subdirectory deeper than parts[-1]"
    )


def test_git_root_anchor_returns_none_outside_any_repo(cup, tmp_path):
    """If there's no `.git/` anywhere in the ancestor chain, the walk
    returns None silently and anchors fall back to the last-component
    tokenization only. Must not crash."""
    loose = tmp_path / "random" / "nested" / "dirs"
    loose.mkdir(parents=True)
    # Lock in BOTH the function-level contract (returns None) AND the
    # signal-level effect (only last-component tokens contribute) —
    # the prior version of this test only checked the latter, so a
    # bug returning some other ancestor would still pass.
    assert cup._find_git_root_name(str(loose)) is None
    _signal, anchors = cup._session_signal(None, str(loose))
    assert "dirs" in anchors


def test_git_root_walk_skips_home_repo(cup, tmp_path, monkeypatch):
    """Regression for review-finding #4 (2026-04-24): if $HOME itself
    is a git repo (e.g., a dotfiles checkout), the walk would
    previously anchor every non-nested-repo cwd to the username,
    which then false-positives past the project filter for any skill
    description containing that token.

    Fix: a `.git` found exactly at $HOME is ignored; the walk
    continues past it. With no DIFFERENT ancestor repo, the function
    returns None — matching the no-repo case."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".git").mkdir()                # home IS a git repo
    inner = fake_home / "Desktop" / "scratch"
    inner.mkdir(parents=True)
    monkeypatch.setattr(cup.Path, "home", classmethod(lambda cls: fake_home))

    # The home `.git` must be skipped — no nested repo exists, so
    # the walk hits filesystem root and returns None.
    assert cup._find_git_root_name(str(inner)) is None


def test_git_root_walk_finds_nested_repo_even_with_home_repo(
        cup, tmp_path, monkeypatch):
    """Pair with the home-skip test: when a nested repo DOES exist
    inside the home-rooted repo, it should still be found. The
    home-skip rule must not block legitimate inner-repo detection."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".git").mkdir()                # home IS a git repo
    project = fake_home / "Desktop" / "dev" / "widget"
    project.mkdir(parents=True)
    (project / ".git").mkdir()                  # nested repo exists
    inner = project / "packages" / "core"
    inner.mkdir(parents=True)
    monkeypatch.setattr(cup.Path, "home", classmethod(lambda cls: fake_home))

    # Walk must find the widget repo before reaching the home-skip case.
    assert cup._find_git_root_name(str(inner)) == "widget"


# --- COACH_ALL_SKILLS escape hatch -----------------------------------------

def test_coach_all_skills_env_bypasses_filter(cup, monkeypatch):
    """Setting COACH_ALL_SKILLS=1 must disable skill filtering in
    _build_tip_pool so all hints become eligible. Insurance escape
    hatch for false-filter misfires in the wild."""
    monkeypatch.setenv("COACH_ALL_SKILLS", "1")
    profile = {
        "entries": [],
        "skill_hints": [
            # Project-scoped to service, but should still fire under bypass.
            {"id": "deploy-staging",
             "short_tip": "Asset pipeline work.",
             "projects": ["service"]},
            # Would also be filtered for thin overlap under normal rules.
            {"id": "frontend-anim",
             "short_tip": "Scroll-linked animations."},
        ],
    }
    # A cwd that anchors to widget — deploy-staging would normally be
    # hard-filtered by the scoped gate.
    signal = {"reconciler", "agent", "journal"}
    anchors = {"widget"}
    pool = cup._build_tip_pool(
        profile, session_signal=signal, project_anchors=anchors)
    skill_ids = {t["entry_id"] for t in pool if t["kind"] == "skill"}
    assert "deploy-staging" in skill_ids
    assert "frontend-anim" in skill_ids


def test_coach_all_skills_off_still_filters(cup, monkeypatch):
    """Sanity counterpart: without the env var, the scoped gate is
    active. Same profile + same signal = deploy-staging filtered out."""
    monkeypatch.delenv("COACH_ALL_SKILLS", raising=False)
    profile = {
        "entries": [],
        "skill_hints": [
            {"id": "deploy-staging",
             "short_tip": "Asset pipeline work.",
             "projects": ["service"]},
        ],
    }
    signal = {"reconciler", "agent", "journal"}
    anchors = {"widget"}
    pool = cup._build_tip_pool(
        profile, session_signal=signal, project_anchors=anchors)
    skill_ids = {t["entry_id"] for t in pool if t["kind"] == "skill"}
    assert "deploy-staging" not in skill_ids


# --- branding regression: Coach Claw persona + reward marker ----------------
# The coach surfaces user-facing strings that drift if labels or banner
# emoji change accidentally. These tests pin the contract so a typo or
# refactor can't silently revert the rebrand.

def test_label_pools_have_no_screwdriver(cup):
    assert not any("🪛" in label for label in cup.WEAKNESS_LABELS)
    assert not any("🪛" in label for label in cup.SKILL_LABELS)
    assert not any("🪛" in label for label in cup.STRENGTH_LABELS)


def test_skill_labels_use_coach_claw(cup):
    assert "*🦞 From Coach Claw:*" in cup.SKILL_LABELS
    assert "*🦞 Coach:*" in cup.SKILL_LABELS
    assert not any("From your coach" in label for label in cup.SKILL_LABELS)
    # 🧭 was the old direction-flavored Coach variant — replaced by 🦞 so
    # every emoji-decorated SKILL label carries the Coach Claw persona.
    assert not any("🧭" in label for label in cup.SKILL_LABELS)


def test_xp_attribution_uses_arrow_marker(cup):
    skill_lines = cup._xp_attribution(
        {"kind": "skill", "entry_id": "deploy-to-vercel", "clean_streak_runs": 0}
    )
    assert isinstance(skill_lines, list) and len(skill_lines) == 1
    assert skill_lines[0].startswith("_↑ +")
    assert "XP" not in skill_lines[0]
    assert "✨" not in skill_lines[0]

    weakness_lines = cup._xp_attribution({
        "kind": "weakness",
        "entry_id": "edits-without-testing",
        "clean_streak": 2,
        "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"},
    })
    # Streak bar must live on its own line so it never wraps inside the
    # per-action reward sentence.
    assert len(weakness_lines) == 2
    assert weakness_lines[0].startswith("_↑ +2 per test run")
    assert "XP" not in weakness_lines[0]
    assert "🔥" not in weakness_lines[0]
    assert weakness_lines[1].startswith("_🌡️ warming up")
    assert "2/5" in weakness_lines[1]
    assert all("✨" not in line for line in weakness_lines)


@pytest.mark.parametrize(
    ("streak", "expected"),
    [
        (2, "_🌡️ warming up 🔴🔴⚪⚪⚪ 2/5 → +5 bonus at 5/5._"),
        (3, "_🌶️ heating up 🔴🔴🔴⚪⚪ 3/5 → +5 bonus at 5/5._"),
        (4, "_🔥 streak 🔴🔴🔴🔴⚪ 4/5 → +5 bonus at 5/5._"),
        (5, "_🏆 mastered 🔴🔴🔴🔴🔴 5/5 → +5 bonus ready._"),
    ],
)
def test_weakness_streak_stage_ladder(cup, streak, expected):
    lines = cup._xp_attribution({"kind": "weakness", "clean_streak": streak})
    assert lines == [expected]


@pytest.mark.parametrize(
    ("streak", "expected"),
    [
        (2, "_🌡️ warming up 🔴🔴⚪⚪⚪ 2/5 → +5 mastery bonus at 5/5._"),
        (3, "_🌶️ heating up 🔴🔴🔴⚪⚪ 3/5 → +5 mastery bonus at 5/5._"),
        (4, "_🔥 streak 🔴🔴🔴🔴⚪ 4/5 → +5 mastery bonus at 5/5._"),
        (5, "_🏆 mastered 🔴🔴🔴🔴🔴 5/5 → +5 mastery bonus ready._"),
    ],
)
def test_strength_streak_stage_ladder(cup, streak, expected):
    lines = cup._xp_attribution({"kind": "strength", "positive_streak": streak})
    assert lines == [expected]


def test_strength_tips_have_distinct_runtime_treatment(cup):
    assert "*Strength:*" in cup.STRENGTH_LABELS
    assert not set(cup.STRENGTH_LABELS) & set(cup.WEAKNESS_LABELS)

    lines = cup._xp_attribution({
        "kind": "strength",
        "entry_id": "tests-after-edits",
        "clean_streak": 0,
        "positive_streak": 3,
        "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"},
    })
    assert len(lines) == 2
    assert lines[0].startswith("_↑ +2 per test run")
    assert "XP" not in lines[0]
    assert "🔥" not in lines[0]
    assert "🌶️ heating up" in lines[1]
    assert "3/5" in lines[1]

    spec = cup._completion_spec({
        "kind": "strength",
        "entry_id": "tests-after-edits",
        "reward_hint": {"action": "test_run", "xp": 2, "description": "test run"},
    })
    assert spec == {"action": "test_run", "xp": 2, "description": "test run"}


def test_strength_completion_banner_reinforces_instead_of_clearing(cup):
    block = cup._completion_banner([
        ("entry:tests-after-edits", {
            "kind": "strength",
            "entry_id": "tests-after-edits",
            "positive_streak": 2,
            "spec": {"action": "test_run", "xp": 2, "description": "test run"},
        })
    ])
    assert "> 💪 Strength reinforced — test runner detected" in block
    assert "> +2 XP · tests-after-edits strength streak 🔴🔴⚪⚪⚪" in block
    assert "advances on next /coach-insights run" not in block


def test_strength_session_cap_helpers(cup):
    state = {}
    now = cup.datetime(2026, 1, 1, tzinfo=cup.timezone.utc)

    assert cup._session_strength_already_fired(state, "s1") is False
    cup._mark_strength_fired(state, "s1", now)
    assert cup._session_strength_already_fired(state, "s1") is True


def test_tip_log_records_redacted_bounded_events(cup, tmp_path, monkeypatch):
    log_path = tmp_path / "log.ndjson"
    monkeypatch.setattr(cup, "LOG_PATH", log_path)
    monkeypatch.setattr(cup, "LOG_MAX_LINES", 2)
    now = cup.datetime(2026, 1, 1, tzinfo=cup.timezone.utc)

    cup._log_tip_fired(
        {
            "id": "entry:edits-without-testing",
            "entry_id": "edits-without-testing",
            "kind": "weakness",
            "tier": "active",
            "nudge": "raw nudge text should never be logged",
            "example": "raw transcript example should never be logged",
        },
        {"action": "test_run", "xp": 2, "description": "pytest tests/unit"},
        now,
    )
    cup._log_tip_completed(
        "entry:edits-without-testing",
        {
            "entry_id": "edits-without-testing",
            "kind": "weakness",
            "spec": {"action": "test_run", "xp": 2, "description": "pytest tests/unit"},
        },
        now,
    )
    cup._log_tip_fired(
        {
            "id": "skill:update-docs",
            "entry_id": "update-docs",
            "kind": "skill",
            "tier": "hint",
        },
        {"action": "skill_invoke", "skill_id": "update-docs"},
        now,
    )

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [record["event"] for record in records] == ["tip_completed", "tip_fired"]
    assert records[0]["action"] == "test_run"
    assert records[0]["xp"] == 2
    assert records[1]["skill_id"] == "update-docs"

    raw = log_path.read_text()
    assert "raw nudge" not in raw
    assert "raw transcript" not in raw
    assert "pytest tests/unit" not in raw


def test_celebration_banners_use_canonical_glyphs(cup):
    # Streak rewards: directional arrows (positive→↑, negative→↓), no ✨ leakage.
    streak_neg = cup._streak_reward_block(
        [{"id": "x", "name": "x", "streak": 2, "target": 5,
          "xp_awarded": 1, "direction": "negative"}]
    )
    assert "↓" in streak_neg and "✨" not in streak_neg
    streak_pos = cup._streak_reward_block(
        [{"id": "y", "name": "y", "streak": 2, "target": 5,
          "xp_awarded": 1, "direction": "positive"}]
    )
    assert "↑" in streak_pos and "✨" not in streak_pos

    # Graduations: 🎓⚡️ (negative) / 🎓🌟 (positive) ceremonial pair preserved.
    grad = cup._graduation_block(
        [{"id": "x", "name": "x", "direction": "negative",
          "graduated_reason": "5 clean runs"}]
    )
    assert "🎓⚡️" in grad and "🎓✨" not in grad
