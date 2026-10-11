"""Install a LaunchDaemon to make the SSID readable on macOS.

macOS hides the SSID by default. `ipconfig setverbose 1` makes it readable,
but the setting lives only in configd memory and is lost on reboot. The
LaunchDaemon runs the command as root at startup.

Installing under /Library/LaunchDaemons requires root privileges. `sudo`
handles elevation and prompts for the password in the terminal.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from .ssid import SsidUnavailableError, current_ssid

LABEL = "local.ipconfig-setverbose"
PLIST_PATH = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
SERVICE_TARGET = f"system/{LABEL}"

PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/sbin/ipconfig</string>
    <string>setverbose</string>
    <string>1</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
"""


class SetupError(RuntimeError):
    """Installing or removing the LaunchDaemon failed."""


def _sudo(args: list[str], *, ignore_failure: bool = False) -> None:
    """Run a command with `sudo`, passing its output through to the terminal."""
    try:
        subprocess.run(["sudo", *args], check=not ignore_failure)
    except subprocess.CalledProcessError as e:
        raise SetupError(f"`sudo {' '.join(args)}` failed with {e.returncode}") from e
    except OSError as e:
        raise SetupError(f"failed to run `sudo {' '.join(args)}`: {e}") from e


def install() -> None:
    """Install the plist and reload the service immediately."""
    with tempfile.NamedTemporaryFile("w", suffix=".plist") as f:
        f.write(PLIST)
        f.flush()
        _sudo(["install", "-o", "root", "-g", "wheel", "-m", "644", f.name,
               str(PLIST_PATH)])

    # Bootstrap fails for an existing service, so remove it before reloading.
    _sudo(["launchctl", "bootout", SERVICE_TARGET], ignore_failure=True)
    _sudo(["launchctl", "bootstrap", "system", str(PLIST_PATH)])


def uninstall() -> None:
    """Remove the LaunchDaemon and plist; verbose mode lasts until reboot."""
    _sudo(["launchctl", "bootout", SERVICE_TARGET], ignore_failure=True)
    _sudo(["rm", "-f", str(PLIST_PATH)])


def report_ssid() -> int:
    """Check whether the SSID is readable and report the result."""
    try:
        ssid = current_ssid()
    except SsidUnavailableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"ssid: {ssid}" if ssid else "wi-fi not connected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the LaunchDaemon",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("error: this command works only on macOS", file=sys.stderr)
        return 1

    try:
        if args.uninstall:
            uninstall()
        else:
            install()
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.uninstall:
        print(f"removed {PLIST_PATH}")
        return 0

    print(f"installed {PLIST_PATH}")
    return report_ssid()


if __name__ == "__main__":
    sys.exit(main())
