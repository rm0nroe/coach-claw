# Coach Claw plugin marketplace

Single-purpose Claude Code marketplace that distributes the
[coach-claw](https://github.com/rm0nroe/coach-claw) plugin.

## Install

```
/plugin marketplace add rm0nroe/coach-claw-plugin-marketplace
/plugin install coach-claw@coach-claw-plugins
```

After install, restart Claude Code (or open a new session) so the
plugin's `SessionStart` hook fires and self-installs the statusline.

## What this is

A `git-subdir` marketplace pointing at the `plugin/` directory of the
[`rm0nroe/coach-claw`](https://github.com/rm0nroe/coach-claw)
repo. The plugin code lives in that repo alongside the npm CLI source;
this marketplace exists purely as a discoverable Claude Code entry
point (`/plugin marketplace add …`). Users never see this repo's
contents directly.

Versioning is driven by `plugin/.claude-plugin/plugin.json:version` in
the source repo — bumping that field is what tells users a new release
is available via `/plugin update`.

## What this is not

This marketplace does **not** distribute the npm CLI version of Coach
Claw. For that, run:

```
npx @rm0nroe/coach-claw install
```

The two distributions are parallel. Both share `~/.claude/coach/` state
(profile.yaml, banked XP, git history). When both are installed, the
plugin defers to the CLI by default; run `/coach-claw:switch` to flip
control to the plugin.

## Channels

- **`main` branch**: stable. Tracks `plugin.json:version` in the source
  repo's default branch.
- **`beta` branch** (planned): pre-release. Will track a `beta` ref or
  branch in the source repo. Add via `/plugin marketplace add
  rm0nroe/coach-claw-plugin-marketplace@beta` once published.

## Updating

Maintainers: this repo's content is generated from
[`rm0nroe/coach-claw/marketplace/`](https://github.com/rm0nroe/coach-claw/tree/main/marketplace).
Edit the source repo, then `make marketplace-publish` (or push the
contents manually). Don't edit this repo directly.
