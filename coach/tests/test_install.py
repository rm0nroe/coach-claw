from __future__ import annotations

import json
import os
import pty
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


def _git_env() -> dict:
    return {
        "GIT_AUTHOR_NAME": "Coach Tests",
        "GIT_AUTHOR_EMAIL": "coach-tests@example.invalid",
        "GIT_COMMITTER_NAME": "Coach Tests",
        "GIT_COMMITTER_EMAIL": "coach-tests@example.invalid",
    }


def _run_install(
    repo: Path,
    claude_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({"CLAUDE_DIR": str(claude_dir), **_git_env()})
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(repo / "install.sh"), *(args or [])],
        cwd=repo,
        env=env,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def _run_install_with_pty(
    repo: Path,
    claude_dir: Path,
    reply: str,
    *,
    extra_env: dict[str, str] | None = None,
    args: list[str] | None = None,
    fake_home: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run install.sh with a real pty as stdin and pre-stuff `reply`.

    Pre-writing the reply to the master side before the child starts is
    the standard expect-style pattern: bash's `read -r` reads from the
    pty buffer when it gets to that line, regardless of which side
    wrote first. Robust against the install.sh startup time variance
    that a sleep-then-write approach would race on.

    `fake_home` overrides `$HOME` so the test controls whether
    `$HOME/.claude/projects` exists for the prompt gate; without this
    the child reads the developer's real ~/.claude/projects.
    """
    master, slave = pty.openpty()
    master_closed = False
    try:
        env = os.environ.copy()
        env.update({"CLAUDE_DIR": str(claude_dir), **_git_env()})
        if fake_home is not None:
            env["HOME"] = str(fake_home)
        if extra_env:
            env.update(extra_env)
        os.write(master, reply.encode())
        try:
            proc = subprocess.Popen(
                ["bash", str(repo / "install.sh"), *(args or [])],
                cwd=repo,
                env=env,
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        finally:
            os.close(slave)
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            os.close(master)
            master_closed = True
            raise
        os.close(master)
        master_closed = True
        return subprocess.CompletedProcess(
            proc.args, proc.returncode, out, err
        )
    finally:
        if not master_closed:
            os.close(master)


def test_install_uses_custom_claude_dir_in_generated_commands(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir With Spaces"
    python3 = shutil.which("python3")
    assert python3

    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    settings = json.loads((claude_dir / "settings.json").read_text())
    py_cmd = shlex.quote(python3)

    session_start = settings["hooks"]["SessionStart"][0]["hooks"][0]
    assert session_start["command"] == (
        f"{py_cmd} {shlex.quote(str(claude_dir / 'hooks/coach-session-start.py'))}"
    )

    user_prompt = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert user_prompt["command"] == (
        f"{py_cmd} {shlex.quote(str(claude_dir / 'hooks/coach-user-prompt.py'))}"
    )

    # v0.3.0+: statusLine points at the rich shell wrapper that composes
    # model + context-bar + coach segment. The wrapper itself contains
    # the resolved python path (sed-substituted from @PY@ at install
    # time) so the install command line is just `bash <wrapper>`.
    statusline_path = claude_dir / "coach/default-statusline-command.sh"
    assert settings["statusLine"]["command"] == (
        f"bash {shlex.quote(str(statusline_path))}"
    )
    assert statusline_path.exists()
    wrapper = statusline_path.read_text()
    assert "@PY@" not in wrapper, "installer should have substituted @PY@"
    assert python3 in wrapper, "installer should have written the python path"


def test_install_preserves_user_config_json(tmp_path: Path) -> None:
    """Re-installing must NOT reset the user's /config choices.

    Pre-create `.user_config.json` with non-default values, run the
    installer twice (the second run is the upgrade path that exercises
    the preserve-and-restore loop), and assert the file content is
    byte-identical to what the user had before.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"

    # Fresh install first — sets up coach/ so the second install hits
    # the preserve path at install.sh:86-103.
    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    user_cfg_path = claude_dir / "coach/.user_config.json"
    payload = {
        "schema_version": 1,
        "statusline_variant": "forge",
        "theme": "skyrim",
        "elo_min": 500,
        "elo_max": 3000,
    }
    user_cfg_path.write_text(json.dumps(payload, indent=2))
    pre = user_cfg_path.read_text()

    # Re-install — this exercises the preserve-and-restore loop.
    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    assert user_cfg_path.exists(), (
        ".user_config.json was dropped by reinstall — preserve list at "
        "install.sh:91 is missing the entry."
    )
    post = user_cfg_path.read_text()
    assert post == pre, (
        "reinstall mutated .user_config.json:\n"
        f"pre:  {pre!r}\n"
        f"post: {post!r}"
    )
    assert json.loads(post) == payload


def test_install_uses_mktemp_for_preserve_dir(tmp_path: Path) -> None:
    """Upgrade preservation must use a private randomized temp directory.

    A predictable `/tmp/coach-preserve.$TS` directory is race/symlink-prone
    on multi-user machines because it temporarily holds user-owned state such
    as profile.yaml, changelog.md, log.ndjson, and pending markers.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    preserve_parent = tmp_path / "preserve parent"
    preserve_parent.mkdir()
    second = _run_install(
        repo,
        claude_dir,
        extra_env={"TMPDIR": str(preserve_parent)},
    )
    assert second.returncode == 0, second.stdout + second.stderr

    match = re.search(r"preserved existing coach state → (.+)", second.stdout)
    assert match, second.stdout
    preserve_dir = Path(match.group(1).strip())
    assert preserve_dir.parent == preserve_parent
    assert re.fullmatch(r"coach-preserve\.[A-Za-z0-9._-]+", preserve_dir.name)
    assert preserve_dir.name != "coach-preserve.$TS"
    assert not preserve_dir.exists(), "preserve dir should be removed after restore"


def test_install_preserves_per_run_git_history(tmp_path: Path) -> None:
    """Re-installing must NOT reset ~/.claude/coach/.git/ to a single bootstrap commit.

    CLAUDE.md treats the per-run git log as authoritative for profile
    history; the documented rollback UX is
    `git -C ~/.claude/coach checkout HEAD~1 -- profile.yaml`. Pre-fix,
    install.sh's preserve loop covered state files but moved the whole
    coach/ dir (including .git/) aside, so every upgrade produced a
    fresh `git init` and the rollback chain was silently broken.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"

    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    coach_dir = claude_dir / "coach"
    assert (coach_dir / ".git").is_dir(), "fresh install did not git init coach/"

    # Simulate a /coach-insights run committing a profile mutation.
    (coach_dir / "profile.yaml").write_text(
        "schema_version: 1\nentries:\n  - id: test-fake-pattern\n    type: weakness\n"
    )
    git_env = {**os.environ, **_git_env()}
    subprocess.run(["git", "add", "-A"], cwd=coach_dir, env=git_env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "insights-test-fake: simulated /coach-insights commit"],
        cwd=coach_dir, env=git_env, check=True,
    )
    pre_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coach_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Upgrade install — must restore .git/ from coach.bak.<ts>/.
    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    assert (coach_dir / ".git").is_dir(), "upgrade install dropped .git/"
    post_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coach_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert post_sha == pre_sha, (
        "upgrade install reset coach/.git/ — documented rollback UX broken.\n"
        f"pre-upgrade HEAD:  {pre_sha}\n"
        f"post-upgrade HEAD: {post_sha}\n"
        "Fix: install.sh must cp -R coach.bak.<ts>/.git into the new coach/."
    )


def test_default_statusline_runs_without_jq(tmp_path: Path) -> None:
    """The installed default statusline must render without jq on PATH.

    macOS does not ship jq, so the v0.3.0 bash wrapper emitted
    `jq: command not found` and rendered an empty model + 0%. This
    test pins the parity contract for the Python replacement: model
    name normalization, percent rounding, 20-segment bar, separator,
    and absence of any `command not found` noise.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    wrapper = claude_dir / "coach/default-statusline-command.sh"
    assert wrapper.exists()

    # Build a sandbox PATH that contains only bash + python3 (and their
    # transitive shell deps), explicitly NOT jq. Symlinking known-good
    # binaries lets us test "what if jq isn't installed" without relying
    # on /usr/bin (which on this user's machine actually has jq).
    sandbox_bin = tmp_path / "sandbox-bin"
    sandbox_bin.mkdir()
    bash_path = shutil.which("bash")
    py_path = shutil.which("python3")
    assert bash_path and py_path
    (sandbox_bin / "bash").symlink_to(bash_path)
    (sandbox_bin / "python3").symlink_to(py_path)
    # Defensive sanity check: nothing named jq in the sandbox.
    assert not (sandbox_bin / "jq").exists()

    payload = (
        '{"model":{"display_name":"Sonnet 4.6"},'
        '"context_window":{"used_percentage":42.7}}'
    )
    proc = subprocess.run(
        [bash_path, str(wrapper)],
        env={"PATH": str(sandbox_bin), "HOME": os.environ.get("HOME", "")},
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert proc.returncode == 0, (
        f"wrapper exited {proc.returncode}\n"
        f"stdout={proc.stdout!r}\n"
        f"stderr={proc.stderr!r}"
    )

    out = proc.stdout
    # Strip ANSI for content assertions; keep raw `out` for separator/glyph counts.
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    plain = ansi.sub("", out)

    assert "sonnet·4.6" in plain, f"model normalization broke: {plain!r}"
    assert "43%" in plain, f"percent round-half-up broke (42.7 → 43): {plain!r}"
    assert plain.count("┃") == 2, f"expected 2 ┃ separators: {plain!r}"
    bar_segments = plain.count("▰") + plain.count("▱")
    assert bar_segments == 20, (
        f"expected 20 bar segments (▰+▱), got {bar_segments}: {plain!r}"
    )

    # Stderr must not contain jq error noise. stdout must not contain
    # the bash 'command not found' fallback message.
    assert "command not found" not in proc.stderr, proc.stderr
    assert "jq" not in proc.stderr.lower(), proc.stderr
    assert "command not found" not in out, out


def test_install_creates_coach_insights_skill_directory(tmp_path: Path) -> None:
    """Fresh install must put the skill at skills/coach-insights/ and must
    NOT install a fresh skills/insights/ shadow over Claude Code's built-in
    /insights command."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    skill_md = claude_dir / "skills/coach-insights/SKILL.md"
    assert skill_md.exists(), (
        "v0.4.0 installer must create skills/coach-insights/SKILL.md "
        "(was skills/insights/ in v0.3.x)"
    )
    body = skill_md.read_text()
    assert "/coach-insights" in body, "skill body should describe its new name"
    assert "disable-model-invocation: true" in body, (
        "frontmatter must opt out of implicit model invocation — the skill "
        "mutates profile state and creates git commits"
    )

    legacy = claude_dir / "skills/insights"
    assert not legacy.exists(), (
        f"fresh install must NOT create {legacy} — that path is reserved "
        "for Claude Code's built-in /insights, which our skill used to shadow"
    )

    # v0.5.0: SKILL.md is a thin wrapper around insights-llm.sh --force,
    # not a full prose translator. The presence of `insights-llm.sh` in
    # the body and the absence of the v0.4.0 prose-translation steps is
    # the contract.
    assert "insights-llm.sh" in body, (
        "v0.5.0 SKILL.md must reference the insights-llm.sh wrapper"
    )


def test_install_creates_aggregate_facets_script(tmp_path: Path) -> None:
    """v0.5.0 installer must ship coach/bin/aggregate_facets.py executable."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    script = claude_dir / "coach/bin/aggregate_facets.py"
    assert script.exists(), "aggregate_facets.py was not installed"
    assert os.access(script, os.X_OK), "aggregate_facets.py is not executable"


def test_install_creates_insights_llm_script(tmp_path: Path) -> None:
    """v0.5.0 installer must ship coach/bin/insights-llm.sh executable."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    script = claude_dir / "coach/bin/insights-llm.sh"
    assert script.exists(), "insights-llm.sh was not installed"
    assert os.access(script, os.X_OK), "insights-llm.sh is not executable"


def test_install_creates_run_with_lock_helper(tmp_path: Path) -> None:
    """v0.5.0 installer must ship coach/bin/run_with_lock.py — the
    flock helper that serializes concurrent weekly-insights runs.
    Without it, insights-llm.sh's `exec ... run_with_lock.py ...`
    fails immediately and the wrapper crashes on every invocation."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    script = claude_dir / "coach/bin/run_with_lock.py"
    assert script.exists(), "run_with_lock.py was not installed"
    assert os.access(script, os.X_OK), "run_with_lock.py is not executable"


def test_install_preserves_last_weekly_insights_marker(tmp_path: Path) -> None:
    """Re-installing must NOT reset the weekly throttle marker — otherwise
    upgrades cause an immediate weekly run on the next session start."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    marker = claude_dir / "coach/.last_weekly_insights"
    marker.write_text("")
    pre_mtime = marker.stat().st_mtime

    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    assert marker.exists(), (
        ".last_weekly_insights was dropped by reinstall — preserve list at "
        "install.sh:91-95 is missing it."
    )
    post_mtime = marker.stat().st_mtime
    assert post_mtime == pre_mtime, (
        f"reinstall mutated marker mtime: pre={pre_mtime} post={post_mtime}"
    )


def test_install_migrates_legacy_insights_skill(tmp_path: Path) -> None:
    """Upgrading from v0.3.x: any pre-existing skills/insights/ must be
    moved aside (mv → .bak.<ts>, not deleted) so the user keeps any
    customizations and the built-in /insights becomes reachable again."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    legacy_skill_dir = claude_dir / "skills/insights"
    legacy_skill_dir.mkdir(parents=True)
    sentinel = legacy_skill_dir / "SKILL.md"
    sentinel_text = "---\ndescription: legacy v0.3.x skill\n---\nLegacy body — must survive the migration.\n"
    sentinel.write_text(sentinel_text)

    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    assert not legacy_skill_dir.exists(), (
        "installer must move skills/insights/ aside, not leave it in place "
        "(it would continue to shadow Claude Code's built-in /insights)"
    )

    bak_dirs = sorted((claude_dir / "skills").glob("insights.bak.*"))
    assert len(bak_dirs) == 1, (
        f"expected exactly one skills/insights.bak.<ts>/ sibling, got "
        f"{[p.name for p in bak_dirs]}"
    )
    # Claude Code's skill loader picks up any skills/<dir>/SKILL.md as a
    # slash command. The migration must rename the legacy SKILL.md inside
    # the bak dir so the loader doesn't surface it as `/insights.bak.<ts>`
    # — the whole point of the migration is to UN-shadow the built-in.
    bak_dir = bak_dirs[0]
    assert not (bak_dir / "SKILL.md").exists(), (
        f"{bak_dir}/SKILL.md must NOT exist post-install — Claude Code "
        f"would surface it as /insights.bak.<ts> and clutter the slash-"
        f"command catalog"
    )
    bak_skill_md = bak_dir / "SKILL.md.bak"
    assert bak_skill_md.exists(), (
        "legacy SKILL.md content must survive — installer should rename "
        "to SKILL.md.bak (preserves customizations, defangs the loader)"
    )
    assert bak_skill_md.read_text() == sentinel_text, (
        "user's customized SKILL.md content must be preserved byte-for-byte"
    )

    new_skill = claude_dir / "skills/coach-insights/SKILL.md"
    assert new_skill.exists(), "v0.4.0 skill must be installed alongside"
    assert "Legacy body" not in new_skill.read_text(), (
        "v0.4.0 skill must NOT inherit content from the legacy skill"
    )


def test_install_under_temp_home(tmp_path: Path) -> None:
    """Sandbox the entire HOME, not just CLAUDE_DIR. This catches any
    `$HOME/...` literal in install.sh that escaped the CLAUDE_DIR
    abstraction (which would silently leak into the developer's real
    HOME during prior test runs)."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    env = os.environ.copy()
    env.update({
        "HOME": str(fake_home),
        # Don't set CLAUDE_DIR — let install.sh derive it from HOME.
        **_git_env(),
    })
    # Drop any inherited CLAUDE_DIR from the developer's environment.
    env.pop("CLAUDE_DIR", None)

    result = subprocess.run(
        ["bash", str(repo / "install.sh")],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    derived_target = fake_home / ".claude"
    assert derived_target.is_dir(), (
        "install.sh derived its target from HOME but did not create "
        f"{derived_target}; either HOME respect broke or the installer "
        "leaked into the real $HOME"
    )
    assert (derived_target / "coach/profile.yaml").exists()
    assert (derived_target / "skills/coach-insights/SKILL.md").exists()
    assert (derived_target / "settings.json").exists()


def test_install_skips_bak_on_byte_identical_files(tmp_path: Path) -> None:
    """Re-running install on an unchanged install produces NO redundant .bak.<ts>.

    Pre-fix, every reinstall created `hooks/<hook>.bak.<ts>` and
    `settings.json.bak.<ts>` even when content was byte-identical. Now
    those backups only appear on real diffs (P2-1 from v0.5.2 audit).
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"

    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    hook_baks = sorted((claude_dir / "hooks").glob("*.bak.*"))
    assert hook_baks == [], (
        f"byte-identical reinstall left hook backups: {hook_baks}\n"
        "Fix: install.sh hook backup loop must gate on `cmp -s` between "
        "bundle and live copy."
    )

    settings_baks = sorted(claude_dir.glob("settings.json.bak.*"))
    assert settings_baks == [], (
        f"byte-identical reinstall left settings.json backups: {settings_baks}\n"
        "Fix: post-patch cleanup must rm the snapshot when cmp -s shows no diff."
    )

    # Note: coach.bak.<ts>/ IS expected (intentional — user state always
    # differs from the bundle). The --prune-backups flag handles long-term
    # accumulation.


def test_install_default_prunes_backups_keeps_three_most_recent(tmp_path: Path) -> None:
    """Default install keeps the 3 most recent .bak.<ts> of each kind.

    v0.5.2 flipped the default to prune-on so ~/.claude/ doesn't
    accumulate hundreds of backups across upgrades. `--no-prune-backups`
    opts out (covered by `test_install_no_prune_backups_keeps_all`).

    `--fresh` is set so the recovery-from-prior-bak block doesn't
    `mv` the most-recent fake .bak back to `coach/` and skew counts.
    """
    import time
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    (claude_dir / "hooks").mkdir()

    for i in range(5):
        d = claude_dir / f"coach.bak.20260101-12000{i}"
        d.mkdir()
        (d / "marker").write_text(f"backup-{i}")
        f = claude_dir / f"settings.json.bak.20260101-12000{i}"
        f.write_text(f'{{"backup": {i}}}')
        for hook in ("coach-session-start.py", "coach-user-prompt.py"):
            (claude_dir / "hooks" / f"{hook}.bak.20260101-12000{i}").write_text(
                f"# bak {i}"
            )
        time.sleep(0.02)

    result = _run_install(repo, claude_dir, args=["--fresh"])
    assert result.returncode == 0, result.stdout + result.stderr

    coach_baks = sorted(p.name for p in claude_dir.glob("coach.bak.20260101-*"))
    settings_baks = sorted(p.name for p in claude_dir.glob("settings.json.bak.20260101-*"))
    assert len(coach_baks) == 3, f"expected 3 coach.bak.*, got {coach_baks}"
    assert len(settings_baks) == 3, f"expected 3 settings.json.bak.*, got {settings_baks}"

    for hook in ("coach-session-start.py", "coach-user-prompt.py"):
        baks = sorted(p.name for p in (claude_dir / "hooks").glob(f"{hook}.bak.20260101-*"))
        assert len(baks) == 3, f"expected 3 {hook}.bak.*, got {baks}"

    surviving_suffixes = {p.split("-")[-1] for p in coach_baks}
    assert surviving_suffixes == {"120002", "120003", "120004"}, (
        f"prune kept the wrong 3: {surviving_suffixes}"
    )


def test_install_no_prune_backups_keeps_all(tmp_path: Path) -> None:
    """`--no-prune-backups` opts out of v0.5.2's default-on prune.

    Use case: user is intentionally holding many .bak.<ts> for forensic
    or recovery purposes and doesn't want install to thin them. Pinned
    so a future "small UX improvement" can't silently flip the default
    again without flagging this contract.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    for i in range(5):
        d = claude_dir / f"coach.bak.20260101-12000{i}"
        d.mkdir()
        (d / "marker").write_text(f"backup-{i}")

    result = _run_install(repo, claude_dir, args=["--fresh", "--no-prune-backups"])
    assert result.returncode == 0, result.stdout + result.stderr

    coach_baks = sorted(claude_dir.glob("coach.bak.20260101-*"))
    assert len(coach_baks) == 5, (
        f"--no-prune-backups must NOT delete .bak.<ts>; only {len(coach_baks)} of 5 survived."
    )


def test_install_recovers_state_from_prior_bak_after_uninstall(tmp_path: Path) -> None:
    """install.sh recovers profile + throttle marker + .git from a prior uninstall.

    Scenario: `/coach uninstall` renamed `coach/` to `coach.bak.<ts>/`,
    then user re-runs `./install.sh`. Without recovery, install treats
    it as a fresh install and the user silently loses:
      - profile.yaml (their tracked weaknesses/strengths)
      - .last_weekly_insights (→ unintended paid /insights API call
        on next SessionStart)
      - per-run git history (rollback UX broken)

    v0.5.2 fix: when no live `coach/` exists but `coach.bak.*` does,
    install renames the most-recent .bak back to coach/ before the
    existing preserve + .git restore logic runs.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()

    # Build a realistic post-uninstall coach.bak.<ts> with state files +
    # a real .git repo with one commit (so we can verify history survives).
    bak = claude_dir / "coach.bak.20260105-100000"
    bak.mkdir()
    (bak / "profile.yaml").write_text("schema_version: 1\nentries:\n- id: marker\n")
    (bak / "banked_sessions.json").write_text('{"sess-1": {"xp": 5}}')
    (bak / ".last_weekly_insights").write_text("")  # zero-byte throttle marker
    (bak / "changelog.md").write_text("# changelog\n")
    subprocess.run(["git", "init", "-q"], cwd=bak, check=True, env={**os.environ, **_git_env()})
    subprocess.run(["git", "add", "-A"], cwd=bak, check=True, env={**os.environ, **_git_env()})
    subprocess.run(
        ["git", "commit", "-q", "-m", "pre-uninstall snapshot"],
        cwd=bak, check=True, env={**os.environ, **_git_env()},
    )
    pre_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=bak, capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Install mode: recovered" in result.stdout, result.stdout

    coach = claude_dir / "coach"
    assert coach.exists(), "coach/ should exist after recovery"
    assert (coach / "profile.yaml").read_text() == "schema_version: 1\nentries:\n- id: marker\n"
    assert (coach / "banked_sessions.json").read_text() == '{"sess-1": {"xp": 5}}'
    assert (coach / ".last_weekly_insights").exists(), (
        "throttle marker MUST be preserved — its absence triggers an unintended "
        "paid /insights API call on next SessionStart"
    )

    # The pre-uninstall commit must still be in the live coach git log.
    log = subprocess.run(
        ["git", "log", "--format=%H"],
        cwd=coach, capture_output=True, text=True, check=True,
    ).stdout
    assert pre_commit in log, (
        f"pre-uninstall commit {pre_commit[:7]} missing from recovered git history"
    )


def test_install_fresh_flag_skips_bak_recovery(tmp_path: Path) -> None:
    """`--fresh` forces a true fresh install even if coach.bak.<ts> exists."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    bak = claude_dir / "coach.bak.20260105-100000"
    bak.mkdir()
    (bak / "profile.yaml").write_text("schema_version: 1\nentries:\n- id: from-bak\n")

    result = _run_install(repo, claude_dir, args=["--fresh"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Install mode: fresh" in result.stdout, result.stdout

    # The .bak must still be sitting where we left it (untouched).
    assert bak.exists()
    assert (bak / "profile.yaml").read_text() == "schema_version: 1\nentries:\n- id: from-bak\n"

    # The new live coach/ must be the bundle template, not the .bak content.
    live_profile = (claude_dir / "coach" / "profile.yaml").read_text()
    assert "from-bak" not in live_profile, (
        "--fresh should NOT have copied .bak's profile into the new install"
    )


def test_install_recovery_picks_most_recent_bak(tmp_path: Path) -> None:
    """When multiple coach.bak.<ts> exist, recovery picks the most recent."""
    import time
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()

    # Older bak — should be left alone.
    older = claude_dir / "coach.bak.20260101-100000"
    older.mkdir()
    (older / "profile.yaml").write_text("# old\n")

    time.sleep(0.05)

    # Newer bak — should be the one recovered.
    newer = claude_dir / "coach.bak.20260105-100000"
    newer.mkdir()
    (newer / "profile.yaml").write_text("# new\n")

    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Install mode: recovered" in result.stdout, result.stdout
    assert "20260105" in result.stdout, (
        f"recovery should report which bak was restored, got: {result.stdout}"
    )

    # The newer bak became coach/ → its content lives there now.
    # install.sh then moves coach/ to coach.bak.<install-TS>, so the original
    # newer/ path should NOT exist anymore.
    assert not newer.exists(), (
        f"newer bak should have been renamed to coach/ then re-baked under install TS"
    )
    # The older bak must still be intact.
    assert older.exists()
    assert (older / "profile.yaml").read_text() == "# old\n"


def test_install_recovers_launchd_plist_from_prior_uninstall(tmp_path: Path) -> None:
    """install.sh restores ~/Library/LaunchAgents/com.local.claude-coach.plist
    when a prior `/coach uninstall` left it as `.uninstalled.<TS>`.

    Without this, the user has to run install-launchd.sh as a second step
    after `./install.sh` to get the daily cron pass back online — a silent
    gap between "reinstall finished" and "Coach is autonomous again".
    Symmetric with the coach.bak.<TS>/ recovery: same shape, same trigger.
    """
    import platform
    if platform.system() != "Darwin":
        pytest.skip("launchd recovery is macOS-only")

    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    # Stage a post-uninstall world: coach.bak.<TS>/ exists with state, AND a
    # .uninstalled.<TS> plist sibling exists in the LaunchAgents dir.
    bak = claude_dir / "coach.bak.20260105-100000"
    bak.mkdir()
    (bak / "profile.yaml").write_text("schema_version: 1\nentries:\n- id: marker\n")

    plist_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        '<key>Label</key><string>com.local.claude-coach</string>'
        '<key>ProgramArguments</key><array><string>/usr/bin/true</string></array>'
        '</dict></plist>\n'
    )
    uninstalled_plist = la_dir / "com.local.claude-coach.plist.uninstalled.20260105-100000"
    uninstalled_plist.write_text(plist_content)
    live_plist = la_dir / "com.local.claude-coach.plist"
    assert not live_plist.exists()

    result = _run_install(
        repo,
        claude_dir,
        extra_env={"LAUNCHAGENTS_DIR": str(la_dir)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Install mode: recovered" in result.stdout, result.stdout

    # The renamed plist should be back at its canonical path.
    assert live_plist.exists(), (
        "launchd plist should be restored to its canonical path after recovery"
    )
    assert live_plist.read_text() == plist_content, (
        "restored plist content must be byte-identical to the .uninstalled.<TS> sibling"
    )
    # The .uninstalled.<TS> sibling should be gone (renamed away, not copied).
    assert not uninstalled_plist.exists(), (
        "recovery should mv (not cp) so the .uninstalled.<TS> sibling is gone"
    )
    # And the install banner should mention the launchd recovery so the user
    # knows the daemon is back online without a second script.
    assert "restored launchd plist" in result.stdout, (
        f"recovery banner should announce launchd plist restore, got: {result.stdout}"
    )


def test_install_fresh_flag_skips_launchd_plist_recovery(tmp_path: Path) -> None:
    """`--fresh` must NOT pull a renamed launchd plist back into place either.

    Symmetry with --fresh skipping coach.bak.<TS>/ recovery: a true fresh
    install leaves both .uninstalled.<TS> and coach.bak.<TS>/ untouched so
    the user can roll back manually if needed.
    """
    import platform
    if platform.system() != "Darwin":
        pytest.skip("launchd recovery is macOS-only")

    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    uninstalled_plist = la_dir / "com.local.claude-coach.plist.uninstalled.20260105-100000"
    uninstalled_plist.write_text("<plist></plist>\n")

    result = _run_install(
        repo,
        claude_dir,
        args=["--fresh"],
        extra_env={"LAUNCHAGENTS_DIR": str(la_dir)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Install mode: fresh" in result.stdout, result.stdout

    # The .uninstalled.<TS> plist must still be sitting where we left it.
    assert uninstalled_plist.exists(), (
        "--fresh should leave the .uninstalled.<TS> plist untouched"
    )
    # And the live plist path should remain absent — install.sh doesn't
    # create the plist itself; that's install-launchd.sh's job.
    assert not (la_dir / "com.local.claude-coach.plist").exists()


def test_install_banner_includes_config_preview(tmp_path: Path) -> None:
    """The post-install banner must surface the /config slash command so
    first-time installers discover the look-customization surface.

    Pre-2026-05-08 the banner listed /coach off, /coach status, and
    /coach uninstall but omitted /config — the actual discoverability
    gap that prompted the Phase 1 banner rewrite.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    assert "/config preview" in result.stdout, (
        "post-install banner must mention /config preview so first-time "
        "installers discover the customization surface"
    )
    assert "/config theme" in result.stdout, (
        "banner should show a theme example (e.g. '/config theme ocean')"
    )
    assert "/config statusline" in result.stdout, (
        "banner should show a statusline-variant example "
        "(e.g. '/config statusline pips')"
    )
    # v0.1.5 regression guard: bracket was removed in v0.1.4 so the
    # variant count is 4. The closeout banner copy drifted and still
    # said "5 variants × 12 themes" through v0.1.4.
    assert "4 variants" in result.stdout, (
        "banner must reflect the v0.1.4 variant count (4 after bracket "
        "was dropped)"
    )
    assert "5 variants" not in result.stdout, (
        "banner is using stale '5 variants' copy — bracket was removed "
        "in v0.1.4"
    )


def test_install_no_seed_flag_parses_and_banner_reflects_choice(tmp_path: Path) -> None:
    """--no-seed parses cleanly and the banner acknowledges the explicit
    skip.

    Today the seed step only fires when --seed is passed, so --no-seed
    is functionally a no-op. It exists to (a) reserve the flag namespace
    for a future TTY-gated seed prompt and (b) let scripted/CI installs
    suppress that future prompt unambiguously.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir, args=["--no-seed"])
    assert result.returncode == 0, result.stdout + result.stderr

    # Banner must acknowledge --no-seed so the user sees their flag was honored.
    assert "--no-seed honored" in result.stdout, (
        "banner should acknowledge --no-seed (e.g. 'Seed: --no-seed honored; ...')"
    )

    # The seed step itself must not have run — the bold 'Seeding profile'
    # header is install.sh's section marker for the seed branch.
    assert "Seeding profile" not in result.stdout, (
        "--no-seed must suppress the seed branch entirely"
    )


def test_install_rejects_seed_and_no_seed_together(tmp_path: Path) -> None:
    """--seed and --no-seed are mutually exclusive; conflicting flags
    must fail fast BEFORE any destructive operation.

    Pinned because the validation point in install.sh runs immediately
    after arg parse and before preflight; a regression that moved it
    past `mv coach → coach.bak.<ts>` would silently destroy state on
    a flag typo.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir, args=["--seed", "--no-seed"])
    assert result.returncode != 0, (
        "install must fail when both --seed and --no-seed are passed"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "mutually exclusive" in combined, (
        "error message must explain why install failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    # Crucially: nothing got written. coach/ must NOT exist because
    # the validation runs before any mv/cp.
    assert not (claude_dir / "coach").exists(), (
        "destructive operations must not run when flag validation fails"
    )


def test_install_tty_prompt_default_yes_seeds(tmp_path: Path) -> None:
    """When stdin is a tty and the user hits Enter, the seed step runs.

    Pin the default-Y semantics: empty input == accept. This is the
    fresh-install happy path the backlog item was written for —
    new users get a populated profile without re-running with --seed.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    projects = fake_home / ".claude" / "projects" / "fake-project"
    projects.mkdir(parents=True)
    # Minimal valid JSONL — one well-formed event is enough for the gate
    # to consider the directory non-empty. Content doesn't matter; the
    # seed step's analyzer reads it but never asserts on shape here.
    (projects / "transcript.jsonl").write_text(
        '{"type":"user","timestamp":"2026-05-19T00:00:00Z"}\n'
    )

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-launchd"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # The prompt itself must be visible to the user.
    assert "Seed profile?" in result.stdout, (
        "expected prompt header in stdout; got:\n" + result.stdout
    )
    # And the seed branch must have actually executed.
    assert "Seeding profile" in result.stdout, (
        "Enter (default Y) must trigger the seed branch; got:\n"
        + result.stdout
    )


def test_install_creates_configure_py(tmp_path: Path) -> None:
    """Phase 2 installer must ship coach/bin/configure.py executable —
    it's the entrypoint the npm wrapper calls for `coach-claw config
    <set|preview|wizard>`."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    script = claude_dir / "coach/bin/configure.py"
    assert script.exists(), "configure.py was not installed"
    assert os.access(script, os.X_OK), "configure.py is not executable"


# ---------------------------------------------------------------------------
# Wrap-mode install (v0.1.4)
# ---------------------------------------------------------------------------


def test_install_creates_wrap_trampoline_with_substituted_python(tmp_path: Path) -> None:
    """The CLI wrap trampoline must land in coach/, with `@PY@`
    substituted to the resolved python3 path, and execute bit set.
    Symmetric with default-statusline-command.sh."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    trampoline = claude_dir / "coach/default-statusline-wrap-command.sh"
    assert trampoline.exists(), "default-statusline-wrap-command.sh missing"
    assert os.access(trampoline, os.X_OK), "trampoline not executable"

    contents = trampoline.read_text()
    assert "@PY@" not in contents, "@PY@ placeholder was not substituted"
    py = shutil.which("python3")
    assert py is not None
    assert py in contents, f"resolved python ({py}) not in trampoline contents"


def test_install_preserves_wrap_markers_on_reinstall(tmp_path: Path) -> None:
    """The preserve list must include all wrap markers — losing them on
    reinstall would either re-wrap an unwrapped user (.statusline-wrap-disabled)
    or wipe their saved-original (.statusline-wrap.json)."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    coach_dir = claude_dir / "coach"
    markers = {
        ".statusline-wrap.json": json.dumps({"original_command": "bash /opt/x.sh"}),
        ".statusline-wrap-disabled": json.dumps({"reason": "user-unwrapped"}),
        ".statusline-wrap-announced": json.dumps({"created_at": "2026-05-09T00:00:00Z"}),
        ".statusline-wrap-duplicate-detected": json.dumps({"created_at": "2026-05-09T00:00:00Z"}),
    }
    for name, content in markers.items():
        (coach_dir / name).write_text(content)

    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    for name, expected in markers.items():
        path = coach_dir / name
        assert path.exists(), (
            f"{name} was dropped by reinstall — preserve list at "
            f"install.sh is missing it"
        )
        assert path.read_text() == expected, (
            f"{name} content mutated by reinstall: was {expected!r}, "
            f"now {path.read_text()!r}"
        )


def test_install_helper_runs_before_redundant_backup_cleanup(tmp_path: Path) -> None:
    """The wrap-if-claimed helper must invoke BEFORE the redundant-backup
    prune in install.sh. Otherwise any settings.json mutation from the
    helper would land outside the .bak.<ts> backup window."""
    repo = Path(__file__).resolve().parents[2]
    install_sh = repo / "install.sh"
    if not install_sh.exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    text = install_sh.read_text()
    helper_idx = text.find("statusline_wrap_action.py")
    cleanup_idx = text.find("settings.json unchanged — discarded redundant backup")
    assert helper_idx > -1, "wrap helper invocation missing from install.sh"
    assert cleanup_idx > -1, "redundant-backup-cleanup block missing"
    assert helper_idx < cleanup_idx, (
        "wrap-if-claimed helper must run BEFORE the redundant-backup "
        "cleanup; ordering inverted in install.sh"
    )


def test_install_auto_wraps_claimed_statusline(tmp_path: Path) -> None:
    """End-to-end: install.sh on a settings.json with a non-Coach
    statusLine command auto-wraps it. Ryan's box scenario — except his
    is filtered by manual-Coach pre-flight; this fixture uses an
    unrelated script that doesn't trigger the pre-flight."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    claude_dir.mkdir()
    user_script = tmp_path / "my-line.sh"
    user_script.write_text("#!/bin/bash\necho 'plain custom statusline'\n")
    user_script.chmod(0o755)

    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({
        "statusLine": {"type": "command", "command": f"bash {user_script}"},
    }))

    result = _run_install(repo, claude_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert "default-statusline-wrap-command.sh" in new_cmd, (
        f"expected wrap-trampoline in command, got {new_cmd!r}"
    )

    saved = json.loads((claude_dir / "coach/.statusline-wrap.json").read_text())
    assert saved["original_command"] == f"bash {user_script}"

    # v0.1.5 regression guard: the generated statusLine.command runs
    # under bash with shell=True (Claude Code semantics). Pre-v0.1.5,
    # CLAUDE_DIR with spaces produced an unquoted path that bash split
    # mid-token: `bash /tmp/.../Claude Dir/coach/...` → ENOENT on
    # `Dir/coach/...`. shlex.quote on the trampoline path fixes it.
    # `claude_dir` here is `tmp_path / "Claude Dir"` — exactly the
    # fixture shape that exposes the bug.
    bash_path = shutil.which("bash")
    assert bash_path
    payload = (
        '{"model":{"display_name":"opus 4.7"},'
        '"context_window":{"used_percentage":10}}'
    )
    proc = subprocess.run(
        [bash_path, "-c", new_cmd],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"generated statusLine command failed under bash -c — likely "
        f"unquoted path with spaces.\ncommand={new_cmd!r}\n"
        f"stderr={proc.stderr!r}"
    )
    assert "No such file or directory" not in proc.stderr, proc.stderr


def test_install_wrap_helper_respects_optout_marker(tmp_path: Path) -> None:
    """If the user previously unwrapped (sticky opt-out marker), reinstall
    must NOT auto-rewrap them."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    # Run install once so coach/ exists.
    first = _run_install(repo, claude_dir)
    assert first.returncode == 0, first.stdout + first.stderr

    # Set up a custom statusLine and an opt-out marker.
    user_script = tmp_path / "my-line.sh"
    user_script.write_text("#!/bin/bash\necho 'plain'\n")
    user_script.chmod(0o755)
    settings = claude_dir / "settings.json"
    data = json.loads(settings.read_text())
    data["statusLine"] = {"type": "command", "command": f"bash {user_script}"}
    settings.write_text(json.dumps(data))
    (claude_dir / "coach/.statusline-wrap-disabled").write_text(json.dumps({
        "reason": "user-unwrapped",
    }))

    second = _run_install(repo, claude_dir)
    assert second.returncode == 0, second.stdout + second.stderr

    # statusLine still points at the user's script — not auto-rewrapped
    new_cmd = json.loads(settings.read_text())["statusLine"]["command"]
    assert new_cmd == f"bash {user_script}", (
        f"opt-out marker ignored — settings.json was rewrapped: {new_cmd!r}"
    )


def test_install_tty_prompt_n_skips_seed_and_banner_acknowledges(
    tmp_path: Path,
) -> None:
    """Declining the prompt with 'n' must skip the seed branch AND
    surface a banner line that distinguishes 'you declined the prompt'
    from 'you passed --no-seed on the command line'.

    The distinction matters because the --no-seed banner copy ('
    --no-seed honored; to seed later: ...') implies the user knew the
    flag exists. A user who hit 'n' at the prompt did not — they need
    the explicit re-seed command spelled out without flag jargon.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    projects = fake_home / ".claude" / "projects" / "fake-project"
    projects.mkdir(parents=True)
    (projects / "transcript.jsonl").write_text(
        '{"type":"user","timestamp":"2026-05-19T00:00:00Z"}\n'
    )

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="n\n", fake_home=fake_home,
        args=["--no-launchd"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # Seed branch must not have fired.
    assert "Seeding profile" not in result.stdout, (
        "'n' at the prompt must suppress the seed branch entirely"
    )
    # Banner must use the prompt-decline copy, not the --no-seed copy.
    assert "you can seed later" in result.stdout.lower(), (
        "banner should acknowledge the declined prompt with explicit "
        "re-seed instructions; got:\n" + result.stdout
    )
    # And specifically must NOT use the --no-seed flag-honored phrasing,
    # because the user did not pass that flag.
    assert "--no-seed honored" not in result.stdout, (
        "prompt-decline banner must not falsely claim --no-seed was passed"
    )


def test_install_tty_prompt_skipped_when_no_projects_dir(
    tmp_path: Path,
) -> None:
    """A fresh box with no $HOME/.claude/projects must not see the
    prompt — there are no transcripts to seed from.

    Regression guard: the gate has to short-circuit on missing-dir
    BEFORE `read -r` fires; otherwise install hangs forever waiting
    on input that never comes (pty stdin is held open by the test).
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    # Deliberately NO .claude/projects under fake_home.

    claude_dir = tmp_path / "Claude Dir"
    # Even though we pre-stuff "\n", the prompt must never fire — so
    # the input is ignored and install completes normally.
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-launchd"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert "Seed profile?" not in result.stdout, (
        "prompt must not fire when $HOME/.claude/projects is missing"
    )
    # Default no-flag banner copy must apply.
    assert "empty profile (no --seed)" in result.stdout, (
        "default banner expected when no flag and no prompt"
    )


def test_install_tty_prompt_skipped_when_projects_dir_has_no_jsonl(
    tmp_path: Path,
) -> None:
    """projects dir exists but has no .jsonl files — the find-gate
    must keep the prompt silent. Without the gate, the seed step
    would run against zero transcripts and emit a noisy 'no work
    to do' line over an empty profile.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    projects = fake_home / ".claude" / "projects" / "some-old-project"
    projects.mkdir(parents=True)
    # Deliberately no .jsonl — a stray sidecar file or empty leftover
    # from a deleted project. The find-gate must treat this as
    # "nothing to seed from."
    (projects / "stale.json").write_text("{}\n")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-launchd"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert "Seed profile?" not in result.stdout, (
        "prompt must not fire when projects dir has no .jsonl files"
    )


def test_install_help_documents_auto_prompt(tmp_path: Path) -> None:
    """--help must mention the TTY-gated auto-prompt so users know
    the seed step is no longer flag-only.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir, args=["--help"])
    assert result.returncode == 0, result.stdout + result.stderr

    out_lower = result.stdout.lower()
    # Anchor on a phrase the help text will commit to — guards against
    # the docs drifting to flag-only language again.
    assert "interactive" in out_lower and "prompt" in out_lower, (
        "--help must mention the interactive prompt; got:\n"
        + result.stdout
    )


def test_install_tty_launchd_prompt_default_yes_registers(tmp_path: Path) -> None:
    """When stdin is a tty on macOS and the user hits Enter, the
    launchd-registration step runs.

    Uses COACH_LAUNCHD_PROMPT_DRY_RUN=1 so install.sh prints a
    "would run install-launchd.sh" message instead of actually
    spawning the launchd installer (which would register a real
    daily job on the test machine).
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    # No .claude/projects → seed prompt won't fire, so the only pty
    # input gets consumed by the launchd prompt.

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-seed"],
        extra_env={
            "COACH_LAUNCHD_PROMPT_DRY_RUN": "1",
            # LaunchAgents dir is empty in the temp tree, so the
            # "plist already exists" gate doesn't fire.
            "LAUNCHAGENTS_DIR": str(tmp_path / "LaunchAgents"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # The prompt header must be visible.
    assert "Register daily Coach insights cron?" in result.stdout, (
        "expected launchd prompt header in stdout; got:\n" + result.stdout
    )
    # And the dry-run message must show the installer would have run.
    assert "would run install-launchd.sh" in result.stdout, (
        "Enter (default Y) must trigger the launchd-installer dry-run; "
        "got:\n" + result.stdout
    )


def test_install_tty_launchd_prompt_n_skips_and_banner_acknowledges(
    tmp_path: Path,
) -> None:
    """Declining the launchd prompt with 'n' must skip registration AND
    surface a banner line that distinguishes 'you declined the prompt'
    from 'you passed --no-launchd on the command line'.

    Same distinction the seed prompt makes — a user who hit 'n' did
    not know about the flag and needs the explicit re-run command
    without flag jargon.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="n\n", fake_home=fake_home,
        args=["--no-seed"],
        extra_env={
            "COACH_LAUNCHD_PROMPT_DRY_RUN": "1",
            "LAUNCHAGENTS_DIR": str(tmp_path / "LaunchAgents"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # Registration must not have fired (dry-run sentinel absent).
    assert "would run install-launchd.sh" not in result.stdout, (
        "'n' at the prompt must suppress the launchd installer entirely"
    )
    # Banner must use the prompt-decline copy.
    assert "skipped at prompt; you can register later" in result.stdout.lower(), (
        "banner should acknowledge the declined prompt with explicit "
        "re-run instructions; got:\n" + result.stdout
    )


def test_install_tty_launchd_prompt_skipped_when_plist_exists(
    tmp_path: Path,
) -> None:
    """If the live plist already exists at LAUNCHAGENTS_DIR/com.local.claude-coach.plist
    (upgrade install), the prompt must not fire. Re-prompting on every
    upgrade would be a usability regression.

    Regression guard: the gate has to short-circuit on plist-exists
    BEFORE `read -r` fires; otherwise the upgrade install hangs forever
    waiting on input that never comes.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    # Pre-seed the live plist as if a prior install-launchd.sh ran.
    # Contents don't matter — only presence does.
    (la_dir / "com.local.claude-coach.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist><dict/></plist>\n'
    )

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-seed"],
        extra_env={
            "COACH_LAUNCHD_PROMPT_DRY_RUN": "1",
            "LAUNCHAGENTS_DIR": str(la_dir),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert "Register daily Coach insights cron?" not in result.stdout, (
        "prompt must not fire when the live plist already exists"
    )
    # The default banner step (existing static command) is what the user
    # should see when neither registration nor decline happened.
    assert "npx @rm0nroe/coach-claw@latest launchd" in result.stdout, (
        "default step-4 banner copy expected when no prompt fired"
    )


def test_install_tty_launchd_prompt_skipped_on_non_macos(
    tmp_path: Path,
) -> None:
    """Linux users must not see the launchd prompt. Uses
    COACH_UNAME_OVERRIDE=Linux to exercise the gate on a macOS dev
    box without actually running on Linux.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install_with_pty(
        repo, claude_dir, reply="\n", fake_home=fake_home,
        args=["--no-seed"],
        extra_env={
            "COACH_LAUNCHD_PROMPT_DRY_RUN": "1",
            "COACH_UNAME_OVERRIDE": "Linux",
            "LAUNCHAGENTS_DIR": str(tmp_path / "LaunchAgents"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert "Register daily Coach insights cron?" not in result.stdout, (
        "launchd prompt must not fire on non-macOS"
    )


def test_install_help_documents_launchd_prompt(tmp_path: Path) -> None:
    """--help must mention the launchd interactive prompt so users know
    the step is no longer flag-only on macOS.
    """
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "install.sh").exists():
        pytest.skip("install.sh is only present in the shareable repo checkout")

    claude_dir = tmp_path / "Claude Dir"
    result = _run_install(repo, claude_dir, args=["--help"])
    assert result.returncode == 0, result.stdout + result.stderr

    out_lower = result.stdout.lower()
    # Anchor on both flags appearing in the help, since they were added
    # in Task 1's USAGE heredoc update. Also check for "interactive
    # prompt" to ensure the help text actually describes the prompt
    # path, not just the flags as standalone options.
    assert "--launchd" in result.stdout, (
        "--help must list the --launchd flag; got:\n" + result.stdout
    )
    assert "--no-launchd" in result.stdout, (
        "--help must list the --no-launchd flag; got:\n" + result.stdout
    )
    assert "interactive prompt" in out_lower, (
        "--help must describe the interactive prompt path; got:\n"
        + result.stdout
    )
