from __future__ import annotations

import io
import json

import pytest

from slack_sender import commands, notify
from slack_sender.commands import SlackSession, SlashCommand


def test_chat_command_uses_matching_session_and_separate_command_text(monkeypatch, capsys):
    requests = []

    def fake_urlopen(request, *, timeout):
        requests.append(request)
        assert timeout == commands.COMMAND_TIMEOUT_SEC
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(commands, "urlopen", fake_urlopen)
    sessions = {"T1": SlackSession("xoxc-one", "xoxd-one")}

    assert commands.send_commands(sessions, [SlashCommand("T1", "C1", "/example action now")]) == 0

    request = requests[0]
    assert request.full_url == "https://slack.com/api/chat.command"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer xoxc-one"
    assert request.get_header("Cookie") == "d=xoxd-one"
    assert json.loads(request.data) == {
        "channel": "C1", "command": "/example", "text": "action now"
    }
    assert "submitted: channel=C1" in capsys.readouterr().out


def test_chat_command_reports_api_failure_without_exposing_session(monkeypatch, capsys):
    monkeypatch.setattr(
        commands,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(b'{"ok":false,"error":"invalid_auth"}'),
    )

    assert commands.send_commands(
        {"T1": SlackSession("xoxc-secret", "xoxd-secret")},
        [SlashCommand("T1", "C1", "/example")],
    ) == 1

    output = capsys.readouterr()
    assert "invalid_auth" in output.err
    assert "secret" not in output.err + output.out


def test_missing_session_fails_without_request(monkeypatch, capsys):
    monkeypatch.setattr(
        commands, "urlopen", lambda *args, **kwargs: pytest.fail("unexpected request")
    )

    assert commands.send_commands({}, [SlashCommand("T1", "C1", "/example")]) == 1
    assert "team=T1" in capsys.readouterr().err


def test_main_loads_command_session(monkeypatch):
    cfg = {
        "commands": {"action": [{"team": "T1", "channel": "C1", "command": "/example"}]},
        "slack_command_sessions": {"T1": {"token": "xoxc-one", "cookie": "xoxd-one"}},
    }
    sent = []
    monkeypatch.setattr(notify, "load_config", lambda: cfg)
    monkeypatch.setattr(
        notify,
        "send_commands",
        lambda sessions, items: sent.append((sessions, items)) or 0,
    )
    monkeypatch.setattr("sys.argv", ["slack-sender", "action"])

    assert notify.main() == 0
    assert sent == [
        ({"T1": SlackSession("xoxc-one", "xoxd-one")}, [SlashCommand("T1", "C1", "/example")])
    ]
