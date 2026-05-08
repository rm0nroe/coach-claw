## Summary

<!-- One or two sentences on what changed and why. The "why" is the part
     reviewers can't reconstruct from the diff. -->

## Test plan

<!-- Tick what you did. CI runs `python3 -m pytest coach/tests/` on Python 3.8
     + 3.11; both must pass. -->

- [ ] `python3 -m pytest coach/tests/` passes locally
- [ ] Added/updated tests for the changed code path
- [ ] Smoke-tested the affected hook(s) if applicable:
  - `echo '{}' | python3 hooks/coach-session-start.py`
  - `echo '{}' | python3 hooks/coach-user-prompt.py`
- [ ] Re-ran `./install.sh` if installer or hook command shape changed

## BACKLOG / CHANGELOG

<!-- If this closes a BACKLOG item, mention the line. If user-visible, add
     a CHANGELOG.md entry under the next version. -->

- Closes BACKLOG.md:
- CHANGELOG entry: <yes/no/n-a>

## Anything reviewers should focus on

<!-- Race conditions, backwards-compat with on-disk profile.yaml shapes,
     anything that could leak past tests, places you weren't sure about. -->
