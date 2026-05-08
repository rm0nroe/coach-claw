#!/usr/bin/env bash
# Optional add-on: register a macOS launchd job that runs the deterministic
# Coach insights pass daily at 04:00 local time.
#
# The scheduled runner (bin/insights.sh) does NOT go through the claude CLI
# — it invokes the structural analyzer + merge script directly, which is
# faster and more reliable than spawning `claude -p` for the LLM-driven
# `/coach-insights` skill on a cron schedule.
#
# Requires the main coach installer (./install.sh) to have been run first.
# Idempotent — re-run to refresh the plist.
#
# To remove: see commands at the bottom of this script's stdout.

set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_PLIST="$BUNDLE_DIR/launchd/com.local.claude-coach.plist.template"
TEMPLATE_WRAPPER="$BUNDLE_DIR/launchd/run-insights.sh"

PLIST_DST="$HOME/Library/LaunchAgents/com.local.claude-coach.plist"
WRAPPER_DST="$HOME/.claude/coach/bin/run-insights.sh"
LABEL="com.local.claude-coach"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m  OK: %s\033[0m\n" "$*"; }
die()  { printf "\033[31m  ERROR: %s\033[0m\n" "$*"; exit 1; }

# --- Preflight ---------------------------------------------------------------

bold "Preflight"

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "install-launchd.sh is macOS-only (launchd doesn't exist on Linux). On Linux, add a crontab entry that runs ~/.claude/coach/bin/insights.sh 1d — see README.md §Schedule."
fi
if ! command -v plutil >/dev/null 2>&1; then
  die "plutil not found (expected on macOS). Are you on a headless VM with XCode tools missing?"
fi
if ! command -v launchctl >/dev/null 2>&1; then
  die "launchctl not found — required to register the job."
fi
if [[ ! -d "$HOME/.claude/coach" ]]; then
  die "$HOME/.claude/coach does not exist. Run ./install.sh first."
fi
if [[ ! -x "$HOME/.claude/coach/bin/insights.sh" ]]; then
  die "$HOME/.claude/coach/bin/insights.sh missing. Re-run ./install.sh."
fi
ok "macOS + launchctl + plutil available"
ok "coach data dir present"
ok "insights.sh present"

# --- Install wrapper + plist ------------------------------------------------

bold "Installing"

mkdir -p "$HOME/.claude/coach/bin" "$HOME/Library/LaunchAgents"

cp "$TEMPLATE_WRAPPER" "$WRAPPER_DST"
chmod +x "$WRAPPER_DST"
ok "wrote $WRAPPER_DST"

sed "s|@HOME@|$HOME|g" "$TEMPLATE_PLIST" > "$PLIST_DST"
plutil -lint "$PLIST_DST" >/dev/null || die "plist failed lint"
ok "wrote $PLIST_DST (validated)"

# --- Load (or reload) -------------------------------------------------------

bold "Loading"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

# Buffer launchctl list into a var before grepping — otherwise `grep -q`
# can exit early, cause SIGPIPE on launchctl, and fail the pipeline under
# `set -euo pipefail` even when the job IS registered.
LAUNCHCTL_LIST="$(launchctl list || true)"
if printf "%s\n" "$LAUNCHCTL_LIST" | grep "$LABEL" >/dev/null 2>&1; then
  ok "registered — running now, then daily at 04:00 local"
else
  die "job did not register; check plist"
fi

# Fire once now so the user has seed data immediately instead of waiting
# until 04:00 tomorrow. kickstart is idempotent and safe to call right
# after load (the daily schedule is already registered).
launchctl kickstart "gui/$(id -u)/$LABEL" 2>/dev/null || true
ok "first run kickstarted — tail /tmp/claude-coach.log to watch progress"

cat <<EOF

Trigger a run again later (useful for testing):
  launchctl kickstart gui/\$(id -u)/$LABEL

Tail the log:
  tail -f /tmp/claude-coach.log

Pause the schedule:
  launchctl unload $PLIST_DST

Remove entirely:
  launchctl unload $PLIST_DST
  rm $PLIST_DST $WRAPPER_DST
EOF
