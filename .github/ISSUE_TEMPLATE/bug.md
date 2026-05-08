---
name: Bug report
about: Something the coach is doing wrong (wrong tip, broken install, lost banner, etc.)
title: "[bug] "
labels: bug
---

## What happened

<!-- One or two sentences. What did the coach do, and what did you expect? -->

## Repro

<!-- Smallest sequence of steps to reproduce. If it's a tip-firing issue, include
     what you were doing in Claude Code when the wrong tip fired. -->

1.
2.
3.

## Environment

- OS:                       <!-- macOS / Linux + version -->
- Python:                   <!-- output of `python3 --version` -->
- Claude Code version:      <!-- if relevant -->
- Coach version:            <!-- npm: `npx @rm0nroe/coach-claw --version`; or git: `git -C ~/.claude/coach log -1 --oneline` -->

## Test suite

<!-- Optional but very helpful — does the suite pass on your install? -->

```
cd ~/.claude/coach && python3 -m pytest tests/
```

## Anything else

<!-- Recent lines from ~/.claude/coach/log.ndjson if related (redacted operational
     metadata, not transcript content). Profile.yaml fragments if a tip is
     misclassified. -->
