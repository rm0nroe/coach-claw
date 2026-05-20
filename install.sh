#!/usr/bin/env bash
# Coach Claw — installer
#
# Copies the coach binaries, hooks, and skills into ~/.claude/ (or CLAUDE_DIR),
# registers
# the SessionStart + UserPromptSubmit hooks plus a statusLine when one is not
# already configured in settings.json, and git-inits the coach data directory
# for rollback.
#
# Idempotent — re-running is safe:
#   • existing coach dir is moved to coach.bak.<ts> before copy
#   • hooks + settings.json are backed up to .bak.<ts> before patch
#   • existing coach state is preserved (only ships template on fresh install)
#   • settings.json hook/statusline entries are added only if not already present
#
# Uninstall: run `/coach uninstall` inside Claude Code after install,
# or see artifacts/infrastructure.md § Uninstall for manual steps.

set -uo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
TS="$(date +%Y%m%d-%H%M%S)"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
note() { printf "  %s\n" "$*"; }
warn() { printf "\033[33m  WARN: %s\033[0m\n" "$*"; }
ok()   { printf "\033[32m  OK: %s\033[0m\n" "$*"; }
die()  { printf "\033[31m  ERROR: %s\033[0m\n" "$*"; exit 1; }

# Allow tests to fake the OS gate without actually running uname.
# `_coach_uname` returns `${COACH_UNAME_OVERRIDE:-$(uname -s)}` so a test
# can set COACH_UNAME_OVERRIDE=Linux and exercise the non-macOS path on
# a macOS dev box.
_coach_uname() { echo "${COACH_UNAME_OVERRIDE:-$(uname -s)}"; }

# --- Flags -------------------------------------------------------------------
# --seed / --bootstrap → after install, run insights.sh 7d once so the
# user doesn't have an empty profile on first Claude Code session.

SEED=0
NO_SEED=0
LAUNCHD=0
NO_LAUNCHD=0
PRUNE_BACKUPS=1
FRESH=0
SEED_DECLINED_AT_PROMPT=0
LAUNCHD_DECLINED_AT_PROMPT=0
for arg in "$@"; do
  case "$arg" in
    --seed|--bootstrap) SEED=1 ;;
    --no-seed) NO_SEED=1 ;;
    --launchd) LAUNCHD=1 ;;
    --no-launchd) NO_LAUNCHD=1 ;;
    --prune-backups) PRUNE_BACKUPS=1 ;;
    --no-prune-backups) PRUNE_BACKUPS=0 ;;
    --fresh) FRESH=1 ;;
    -h|--help)
      cat <<USAGE
Usage: $(basename "$0") [--seed | --no-seed] [--no-prune-backups] [--fresh]

  --seed / --bootstrap   After install, run insights.sh 7d against your
                         existing Claude Code transcripts so the profile
                         isn't empty on your first session. Equivalent
                         to accepting the interactive prompt. Safe to
                         omit in a tty (you'll be asked); CI/piped
                         installs default to skipping.

  --no-seed              Explicitly skip seeding and suppress the
                         interactive prompt. Use this for scripted/CI
                         installs that should never block on input.
                         Mutually exclusive with --seed.

  --launchd              After install, run install-launchd.sh to register
                         the macOS daily Coach insights job. Equivalent to
                         accepting the interactive prompt. Safe to omit in
                         a tty (you'll be asked); CI/piped installs default
                         to skipping. macOS-only.

  --no-launchd           Explicitly skip launchd registration and suppress
                         the interactive prompt. Use this for scripted/CI
                         installs that should never block on input.
                         Mutually exclusive with --launchd.

  --no-prune-backups     Keep all coach.bak.*, settings.json.bak.*, and
                         hooks/*.bak.* files. Default is to keep only the
                         3 most recent of each kind so ~/.claude/ doesn't
                         accumulate hundreds of backups across upgrades.

  --fresh                Skip recovery from a prior /coach uninstall. By
                         default, if no live coach/ dir exists but a
                         coach.bak.<ts>/ does, the most-recent backup is
                         restored before install (preserving profile,
                         throttle marker, and git history). --fresh forces
                         a true fresh install regardless.
USAGE
      exit 0 ;;
    *) warn "unknown arg: $arg (ignored)" ;;
  esac
done

# Mutually-exclusive flag check — fail-fast BEFORE preflight so we don't
# touch anything on disk when the args are nonsense.
if [[ "$SEED" == "1" && "$NO_SEED" == "1" ]]; then
  printf "\033[31m  ERROR: --seed and --no-seed are mutually exclusive.\033[0m\n" >&2
  exit 1
fi

if [[ "$LAUNCHD" == "1" && "$NO_LAUNCHD" == "1" ]]; then
  printf "\033[31m  ERROR: --launchd and --no-launchd are mutually exclusive.\033[0m\n" >&2
  exit 1
fi

# --- Preflight ---------------------------------------------------------------

bold "Preflight"

command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH"
PY="$(command -v python3)"
note "python3: $PY"

# Resolve the Python version — need 3.8+ for f-strings + from __future__ annotations
PY_OK="$("$PY" - <<'PYEOF'
import sys
print("ok" if sys.version_info >= (3, 8) else f"too old ({sys.version_info[:2]})")
PYEOF
)"
[[ "$PY_OK" == "ok" ]] || die "python3 too old: $PY_OK. Coach needs Python 3.8+."
ok "python3 version adequate"

_have_yaml() { "$PY" -c "import yaml" 2>/dev/null; }

# Two-strategy fallback. pip --user covers system Python, pyenv, asdf,
# conda. --break-system-packages bypasses PEP 668 for Homebrew Python
# 3.12+ where the first attempt is rejected. (PyYAML is not in Homebrew
# core, so the brew formula is not a recovery path either.) We re-test
# `import yaml` after each pip call so a pip that "succeeded" but didn't
# actually land on PYTHONPATH still triggers the next step.
_install_pyyaml() {
  note "Trying: $PY -m pip install --user pyyaml"
  if "$PY" -m pip install --user pyyaml >/dev/null 2>&1 && _have_yaml; then
    return 0
  fi

  note "Trying: $PY -m pip install --user --break-system-packages pyyaml"
  note "  (bypasses PEP 668 for the per-user install — safe for libraries)"
  if "$PY" -m pip install --user --break-system-packages pyyaml >/dev/null 2>&1 && _have_yaml; then
    return 0
  fi

  return 1
}

if ! _have_yaml; then
  warn "PyYAML not installed for $PY — attempting auto-install."
  if ! _install_pyyaml; then
    printf "\033[31m  ERROR: could not install PyYAML automatically.\033[0m\n\n"
    printf "  Pick one of these and re-run ./install.sh:\n\n"
    printf "    %s -m pip install --user --break-system-packages pyyaml\n" "$PY"
    printf "        Bypasses PEP 668 for a per-user install. Safe.\n\n"
    printf "    %s -m venv ~/.coach-venv && ~/.coach-venv/bin/pip install pyyaml\n" "$PY"
    printf "        Then re-run install.sh with PATH=~/.coach-venv/bin:\$PATH so\n"
    printf "        the preflight uses the venv's python3.\n"
    exit 1
  fi
fi
ok "PyYAML available"

if [[ ! -d "$CLAUDE_DIR" ]]; then
  warn "$CLAUDE_DIR does not exist — creating it. (Normally Claude Code creates this on first launch.)"
  mkdir -p "$CLAUDE_DIR"
fi
ok "Claude config dir: $CLAUDE_DIR"

# --- Recover from prior /coach uninstall -----------------------------------

# If a previous /coach uninstall moved coach/ to coach.bak.<ts>/ (so coach/
# itself doesn't exist), revive the most-recent .bak before the existing
# preserve + .git restore flow runs. Without this, the install treats it
# as a true fresh install and silently drops the user's profile.yaml,
# .last_weekly_insights throttle marker (→ unintended paid /insights
# call on next SessionStart), and per-run git history. --fresh opts out.
RECOVERED_FROM=""
if [[ ! -e "$CLAUDE_DIR/coach" && "$FRESH" != "1" ]]; then
  prior_bak="$(ls -dt "$CLAUDE_DIR"/coach.bak.* 2>/dev/null | head -1)"
  if [[ -n "$prior_bak" && -d "$prior_bak" ]]; then
    mv "$prior_bak" "$CLAUDE_DIR/coach"
    RECOVERED_FROM="$(basename "$prior_bak")"
  fi
fi

# Compute install mode for the banner: fresh / upgrade / recovered.
if [[ -n "$RECOVERED_FROM" ]]; then
  MODE="recovered"
elif [[ -e "$CLAUDE_DIR/coach" ]]; then
  MODE="upgrade"
else
  MODE="fresh"
fi

case "$MODE" in
  fresh)
    bold "Install mode: fresh"
    note "no prior coach/ or coach.bak.* detected"
    ;;
  upgrade)
    bold "Install mode: upgrade"
    note "preserving live ~/.claude/coach/ state"
    ;;
  recovered)
    bold "Install mode: recovered"
    note "restored prior uninstall from $RECOVERED_FROM"
    note "  (preserving profile, throttle marker, git history; pass --fresh to skip)"
    ;;
esac

# --- Recover launchd plist from prior /coach uninstall ---------------------

# Symmetric with the coach/ recovery above. /coach uninstall renames the
# plist to .uninstalled.<TS> and unloads it; without this block the user
# has to run install-launchd.sh separately to get the daily cron back —
# which is a real footgun (silent gap between reinstall and "Coach is
# autonomous again"). macOS-only; Linux uses cron, no plist.
#
# LAUNCHAGENTS_DIR override exists for test_install.py so the test can
# stage fixtures in a tmp dir without touching the real ~/Library/LaunchAgents/.
LAUNCHD_RECOVERED_FROM=""
LA_DIR="${LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
if [[ "$(uname)" == "Darwin" && "$FRESH" != "1" && -d "$LA_DIR" ]]; then
  LIVE_PLIST="$LA_DIR/com.local.claude-coach.plist"
  if [[ ! -e "$LIVE_PLIST" ]]; then
    prior_plist="$(ls -dt "$LIVE_PLIST".uninstalled.* 2>/dev/null | head -1)"
    if [[ -n "$prior_plist" && -f "$prior_plist" ]]; then
      mv "$prior_plist" "$LIVE_PLIST"
      LAUNCHD_RECOVERED_FROM="$(basename "$prior_plist")"
      if command -v launchctl >/dev/null 2>&1; then
        # unload first in case launchd somehow has a stale registration —
        # mirrors the unload-then-load pattern in install-launchd.sh
        launchctl unload "$LIVE_PLIST" 2>/dev/null || true
        if launchctl load "$LIVE_PLIST" 2>/dev/null; then
          note "restored launchd plist from $LAUNCHD_RECOVERED_FROM (job loaded)"
        else
          warn "restored launchd plist but launchctl load failed; run ./install-launchd.sh to reload"
        fi
      else
        note "restored launchd plist from $LAUNCHD_RECOVERED_FROM (launchctl unavailable; load manually)"
      fi
    fi
  fi
fi

# --- Backup existing pieces (if present) ------------------------------------

bold "Backups"

if [[ -e "$CLAUDE_DIR/coach" ]]; then
  # Preserve user-owned state so reinstall updates code/docs without resetting
  # progress, cooldowns, pending notifications, or the disabled flag.
  TMP_PARENT="${TMPDIR:-/tmp}"
  PRESERVE_DIR="$(mktemp -d "${TMP_PARENT%/}/coach-preserve.XXXXXX")" || \
    die "failed to create temporary preserve directory"
  for state_file in \
    profile.yaml banked_sessions.json changelog.md log.ndjson \
    .disabled .tip_state.json .level_state.json .last_session_start \
    .last_weekly_insights .user_config.json \
    .pending_* \
    .statusline-wrap.json .statusline-wrap-disabled \
    .statusline-wrap-announced .statusline-wrap-duplicate-detected; do
    for src in "$CLAUDE_DIR/coach"/$state_file; do
      [[ -e "$src" ]] || continue
      cp -p "$src" "$PRESERVE_DIR/$(basename "$src")"
    done
  done
  note "preserved existing coach state → $PRESERVE_DIR"
  mv "$CLAUDE_DIR/coach" "$CLAUDE_DIR/coach.bak.$TS"
  ok "moved existing coach dir → coach.bak.$TS"
fi

for hook in coach-session-start.py coach-user-prompt.py; do
  if [[ -e "$CLAUDE_DIR/hooks/$hook" ]]; then
    # Skip backup when bundle and live copy are byte-identical — re-running
    # the installer with no code changes upstream would otherwise pile up
    # an empty .bak.<ts> per run. Real diffs still get backed up.
    if cmp -s "$BUNDLE_DIR/hooks/$hook" "$CLAUDE_DIR/hooks/$hook"; then
      note "$hook unchanged — skipping backup"
    else
      cp "$CLAUDE_DIR/hooks/$hook" "$CLAUDE_DIR/hooks/$hook.bak.$TS"
      ok "backed up existing hook: $hook"
    fi
  fi
done

if [[ -f "$CLAUDE_DIR/settings.json" ]]; then
  # Snapshot first, then let the patch run. The post-patch cleanup block
  # (after the python heredoc) drops this .bak when the patch is a no-op
  # (settings.json byte-identical post-patch), so byte-identical reinstalls
  # don't pile up backups.
  cp "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR/settings.json.bak.$TS"
  ok "backed up settings.json"
fi

# v0.4.0 — Coach's old /insights skill shadowed Claude Code's built-in.
# Move the legacy skill aside so the built-in becomes reachable again.
# `mv` (not rm -rf) so any user customizations are recoverable.
if [[ -d "$CLAUDE_DIR/skills/insights" ]]; then
  mv "$CLAUDE_DIR/skills/insights" "$CLAUDE_DIR/skills/insights.bak.$TS"
  note "moved legacy /insights skill → skills/insights.bak.$TS"
  note "  (Coach's old skill no longer shadows Claude Code's built-in /insights)"
fi

# Claude Code's skill loader picks up any directory under `skills/` that
# contains a `SKILL.md`. A backup dir like `skills/insights.bak.<ts>/`
# would therefore become a live slash command (`/insights.bak.<ts>`) —
# polluting the catalog. Rename SKILL.md → SKILL.md.bak inside any
# `insights.bak.*/` dir so the loader skips it. Idempotent — runs every
# install, fixes both the freshly-moved bak from above AND any older
# bak dirs left over from a buggy v0.4.0 first-pass install.
for bak_skill in "$CLAUDE_DIR"/skills/insights.bak.*/SKILL.md; do
  [[ -f "$bak_skill" ]] || continue
  mv "$bak_skill" "${bak_skill}.bak"
  note "defanged stale legacy SKILL.md → $(basename "$(dirname "$bak_skill")")/SKILL.md.bak"
done

# --- Copy files --------------------------------------------------------------

bold "Installing files"

mkdir -p "$CLAUDE_DIR/coach/bin" "$CLAUDE_DIR/coach/tests" \
         "$CLAUDE_DIR/hooks" \
         "$CLAUDE_DIR/skills/coach" "$CLAUDE_DIR/skills/coach-insights"

# Data files — profile is restored from preservation below if present
cp "$BUNDLE_DIR/coach/profile.yaml" "$CLAUDE_DIR/coach/profile.yaml"
cp "$BUNDLE_DIR/coach/changelog.md" "$CLAUDE_DIR/coach/changelog.md"
cp "$BUNDLE_DIR/coach/README.md"    "$CLAUDE_DIR/coach/README.md"
[[ -f "$BUNDLE_DIR/coach/.gitignore" ]] && cp "$BUNDLE_DIR/coach/.gitignore" "$CLAUDE_DIR/coach/.gitignore"
touch                                "$CLAUDE_DIR/coach/log.ndjson"

# Binaries — ALL coach/bin/*.py + *.sh go in
for f in "$BUNDLE_DIR"/coach/bin/*.py "$BUNDLE_DIR"/coach/bin/*.sh; do
  [[ -e "$f" ]] && cp "$f" "$CLAUDE_DIR/coach/bin/$(basename "$f")"
done

# Tests (optional — contributors can run pytest from ~/.claude/coach/)
for f in "$BUNDLE_DIR"/coach/tests/*.py; do
  [[ -e "$f" ]] && cp "$f" "$CLAUDE_DIR/coach/tests/$(basename "$f")"
done

# Hooks — BOTH SessionStart AND UserPromptSubmit
cp "$BUNDLE_DIR/hooks/coach-session-start.py" "$CLAUDE_DIR/hooks/coach-session-start.py"
cp "$BUNDLE_DIR/hooks/coach-user-prompt.py"   "$CLAUDE_DIR/hooks/coach-user-prompt.py"

# Skills (slash commands: /coach, /coach-insights, /config)
cp "$BUNDLE_DIR/skills/coach/SKILL.md"                "$CLAUDE_DIR/skills/coach/SKILL.md"
cp "$BUNDLE_DIR/skills/coach-insights/SKILL.md"       "$CLAUDE_DIR/skills/coach-insights/SKILL.md"
mkdir -p "$CLAUDE_DIR/skills/config"
[[ -f "$BUNDLE_DIR/skills/config/SKILL.md" ]] && \
  cp "$BUNDLE_DIR/skills/config/SKILL.md" "$CLAUDE_DIR/skills/config/SKILL.md"

# Default statusline composition: model + context-bar + coach segment.
# `@PY@` is substituted with the resolved python path so the script
# doesn't depend on PATH at statusline-render time.
if [[ -f "$BUNDLE_DIR/coach/default-statusline-command.sh" ]]; then
  sed "s|@PY@|$PY|g" "$BUNDLE_DIR/coach/default-statusline-command.sh" \
    > "$CLAUDE_DIR/coach/default-statusline-command.sh"
fi

# Wrap-mode statusline trampoline (v0.1.4): symmetric installer-time
# substitution. settings.json:statusLine.command points at this when
# the user's existing statusLine got auto-wrapped by the wrap helper.
if [[ -f "$BUNDLE_DIR/coach/default-statusline-wrap-command.sh" ]]; then
  sed "s|@PY@|$PY|g" "$BUNDLE_DIR/coach/default-statusline-wrap-command.sh" \
    > "$CLAUDE_DIR/coach/default-statusline-wrap-command.sh"
fi

# Make the executables executable
chmod +x "$CLAUDE_DIR/coach/bin/"*.py "$CLAUDE_DIR/coach/bin/"*.sh \
         "$CLAUDE_DIR/coach/default-statusline-command.sh" \
         "$CLAUDE_DIR/coach/default-statusline-wrap-command.sh" \
         "$CLAUDE_DIR/hooks/coach-session-start.py" \
         "$CLAUDE_DIR/hooks/coach-user-prompt.py" 2>/dev/null || true
ok "files copied"

# Restore preserved user data if this was an upgrade (not a fresh install)
if [[ -n "${PRESERVE_DIR:-}" && -d "$PRESERVE_DIR" ]]; then
  restored=0
  for src in "$PRESERVE_DIR"/* "$PRESERVE_DIR"/.[!.]*; do
    [[ -f "$src" ]] || continue
    cp -p "$src" "$CLAUDE_DIR/coach/$(basename "$src")"
    restored=$((restored + 1))
  done
  rm -rf "$PRESERVE_DIR"
  ok "restored existing coach state ($restored files; progress preserved)"
fi

# Restore the per-run git history if this was an upgrade. The profile-mutation
# log lives at ~/.claude/coach/.git/ and the documented rollback UX is
# `git -C ~/.claude/coach checkout HEAD~1 -- profile.yaml`. Without this,
# every upgrade would reset that history to a single bootstrap commit.
if [[ -d "$CLAUDE_DIR/coach.bak.$TS/.git" && ! -d "$CLAUDE_DIR/coach/.git" ]]; then
  cp -R "$CLAUDE_DIR/coach.bak.$TS/.git" "$CLAUDE_DIR/coach/.git"
  ok "restored git history from previous install (rollback UX preserved)"
fi

# --- Git-init the coach data dir for rollback -------------------------------

bold "Git-init coach data dir (so every profile change is a commit)"

if [[ ! -d "$CLAUDE_DIR/coach/.git" ]]; then
  ( cd "$CLAUDE_DIR/coach" && git init -q && git add -A && \
    git commit -q -m "Bootstrap coach directory" --allow-empty )
  ok "git initialized at ~/.claude/coach"
else
  ok "git already initialized"
fi

# --- Patch settings.json -----------------------------------------------------

bold "Patching settings.json (additive, safe)"

SETTINGS="$CLAUDE_DIR/settings.json"
if [[ ! -f "$SETTINGS" ]]; then
  echo '{}' > "$SETTINGS"
  warn "no existing settings.json — created an empty one"
fi

# Patch is wrapped in try/except so a corrupt settings.json doesn't crash the
# installer — we report + point the user at their .bak file.
"$PY" - "$SETTINGS" "$PY" "$CLAUDE_DIR" <<'PYEOF'
import json, shlex, sys, traceback

settings_path, py, claude_dir = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    with open(settings_path) as f:
        data = json.load(f)
except Exception as e:
    print(f"  ERROR: settings.json is not valid JSON: {e}")
    print(f"  Your original was backed up. Fix the JSON and re-run ./install.sh.")
    sys.exit(1)

# We resolve `$PY` at install time and hardcode it in the hook command so the
# hook fires correctly even if Claude Code's runtime shell PATH doesn't
# include Homebrew/pyenv etc. Users can swap this manually later if they
# change interpreter.
hook_specs = [
    ("SessionStart",     "coach-session-start.py", 3),
    ("UserPromptSubmit", "coach-user-prompt.py",   2),
]

py_cmd = shlex.quote(py)
stats_path = shlex.quote(f"{claude_dir}/coach/bin/stats.py")
default_statusline_path = shlex.quote(
    f"{claude_dir}/coach/default-statusline-command.sh"
)

hooks = data.setdefault("hooks", {})
changed = False
for event, script_name, timeout in hook_specs:
    buckets = hooks.setdefault(event, [])
    already = any(
        script_name in h.get("command", "")
        for group in buckets if isinstance(group, dict)
        for h in (group.get("hooks") or []) if isinstance(h, dict)
    )
    if already:
        print(f"  OK: {event} hook already registered (no change)")
        continue
    entry = {
        "type": "command",
        "command": f"{py_cmd} {shlex.quote(f'{claude_dir}/hooks/{script_name}')}",
        "timeout": timeout,
    }
    buckets.append({"hooks": [entry]})
    changed = True
    print(f"  OK: {event} hook added ({script_name}, timeout={timeout}s)")

status = data.get("statusLine")
if isinstance(status, dict) and (
    "default-statusline-command.sh" in str(status.get("command", ""))
    or "stats.py" in str(status.get("command", ""))
):
    print("  OK: statusLine already registered for Coach (no change)")
elif status:
    print("  OK: existing statusLine left unchanged (Coach default not installed)")
else:
    data["statusLine"] = {
        "type": "command",
        "command": f"bash {default_statusline_path}",
    }
    changed = True
    print("  OK: statusLine added (coach/default-statusline-command.sh)")

if changed:
    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)
PYEOF

[[ $? -eq 0 ]] || die "settings.json patch failed — see message above"

# Wrap-mode auto-wrap (v0.1.4). When settings.json:statusLine is
# `claimed` (user's custom shell script), the helper appends Coach's
# segment by saving the original and replacing the command with our
# wrap trampoline. Skips if a sticky opt-out marker is present OR the
# user's script already references Coach internals (manual-Coach
# pre-flight). Always exits 0 — never breaks an install.
COACH_CONFIG_DIR="$CLAUDE_DIR/coach" \
CLAUDE_SETTINGS_PATH="$SETTINGS" \
  "$PY" "$CLAUDE_DIR/coach/bin/statusline_wrap_action.py" wrap-if-claimed || true

# If the patch ran cleanly but didn't actually change settings.json (everything
# already registered, byte-identical output), the .bak.<ts> from the snapshot
# above is dead weight. Drop it. Real diffs leave the .bak in place for the
# user to recover from if anything went sideways.
if [[ -f "$SETTINGS.bak.$TS" ]] && cmp -s "$SETTINGS" "$SETTINGS.bak.$TS"; then
  rm -f "$SETTINGS.bak.$TS"
  note "settings.json unchanged — discarded redundant backup"
fi

# --- Smoke-test the hooks ----------------------------------------------------

bold "Smoke-testing hooks"
for hook in coach-session-start.py coach-user-prompt.py; do
  OUT="$(echo '{}' | COACH_DISABLE=1 "$PY" "$CLAUDE_DIR/hooks/$hook" 2>/dev/null || true)"
  if [[ -z "$OUT" ]]; then
    ok "$hook exits cleanly with COACH_DISABLE=1 (side-effect-free)"
  else
    note "$hook emitted output even with COACH_DISABLE=1"
  fi
done

# --- Prune accumulated backups (default; opt out with --no-prune-backups) ---

if [[ "$PRUNE_BACKUPS" == "1" ]]; then
  bold "Pruning old backups (keeping 3 most recent of each kind)"
  pruned=0
  # `while read` instead of `for $(ls ...)` so paths with spaces in
  # $CLAUDE_DIR (e.g. ~/Library/Application Support/...) survive
  # word-splitting. ls -t is mtime-descending, tail -n +4 skips the 3
  # most recent.

  # coach.bak.<ts>/ directories
  while IFS= read -r old; do
    [[ -z "$old" ]] && continue
    rm -rf -- "$old" && pruned=$((pruned + 1))
  done <<< "$(ls -dt "$CLAUDE_DIR"/coach.bak.* 2>/dev/null | tail -n +4)"

  # settings.json.bak.<ts> files
  while IFS= read -r old; do
    [[ -z "$old" ]] && continue
    rm -f -- "$old" && pruned=$((pruned + 1))
  done <<< "$(ls -t "$CLAUDE_DIR"/settings.json.bak.* 2>/dev/null | tail -n +4)"

  # hooks/<hook>.bak.<ts> files (per-hook accounting so each hook keeps 3)
  for hook in coach-session-start.py coach-user-prompt.py; do
    while IFS= read -r old; do
      [[ -z "$old" ]] && continue
      rm -f -- "$old" && pruned=$((pruned + 1))
    done <<< "$(ls -t "$CLAUDE_DIR"/hooks/"$hook".bak.* 2>/dev/null | tail -n +4)"
  done

  ok "pruned $pruned old backup(s)"
fi

# --- Auto-seed prompt (TTY-gated) -------------------------------------------
# When neither --seed nor --no-seed was passed AND stdin is a real tty AND
# the user has existing transcripts, ask once before the seed step. Empty
# input or y/Y/yes accepts (sets SEED=1 so the existing seed branch fires);
# anything else declines (sets NO_SEED=1 + SEED_DECLINED_AT_PROMPT=1 so the
# banner uses the prompt-aware copy, not the --no-seed flag copy). Non-tty
# installs (CI, piped, < /dev/null) skip this entirely
# and preserve the original silent behavior.

if [[ "$SEED" == "0" && "$NO_SEED" == "0" ]] && [[ -t 0 ]]; then
  if [[ -d "$HOME/.claude/projects" ]] && \
     find "$HOME/.claude/projects" -name '*.jsonl' -type f 2>/dev/null | head -1 | grep -q .; then
    printf "\n"
    bold "Seed profile?"
    note "Found existing Claude Code transcripts at \$HOME/.claude/projects."
    note "Seeding analyzes the last 7 days so your profile isn't empty on first session."
    printf "  Run seed now? [Y/n] "
    read -r SEED_REPLY
    # Exact match only — mixed-case like 'Yes' or 'YeS' falls through to decline.
    # Most users hit Enter (the default) anyway; users typing a deliberate reply
    # use the canonical 'y' or 'n' shown in the prompt.
    case "$SEED_REPLY" in
      ""|y|Y|yes|YES) SEED=1 ;;
      *) NO_SEED=1; SEED_DECLINED_AT_PROMPT=1 ;;
    esac
  fi
fi

# --- Auto-launchd-registration prompt (macOS + TTY-gated) -------------------
# When stdin is a real tty AND we're on macOS AND neither --launchd nor
# --no-launchd was passed AND the live plist doesn't already exist, ask
# once before kicking off install-launchd.sh. Default Y accepts and runs
# the installer; anything else sets LAUNCHD_DECLINED_AT_PROMPT=1 so the
# closing banner uses prompt-aware copy. Non-tty installs (CI, piped) and
# non-macOS users skip this entirely.
#
# COACH_LAUNCHD_PROMPT_DRY_RUN=1 lets tests exercise the prompt without
# spawning a real install-launchd.sh (which would register an actual
# daily job on the dev machine). The dry-run path prints a sentinel
# string the test asserts on.
#
# LAUNCHAGENTS_DIR matches the override used by the launchd-recovery
# block earlier in this script — tests stage fixtures in a tmp dir.

if [[ "$LAUNCHD" == "0" && "$NO_LAUNCHD" == "0" ]] && [[ -t 0 ]] \
   && [[ "$(_coach_uname)" == "Darwin" ]]; then
  _LA_DIR_PROMPT="${LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
  if [[ ! -e "$_LA_DIR_PROMPT/com.local.claude-coach.plist" ]]; then
    printf "\n"
    bold "Register daily Coach insights cron?"
    note "Coach insights runs daily at 04:00 local — analyzes your last 24h"
    note "of transcripts so the profile + watch-list stay fresh."
    # Exact match only — mixed-case like 'Yes' or 'YeS' falls through to
    # decline. Most users hit Enter (the default) anyway; users typing a
    # deliberate reply use the canonical 'y' or 'n' shown in the prompt.
    printf "  Register now? [Y/n] "
    read -r LAUNCHD_REPLY
    case "$LAUNCHD_REPLY" in
      ""|y|Y|yes|YES) LAUNCHD=1 ;;
      *) NO_LAUNCHD=1; LAUNCHD_DECLINED_AT_PROMPT=1 ;;
    esac
  fi
fi

# --- Optional: seed the profile from recent transcripts ---------------------

SEEDED=0
if [[ "$SEED" == "1" ]]; then
  bold "Seeding profile (--seed)"
  if [[ -d "$HOME/.claude/projects" ]]; then
    note "running insights.sh 7d against $HOME/.claude/projects (may take ~30s)…"
    if "$CLAUDE_DIR/coach/bin/insights.sh" 7d 2>&1 | tail -4; then
      ok "profile seeded — run '/coach status' inside Claude Code to see it"
      SEEDED=1
    else
      warn "seed run didn't complete cleanly — non-fatal, you can run ~/.claude/coach/bin/insights.sh 7d manually later"
    fi
  else
    warn "no $HOME/.claude/projects dir yet — skipping seed (no transcripts to analyze)"
    note "open Claude Code at least once, use it a bit, then re-run with --seed"
  fi
fi

# --- Optional: register the macOS daily launchd job -------------------------

LAUNCHD_REGISTERED=0
if [[ "$LAUNCHD" == "1" ]]; then
  bold "Registering daily Coach insights cron (launchd)"
  if [[ "${COACH_LAUNCHD_PROMPT_DRY_RUN:-0}" == "1" ]]; then
    # Tests assert on this sentinel string; never run the real installer.
    ok "would run install-launchd.sh (dry-run for tests)"
    LAUNCHD_REGISTERED=1
  else
    if [[ -x "$BUNDLE_DIR/install-launchd.sh" ]]; then
      if "$BUNDLE_DIR/install-launchd.sh" 2>&1 | sed 's/^/  /'; then
        ok "launchd job registered"
        LAUNCHD_REGISTERED=1
      else
        warn "install-launchd.sh did not complete cleanly — non-fatal, run it manually later"
      fi
    else
      warn "install-launchd.sh missing or not executable at $BUNDLE_DIR — skipping"
    fi
  fi
fi

# --- Done --------------------------------------------------------------------

bold "Installed. Coach Claw is now active."

# Seed-step copy depends on the flag the user passed (or didn't).
# Decline-at-prompt wins over generic NO_SEED so the user sees prompt-aware copy.
if [[ "$SEEDED" == "1" ]]; then
  SEED_LINE="profile seeded from the last 7 days of transcripts."
elif [[ "$SEED_DECLINED_AT_PROMPT" == "1" ]]; then
  SEED_LINE="skipped at prompt; you can seed later: ~/.claude/coach/bin/insights.sh 7d"
elif [[ "$NO_SEED" == "1" ]]; then
  SEED_LINE="--no-seed honored; to seed later: ~/.claude/coach/bin/insights.sh 7d"
else
  SEED_LINE="empty profile (no --seed). Re-run with --seed to bootstrap, or just use Claude Code — the daily cron will fill it in."
fi

# Launchd-step copy branches on whether we registered, the user declined at the
# prompt, or neither (default static command for non-prompt installs).
if [[ "$LAUNCHD_REGISTERED" == "1" ]]; then
  LAUNCHD_STEP="macOS launchd job registered — running now, then daily at 04:00 local.
        Tail the log:  tail -f /tmp/claude-coach.log"
elif [[ "$LAUNCHD_DECLINED_AT_PROMPT" == "1" ]]; then
  LAUNCHD_STEP="macOS launchd: skipped at prompt; you can register later: ./install-launchd.sh
        Linux:  README.md → Install → step 3"
else
  LAUNCHD_STEP="macOS:  npx @rm0nroe/coach-claw@latest launchd
        Linux:  README.md → Install → step 3"
fi
cat <<EOF

What's next:
  1. Restart Claude Code (or open a new session) — the hooks need to load.

  2. Send any prompt. Watch the bottom-right statusline:
        ◆ Ⅰ 1000 Drafter
     That's your level + ELO + rank name. It updates as you ship code.

  3. Customize the look any time (theme also changes rank names + celebration banners):
        Inside Claude Code:
          /config preview            (see all 4 variants × 12 themes)
          /config theme ocean        (try a different ladder)
          /config statusline pips    (try a different statusline shape)
        From the terminal — same backing file:
          npx @rm0nroe/coach-claw@latest config wizard         (interactive)
          npx @rm0nroe/coach-claw@latest config set --theme ocean

  4. Schedule daily auto-analysis:
        $LAUNCHD_STEP

  5. Other slash commands:  /coach status   /coach off | on   /coach uninstall

  Seed: $SEED_LINE

See README.md for design rationale, feature docs, and troubleshooting.
EOF
