"""Submit slash commands through the logged-in Slack desktop app on macOS."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field

from .ssid import SsidCondition, parse_exclude_patterns, parse_patterns

COMMAND_TIMEOUT_SEC = 20

_SUBMIT_SCRIPT = """
on run
    delay 0.5
    tell application "Slack" to activate
    tell application "System Events"
        set frontAppName to name of (first application process whose frontmost is true)
        if frontAppName is not "Slack" then
            error "Slack is not the frontmost application"
        end if
        keystroke "v" using command down
        delay 0.5
        keystroke return
        delay 0.5
        keystroke return using command down
    end tell
end run
"""


@dataclass(frozen=True)
class SlashCommand:
    team: str
    channel: str
    command: str
    ssid: SsidCondition = field(default_factory=SsidCondition)


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
            f"dry-run: desktop team={item.team} channel={item.channel} "
            f"command={item.command}"
        )
    return 0


def send_commands(commands: list[SlashCommand]) -> int:
    if sys.platform != "darwin":
        print("error: Slack desktop commands require macOS", file=sys.stderr)
        return len(commands)

    failures = 0
    for item in commands:
        link = f"slack://channel?team={item.team}&id={item.channel}&tab=messages"
        try:
            subprocess.run(
                ["pbcopy"],
                input=item.command,
                capture_output=True,
                text=True,
                check=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
            subprocess.run(
                ["open", link],
                capture_output=True,
                text=True,
                check=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
            subprocess.run(
                ["osascript", "-e", _SUBMIT_SCRIPT],
                capture_output=True,
                text=True,
                check=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
            print(f"submitted: desktop channel={item.channel} command={item.command}")
        except (OSError, subprocess.SubprocessError) as exc:
            failures += 1
            detail = (
                (exc.stderr or "").strip() or str(exc)
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            print(f"error: desktop command in {item.channel}: {detail}", file=sys.stderr)
    return failures
