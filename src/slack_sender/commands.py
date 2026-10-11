"""Submit slash commands through Slack's private chat.command API."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ssid import SsidCondition, parse_exclude_patterns, parse_patterns

COMMAND_TIMEOUT_SEC = 20
COMMAND_URL = "https://slack.com/api/chat.command"


@dataclass(frozen=True)
class SlackSession:
    token: str
    cookie: str


@dataclass(frozen=True)
class SlashCommand:
    team: str
    channel: str
    command: str
    ssid: SsidCondition = field(default_factory=SsidCondition)


def load_sessions(cfg: dict) -> dict[str, SlackSession]:
    raw = cfg.get("slack_command_sessions", {})
    if not isinstance(raw, dict):
        raise ValueError("config.json: slack_command_sessions must be an object")

    sessions = {}
    for team, value in raw.items():
        path = f"slack_command_sessions.{team}"
        if not isinstance(team, str) or not re.fullmatch(r"T[A-Z0-9]+", team):
            raise ValueError(f"config.json: {path} must use a Slack team ID")
        if not isinstance(value, dict):
            raise ValueError(f"config.json: {path} must be an object")
        token, cookie = value.get("token"), value.get("cookie")
        if not isinstance(token, str) or not token.startswith("xoxc-"):
            raise ValueError(f"config.json: {path}.token must be an xoxc token")
        if not isinstance(cookie, str) or not cookie.startswith("xoxd-"):
            raise ValueError(f"config.json: {path}.cookie must be an xoxd cookie")
        sessions[team] = SlackSession(token, cookie)
    return sessions


def parse_commands(cfg: dict, key: str) -> list[SlashCommand]:
    section = cfg.get("commands", {})
    if not isinstance(section, dict):
        raise ValueError("config.json: commands must be an object")
    entries = section.get(key, [])
    if not isinstance(entries, list):
        raise ValueError(f"config.json: commands.{key} must be a list")

    commands: list[SlashCommand] = []
    for i, entry in enumerate(entries):
        path = f"commands.{key}[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"config.json: {path} must be an object")
        team = entry.get("team")
        channel = entry.get("channel")
        command = entry.get("command")
        if not isinstance(team, str) or not re.fullmatch(r"T[A-Z0-9]+", team):
            raise ValueError(f"config.json: {path}.team must be a Slack team ID")
        if not isinstance(channel, str) or not re.fullmatch(r"[CDG][A-Z0-9]+", channel):
            raise ValueError(f"config.json: {path}.channel must be a Slack conversation ID")
        validate_command(command, f"{path}.command")
        commands.append(
            SlashCommand(
                team=team,
                channel=channel,
                command=command,
                ssid=SsidCondition(
                    include=parse_patterns(entry.get("ssid"), f"{path}.ssid"),
                    exclude=parse_exclude_patterns(entry.get("not_ssid"), f"{path}.not_ssid"),
                ),
            )
        )
    return commands


def validate_command(value: object, path: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) < 2
        or value[1].isspace()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError(f"config.json: {path} must be a single-line slash command")


def preview_commands(commands: list[SlashCommand]) -> int:
    for item in commands:
        print(
            f"dry-run: team={item.team} channel={item.channel} "
            f"command={item.command}"
        )
    return 0


def send_commands(sessions: dict[str, SlackSession], commands: list[SlashCommand]) -> int:
    failures = 0
    for item in commands:
        session = sessions.get(item.team)
        if session is None:
            failures += 1
            print(
                f"error: no slack_command_sessions entry for team={item.team}",
                file=sys.stderr,
            )
            continue

        name, _, command_text = item.command.partition(" ")
        payload = {"channel": item.channel, "command": name}
        if command_text:
            payload["text"] = command_text
        request = Request(
            COMMAND_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {session.token}",
                "Cookie": f"d={session.cookie}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=COMMAND_TIMEOUT_SEC) as response:
                result = json.load(response)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError(
                    result.get("error", "invalid API response")
                    if isinstance(result, dict)
                    else "invalid API response"
                )
            print(f"submitted: channel={item.channel} command={item.command}")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            failures += 1
            detail = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else str(exc)
            print(f"error: command in {item.channel}: {detail}", file=sys.stderr)
    return failures
