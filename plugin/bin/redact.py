#!/usr/bin/env python3
"""
Transcript redactor — stdin → stdout.

Runs BEFORE the deterministic cron analyzer (`analyze.py` invoked by
`insights.sh`) reads any transcript byte. P0 privacy gate: strips known
credential shapes out of transcript content so they cannot be echoed into
the coach profile and subsequently injected as additionalContext on every
SessionStart.

The on-demand `/coach-insights` skill does NOT call `redact.py` — that
path defers the analytical step to Claude Code's built-in `/insights`,
which is an Anthropic-side LLM step the user is already authorized for
by virtue of running Claude Code. Coach still never writes raw
transcript content to `profile.yaml` on either path.

Usage:
    cat transcript.jsonl | redact.py > redacted.jsonl

This is deliberately conservative — it over-redacts rather than miss a
secret. False positives are fine; leaked keys are not.
"""
from __future__ import annotations

import re
import sys

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Provider-specific API key shapes
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED:anthropic-key]"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "[REDACTED:openai-key]"),
    # Stripe live + test secret keys (underscore not hyphen — the openai
    # `sk-` rule above will not match these).
    (re.compile(r"sk_live_[A-Za-z0-9]{24,}"), "[REDACTED:stripe-live-key]"),
    (re.compile(r"sk_test_[A-Za-z0-9]{24,}"), "[REDACTED:stripe-test-key]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-access-key]"),
    (re.compile(r"ASIA[0-9A-Z]{16}"), "[REDACTED:aws-sts-key]"),
    (re.compile(r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}", re.IGNORECASE),
     "aws_secret_access_key=[REDACTED]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED:github-token]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), "[REDACTED:github-pat]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack-token]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[REDACTED:google-api-key]"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-]{50,}"), "[REDACTED:google-oauth]"),
    # Hugging Face user access tokens (start with `hf_`, ~37 chars).
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[REDACTED:huggingface-token]"),
    # npm automation/publish tokens.
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "[REDACTED:npm-token]"),

    # Generic bearer tokens and authorization headers
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"Authorization:\s*[A-Za-z]+\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
     "Authorization: [REDACTED]"),

    # Private keys (PEM blocks — collapse the whole block)
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED:private-key-block]"),

    # JWT-shaped tokens (three base64url segments separated by dots)
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
     "[REDACTED:jwt]"),

    # Hex-form secrets that look like 32+ hex chars on their own
    (re.compile(r"\b[a-fA-F0-9]{40,}\b"), "[REDACTED:hex-secret?]"),

    # .env-style assignments for suspicious key names
    (re.compile(
        r"(?mi)^\s*(\w*(?:SECRET|TOKEN|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDENTIAL)\w*)"
        r"\s*[:=]\s*[\"']?([^\"'\n\s]{8,})[\"']?"),
     r"\1=[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, replacement in PATTERNS:
        text = pat.sub(replacement, text)
    return text


def main() -> None:
    data = sys.stdin.read()
    sys.stdout.write(redact(data))


if __name__ == "__main__":
    main()
