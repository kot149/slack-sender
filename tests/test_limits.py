"""Tests for the cooldown and rate limit."""
from __future__ import annotations

import pytest

from slack_sender import notify
from slack_sender.limits import Limits, acquire, parse_limits, state_file


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_cooldown_refuses_the_same_key(config):
    limits = Limits(cooldown_sec=60, max_runs=0)

    assert acquire(limits, "a", config, now=1000) is None
    assert "cooldown" in acquire(limits, "a", config, now=1030)
    assert acquire(limits, "b", config, now=1030) is None
    assert acquire(limits, "a", config, now=1061) is None


def test_rate_limit_counts_every_key_in_the_window(config):
    limits = Limits(cooldown_sec=0, max_runs=2, window_sec=100)

    assert acquire(limits, "a", config, now=1000) is None
    assert acquire(limits, "b", config, now=1010) is None
    assert "rate limit" in acquire(limits, "c", config, now=1020)
    assert acquire(limits, "c", config, now=1101) is None


def test_refused_runs_are_not_recorded(config):
    limits = Limits(cooldown_sec=60, max_runs=0)
    acquire(limits, "a", config, now=1000)

    acquire(limits, "a", config, now=1050)

    assert acquire(limits, "a", config, now=1061) is None


def test_each_config_file_has_its_own_state(tmp_path, config):
    other = tmp_path / "test.json"
    other.write_text("{}", encoding="utf-8")
    limits = Limits(cooldown_sec=60, max_runs=0)

    assert acquire(limits, "a", config, now=1000) is None
    assert acquire(limits, "a", other, now=1000) is None
    assert state_file(config) != state_file(other)


def test_zero_disables_both_limits(config):
    limits = Limits(cooldown_sec=0, max_runs=0)

    for i in range(20):
        assert acquire(limits, "a", config, now=1000 + i) is None


def test_parse_limits_uses_defaults_and_overrides():
    assert parse_limits({}) == Limits()
    assert parse_limits({"limits": {"cooldown_sec": 5}}) == Limits(cooldown_sec=5)


@pytest.mark.parametrize("value", [-1, 1.5, "60", True])
def test_parse_limits_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="limits.cooldown_sec"):
        parse_limits({"limits": {"cooldown_sec": value}})


def test_main_skips_a_repeated_key_without_sending(monkeypatch, capsys, config):
    cfg = {"slack_bot_tokens": {"default": "xoxb-test"}, "posts": {"a": [{"channel": "C1", "message": "m"}]}}
    sent = []
    monkeypatch.setattr(notify, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(notify, "send_all", lambda tokens, posts: sent.append(posts) or 0)
    monkeypatch.setattr("sys.argv", ["slack-sender", "a", "--config", str(config)])

    assert notify.main() == 0
    assert notify.main() == 1
    assert len(sent) == 1
    assert "skipped: cooldown: key=a" in capsys.readouterr().err


def test_dry_run_is_not_limited(monkeypatch, config):
    cfg = {"slack_bot_tokens": {"default": "xoxb-test"}, "posts": {"a": [{"channel": "C1", "message": "m"}]}}
    monkeypatch.setattr(notify, "load_config", lambda path=None: cfg)
    monkeypatch.setattr("sys.argv", ["slack-sender", "a", "--dry-run", "--config", str(config)])

    assert notify.main() == 0
    assert notify.main() == 0
    assert not state_file(config).exists()
