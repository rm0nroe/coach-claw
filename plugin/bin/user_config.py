"""Read/write `~/.claude/coach/.user_config.json` — the operator-tunable
settings the `/config` slash command edits.

Schema (v1):
    {
      "schema_version": 1,
      "statusline_variant": one of VALID_VARIANTS (see below),
      "theme":             one of VALID_THEMES   (see below),
      "elo_min":           int (default 1000, must be < elo_max),
      "elo_max":           int (default 2800, must be > elo_min)
    }

The valid sets are defined as constants below — they are the single
source of truth, consumed by `/config` validation and the statusline
preview. Don't duplicate the lists into this docstring; they will rot.

Missing file → defaults. Unknown keys → ignored. Invalid values →
fall back to defaults for that field. Reads never raise.

Writes are atomic (tempfile + os.replace) but NOT locked — `/config`
is interactive + slow-path, no concurrency concern with `stats.py`
which only reads.

Path resolution: by default the file lives at
`~/.claude/coach/.user_config.json`. Set `COACH_CONFIG_DIR=/some/dir` to
override the directory (the file name `.user_config.json` is fixed).
This is what lets `coach/bin/configure.py` invoked via `npx coach-claw
config` honor a custom `CLAUDE_DIR` install — the npm wrapper exports
`COACH_CONFIG_DIR` to match. Resolution happens at every read/write so
tests can monkeypatch `COACH_CONFIG_DIR` via `monkeypatch.setenv`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from coach_paths import resolve_coach_dir


def _resolve_config_path() -> Path:
    """Return the path to .user_config.json. Delegates to
    `coach_paths.resolve_coach_dir()` so the COACH_CONFIG_DIR contract
    is enforced in exactly one place. Resolved per-call so the env var
    can be set at test time or by the npm wrapper before the Python
    entry point reads it."""
    return resolve_coach_dir() / ".user_config.json"


def __getattr__(name):
    """Module-level __getattr__ (PEP 562). Lets external code read
    `user_config.CONFIG_PATH` and get a fresh, env-aware path. Internal
    code should call `_resolve_config_path()` directly so behavior is
    explicit at the call site."""
    if name == "CONFIG_PATH":
        return _resolve_config_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

DEFAULTS: dict = {
    "schema_version": 1,
    "statusline_variant": "crystal",
    "theme": "craft",
    "elo_min": 1000,
    "elo_max": 2800,
}

VALID_VARIANTS = {"crystal", "pips", "bracket", "slash", "forge"}
VALID_THEMES = {
    # abstract themes
    "craft", "forge", "cosmic", "ocean",
    # pop-culture-inspired (fan-themed; see themes.py docstring on brand safety)
    "skyrim", "marvel", "dc", "finalfantasy",
    "military", "lotr", "starwars", "hacker",
}


def load() -> dict:
    """Return a complete config dict. Always populates every key — caller
    can index without `.get()`."""
    cfg = dict(DEFAULTS)
    config_path = _resolve_config_path()
    try:
        if config_path.exists():
            raw = json.loads(config_path.read_text())
            if isinstance(raw, dict):
                _coerce_into(cfg, raw)
    except Exception:
        pass
    return cfg


def _coerce_into(cfg: dict, raw: dict) -> None:
    """Apply each valid field from `raw` into `cfg`. Invalid values stay
    at their default — never raises."""
    v = raw.get("statusline_variant")
    if isinstance(v, str) and v in VALID_VARIANTS:
        cfg["statusline_variant"] = v
    t = raw.get("theme")
    if isinstance(t, str) and t in VALID_THEMES:
        cfg["theme"] = t
    emin = raw.get("elo_min")
    emax = raw.get("elo_max")
    if isinstance(emin, int) and isinstance(emax, int) and 0 < emin < emax:
        cfg["elo_min"] = emin
        cfg["elo_max"] = emax


def save(cfg: dict) -> None:
    """Atomic write. Validates against schema before persisting; raises
    ValueError on invalid input so `/config` can show a clear error."""
    validated = dict(DEFAULTS)
    _coerce_into(validated, cfg)
    # If the caller passed an unknown variant/theme, _coerce_into silently
    # kept the default. Detect that and complain so /config can surface
    # which key was rejected.
    for key in ("statusline_variant", "theme"):
        if key in cfg and cfg[key] != validated[key]:
            valid = (
                VALID_VARIANTS if key == "statusline_variant" else VALID_THEMES
            )
            raise ValueError(
                f"unknown {key} {cfg[key]!r}; valid: {sorted(valid)}"
            )
    if "elo_min" in cfg or "elo_max" in cfg:
        emin = cfg.get("elo_min", validated["elo_min"])
        emax = cfg.get("elo_max", validated["elo_max"])
        if not (isinstance(emin, int) and isinstance(emax, int) and 0 < emin < emax):
            raise ValueError(
                f"elo_min ({emin}) must be a positive int less than "
                f"elo_max ({emax})"
            )

    config_path = _resolve_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="." + config_path.name + ".",
        suffix=".tmp",
        dir=str(config_path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(validated, indent=2, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, config_path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# --- Convenience accessors used by stats.py / variants render path ------

def get_variant() -> str:
    return load()["statusline_variant"]


def get_theme() -> str:
    return load()["theme"]


def get_elo_range() -> tuple[int, int]:
    cfg = load()
    return cfg["elo_min"], cfg["elo_max"]


def update(**kwargs) -> dict:
    """Merge updates into the existing config and persist. Returns the
    new config. Raises ValueError on invalid input."""
    cfg = load()
    cfg.update(kwargs)
    save(cfg)
    return cfg
