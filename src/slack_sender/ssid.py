"""Detect Wi-Fi SSIDs and evaluate post conditions on macOS.

Since macOS 14.4, SSIDs are hidden by default: `ipconfig`, `networksetup`,
and `system_profiler` return `<redacted>`. Running `ipconfig setverbose 1`
as root makes the SSID readable until the next reboot. A hidden SSID raises
`SsidUnavailableError`.

The CLI cannot request location permission without an app bundle. Requests
from unbundled processes are discarded before reaching locationd.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase

COMMAND_TIMEOUT_SEC = 5
REDACTED_MARKER = "<redacted>"
DEFAULT_WIFI_DEVICE = "en0"

_SSID_LINE = re.compile(r"^SSID\s*:\s*(?P<ssid>.+)$")
_CURRENT_NETWORK_LINE = re.compile(r"^Current Wi-Fi Network:\s*(?P<ssid>.+)$")


class SsidUnavailableError(RuntimeError):
    """The SSID cannot be read because macOS hides it or a command failed."""


@dataclass(frozen=True)
class SsidCondition:
    """SSID conditions for a post, using `fnmatch` patterns such as `network-*`.

    `include=None` means `ssid` is omitted; an empty tuple means it is
    explicitly empty, so no SSID matches. Omit it or use `"*"` to allow all.
    """

    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether no conditions are set, so the post is always sent."""
        return self.include is None and not self.exclude

    @property
    def matches_nothing(self) -> bool:
        """Whether `ssid` is empty, so no SSID matches."""
        return self.include is not None and not self.include

    @property
    def needs_current_ssid(self) -> bool:
        """Whether evaluation requires detecting the current SSID."""
        return not self.is_empty and not self.matches_nothing

    def matches(self, ssid: str | None) -> bool:
        """Whether `ssid` matches; None means Wi-Fi is disconnected."""
        if self.matches_nothing:
            return False
        if self.is_empty:
            return True
        if ssid is None:
            # A disconnected device cannot match include patterns, but matches exclusion-only conditions.
            return not self.include
        if self.include and not any(fnmatchcase(ssid, p) for p in self.include):
            return False
        return not any(fnmatchcase(ssid, p) for p in self.exclude)


def parse_patterns(value: object, where: str) -> tuple[str, ...] | None:
    """Normalize a string or array of strings to a tuple of SSID patterns.

    An omitted value becomes None; an empty string or array becomes an empty tuple.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, list):
        raise ValueError(
            f"config.json: {where} must be a string or an array of strings"
        )

    for v in value:
        if not isinstance(v, str) or not v:
            raise ValueError(f"config.json: {where} must not contain empty values")
    return tuple(value)


def parse_exclude_patterns(value: object, where: str) -> tuple[str, ...]:
    """Normalize `not_ssid`; omitted and empty values exclude nothing."""
    return parse_patterns(value, where) or ()


def _run(args: list[str]) -> str | None:
    """Run a command and return stdout, or None if it fails."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def wifi_device() -> str:
    """Return the Wi-Fi interface name, such as en0, or the default."""
    out = _run(["networksetup", "-listallhardwareports"])
    if not out:
        return DEFAULT_WIFI_DEVICE

    is_wifi_port = False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            is_wifi_port = line.split(":", 1)[1].strip() == "Wi-Fi"
        elif is_wifi_port and line.startswith("Device:"):
            return line.split(":", 1)[1].strip()
    return DEFAULT_WIFI_DEVICE


def _ssid_from_ipconfig(device: str) -> str | None:
    out = _run(["ipconfig", "getsummary", device])
    if out is None:
        raise SsidUnavailableError(
            f"failed to run `ipconfig getsummary {device}`"
        )
    for line in out.splitlines():
        m = _SSID_LINE.match(line.strip())
        if m:
            return m.group("ssid").strip()
    return None


def _ssid_from_networksetup(device: str) -> str | None:
    out = _run(["networksetup", "-getairportnetwork", device])
    if out is None:
        return None
    for line in out.splitlines():
        m = _CURRENT_NETWORK_LINE.match(line.strip())
        if m:
            return m.group("ssid").strip()
    return None


def current_ssid() -> str | None:
    """Return the current SSID, or None when Wi-Fi is disconnected.

    Raise `SsidUnavailableError` if macOS hides the SSID.
    """
    device = wifi_device()
    ssid = _ssid_from_ipconfig(device) or _ssid_from_networksetup(device)
    if ssid is None:
        return None
    if ssid == REDACTED_MARKER:
        raise SsidUnavailableError(
            "SSID is hidden by macOS. Run `sudo ipconfig setverbose 1` to "
            "unhide it (needed once per boot), or pass --ssid / --ignore-ssid."
        )
    return ssid
