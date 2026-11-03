from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Keep send limit state out of the real home directory."""
    monkeypatch.setenv("SLACK_SENDER_STATE_DIR", str(tmp_path / "state"))
