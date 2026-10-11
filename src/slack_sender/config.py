"""Shared config.json loader. Resolves relative to current working directory.

config.json is parsed as JSON5, so comments (`//`, `/* */`) and trailing commas
are accepted in addition to plain JSON.
"""
from __future__ import annotations

from pathlib import Path

import json5


def config_path() -> Path:
    return Path.cwd() / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.json to config.json."
        )
    try:
        config = json5.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON5: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"{path} must contain a JSON object at the top level.")
    return config
