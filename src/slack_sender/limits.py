"""Cooldown and rate limit shared by every run that uses the same config file.

config.json may override the defaults; 0 disables a limit:
{
  "limits": {"cooldown_sec": 60, "max_runs": 10, "window_sec": 3600}
}

State lives in SLACK_SENDER_STATE_DIR, $XDG_STATE_HOME/slack-sender, or
~/.local/state/slack-sender, in one file per resolved config path.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COOLDOWN_SEC = 60
DEFAULT_MAX_RUNS = 10
DEFAULT_WINDOW_SEC = 3600


@dataclass(frozen=True)
class Limits:
    cooldown_sec: int = DEFAULT_COOLDOWN_SEC
    max_runs: int = DEFAULT_MAX_RUNS
    window_sec: int = DEFAULT_WINDOW_SEC


def parse_limits(cfg: dict) -> Limits:
    raw = cfg.get("limits", {})
    if not isinstance(raw, dict):
        raise ValueError("config.json: limits must be an object")
    values = {}
    for name in ("cooldown_sec", "max_runs", "window_sec"):
        if name not in raw:
            continue
        value = raw[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"config.json: limits.{name} must be a non-negative integer")
        values[name] = value
    return Limits(**values)


def state_dir() -> Path:
    if explicit := os.environ.get("SLACK_SENDER_STATE_DIR"):
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "slack-sender"


def state_file(config: Path) -> Path:
    digest = hashlib.sha256(str(config.resolve()).encode()).hexdigest()[:16]
    return state_dir() / f"{digest}.json"


def acquire(limits: Limits, key: str, config: Path, now: float | None = None) -> str | None:
    """Record a run of `key` and return None, or return why the run is refused.

    The run is recorded before sending, so a crash mid-send still counts.
    """
    now = time.time() if now is None else now
    path = state_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read_state(path)
        last = state["last"].get(key)
        if limits.cooldown_sec and last is not None and now - last < limits.cooldown_sec:
            return f"cooldown: key={key} ran {int(now - last)}s ago (limit {limits.cooldown_sec}s)"
        runs = [t for t in state["runs"] if now - t < limits.window_sec]
        if limits.max_runs and limits.window_sec and len(runs) >= limits.max_runs:
            return f"rate limit: {len(runs)} runs in the last {limits.window_sec}s (limit {limits.max_runs})"
        state["runs"] = [*runs, now]
        state["last"][key] = now
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    return None


def _read_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"runs": [], "last": {}}
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(state.get("runs"), list) or not isinstance(state.get("last"), dict):
        raise ValueError(f"{path} has an unexpected format")
    return state
