from __future__ import annotations

import plistlib
import sys

import pytest

from slack_sender import setup as setup_mod
from slack_sender.setup import LABEL, PLIST, PLIST_PATH, SERVICE_TARGET, SetupError


class FakeSudo:
    """Record `_sudo` calls and fail for a specified argument."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.ignored: list[list[str]] = []
        self._fail_on = fail_on

    def __call__(self, args: list[str], *, ignore_failure: bool = False) -> None:
        self.calls.append(args)
        if ignore_failure:
            self.ignored.append(args)
        if self._fail_on and self._fail_on in args:
            raise SetupError(f"`sudo {' '.join(args)}` failed with 1")


class TestPlist:
    def test_is_valid_plist_running_ipconfig_setverbose(self):
        # Arrange / Act
        parsed = plistlib.loads(PLIST.encode())

        # Assert
        assert parsed["Label"] == LABEL
        assert parsed["ProgramArguments"] == [
            "/usr/sbin/ipconfig",
            "setverbose",
            "1",
        ]
        assert parsed["RunAtLoad"] is True


class TestInstall:
    def test_installs_the_plist_as_root_and_bootstraps_it(self, monkeypatch):
        # Arrange
        sudo = FakeSudo()
        monkeypatch.setattr(setup_mod, "_sudo", sudo)

        # Act
        setup_mod.install()

        # Assert
        install_cmd, bootout, bootstrap = sudo.calls
        assert install_cmd[:7] == [
            "install", "-o", "root", "-g", "wheel", "-m", "644",
        ]
        assert install_cmd[-1] == str(PLIST_PATH)
        assert bootout == ["launchctl", "bootout", SERVICE_TARGET]
        assert bootstrap == ["launchctl", "bootstrap", "system", str(PLIST_PATH)]

    def test_tolerates_bootout_failing_when_not_yet_loaded(self, monkeypatch):
        # Arrange
        sudo = FakeSudo()
        monkeypatch.setattr(setup_mod, "_sudo", sudo)

        # Act
        setup_mod.install()

        # Assert
        assert sudo.ignored == [["launchctl", "bootout", SERVICE_TARGET]]

    def test_stops_when_the_plist_cannot_be_installed(self, monkeypatch):
        # Arrange
        sudo = FakeSudo(fail_on="install")
        monkeypatch.setattr(setup_mod, "_sudo", sudo)

        # Act / Assert
        with pytest.raises(SetupError):
            setup_mod.install()
        assert len(sudo.calls) == 1


class TestUninstall:
    def test_boots_out_the_service_and_removes_the_plist(self, monkeypatch):
        # Arrange
        sudo = FakeSudo()
        monkeypatch.setattr(setup_mod, "_sudo", sudo)

        # Act
        setup_mod.uninstall()

        # Assert
        assert sudo.calls == [
            ["launchctl", "bootout", SERVICE_TARGET],
            ["rm", "-f", str(PLIST_PATH)],
        ]


class TestReportSsid:
    def test_prints_the_detected_ssid(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setattr(setup_mod, "current_ssid", lambda: "network-a")

        # Act
        code = setup_mod.report_ssid()

        # Assert
        assert code == 0
        assert "ssid: network-a" in capsys.readouterr().out

    def test_reports_a_disconnected_network(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setattr(setup_mod, "current_ssid", lambda: None)

        # Act
        code = setup_mod.report_ssid()

        # Assert
        assert code == 0
        assert "wi-fi not connected" in capsys.readouterr().out

    def test_fails_when_the_ssid_is_still_hidden(self, monkeypatch, capsys):
        # Arrange
        def hidden():
            raise setup_mod.SsidUnavailableError("SSID is hidden by macOS")

        monkeypatch.setattr(setup_mod, "current_ssid", hidden)

        # Act
        code = setup_mod.report_ssid()

        # Assert
        assert code == 1
        assert "hidden" in capsys.readouterr().err


class TestMain:
    def test_installs_and_reports_the_ssid_by_default(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setattr(sys, "argv", ["slack-sender-setup"])
        monkeypatch.setattr(setup_mod, "install", lambda: None)
        monkeypatch.setattr(setup_mod, "current_ssid", lambda: "network-a")

        # Act
        code = setup_mod.main()

        # Assert
        assert code == 0
        out = capsys.readouterr().out
        assert str(PLIST_PATH) in out
        assert "ssid: network-a" in out

    def test_uninstall_skips_the_ssid_check(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setattr(sys, "argv", ["slack-sender-setup", "--uninstall"])
        monkeypatch.setattr(setup_mod, "uninstall", lambda: None)
        monkeypatch.setattr(setup_mod, "current_ssid", unexpected_ssid_read)

        # Act
        code = setup_mod.main()

        # Assert
        assert code == 0
        assert "removed" in capsys.readouterr().out

    def test_refuses_to_run_outside_macos(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setattr(sys, "argv", ["slack-sender-setup"])
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(setup_mod, "install", unexpected_install)

        # Act
        code = setup_mod.main()

        # Assert
        assert code == 1
        assert "only on macOS" in capsys.readouterr().err


def unexpected_ssid_read():
    raise AssertionError("the ssid must not be read while uninstalling")


def unexpected_install():
    raise AssertionError("nothing must be installed outside macOS")
