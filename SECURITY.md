# Security Policy

## Supported versions

Coach Claw is a single-maintainer project. Only `main` is supported —
the latest tagged release is what receives security fixes. Older tags
remain available but will not be patched.

## Reporting a vulnerability

**Please do not open public GitHub issues for security reports.**

Use one of these private channels:

- **GitHub Security Advisory** (preferred):
  [Open a private advisory](https://github.com/rm0nroe/coach-claw/security/advisories/new).
  GitHub-hosted, encrypted, and lets us collaborate on a fix before
  public disclosure.
- **Email:** `36248507+rm0nroe@users.noreply.github.com`. Slower path,
  use only if the GitHub Security Advisory flow is unavailable.

## What to include

- A clear description of the issue and its impact.
- Steps to reproduce (a minimal repro, scripted if possible).
- Affected version / commit SHA.
- Any suggested fix or mitigation.

## What to expect

- **Acknowledgement** within 7 days.
- **Triage + assessment** within 14 days.
- **Fix or mitigation** as soon as practical, prioritized by severity.
  Coach is a small, hobby-scale project — there is no on-call rotation.
- **Coordinated disclosure**: I'll work with you on a public-disclosure
  timeline once a fix is available. Credit by name in the release notes
  and advisory unless you'd prefer otherwise.

## Scope

In-scope: anything in this repo that runs on a user's machine —
hooks, `coach/bin/*`, the installer, slash-command skills, tests.

Out-of-scope: vulnerabilities in upstream dependencies (`pyyaml`,
Python itself, Claude Code), GitHub infrastructure, or third-party
services. Report those to the relevant project.

## Privacy considerations

Coach Claw is a local-only coaching layer that reads your Claude Code
session transcripts. The deterministic cron path performs no network
I/O; the weekly path invokes `claude -p "/insights"` only as a
side-effect to refresh local sidecar files. If you find evidence of
unexpected data exfiltration, that is in-scope — please report
privately via the channels above.
