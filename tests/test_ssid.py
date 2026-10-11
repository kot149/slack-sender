from __future__ import annotations

import pytest

from slack_sender import ssid as ssid_mod
from slack_sender.ssid import (
    REDACTED_MARKER,
    SsidCondition,
    SsidUnavailableError,
    current_ssid,
    parse_exclude_patterns,
    parse_patterns,
    wifi_device,
)


class TestSsidCondition:
    def test_unspecified_condition_matches_any_network(self):
        cond = SsidCondition()

        assert cond.matches("network-a") is True
        assert cond.matches(None) is True

    def test_explicitly_empty_include_matches_nothing(self):
        cond = SsidCondition(include=())

        assert cond.matches_nothing is True
        assert cond.matches("network-a") is False
        assert cond.matches(None) is False

    def test_explicitly_empty_include_needs_no_detection(self):
        assert SsidCondition(include=()).needs_current_ssid is False

    def test_a_real_pattern_needs_detection(self):
        assert SsidCondition(include=("network-*",)).needs_current_ssid is True
        assert SsidCondition(exclude=("network-b",)).needs_current_ssid is True

    def test_star_pattern_matches_any_connected_network(self):
        cond = SsidCondition(include=("*",))

        assert cond.matches("network-a") is True
        assert cond.matches("network-b") is True
        assert cond.matches(None) is False

    def test_include_matches_only_listed_ssid(self):
        cond = SsidCondition(include=("network-a",))

        assert cond.matches("network-a") is True
        assert cond.matches("network-b") is False

    def test_include_supports_wildcard_patterns(self):
        cond = SsidCondition(include=("network-*",))

        assert cond.matches("network-5g") is True
        assert cond.matches("other-free") is False

    def test_include_is_case_sensitive(self):
        cond = SsidCondition(include=("Network-A",))

        assert cond.matches("network-a") is False

    def test_exclude_rejects_listed_ssid(self):
        cond = SsidCondition(exclude=("network-b",))

        assert cond.matches("network-a") is True
        assert cond.matches("network-b") is False

    def test_exclude_wins_over_a_catch_all_include(self):
        cond = SsidCondition(include=("*",), exclude=("network-guest", "other-*"))

        assert cond.matches("network-5g") is True
        assert cond.matches("network-guest") is False
        assert cond.matches("other-free") is False

    def test_exclude_wins_over_include(self):
        cond = SsidCondition(include=("network-*",), exclude=("network-guest",))

        assert cond.matches("network-5g") is True
        assert cond.matches("network-guest") is False

    def test_include_condition_fails_when_wifi_is_disconnected(self):
        assert SsidCondition(include=("network-a",)).matches(None) is False

    def test_exclude_only_condition_holds_when_wifi_is_disconnected(self):
        assert SsidCondition(exclude=("network-b",)).matches(None) is True


class TestParsePatterns:
    def test_returns_none_when_unspecified(self):
        assert parse_patterns(None, "posts.message_a[0].ssid") is None

    def test_wraps_a_single_string(self):
        assert parse_patterns("network-a", "posts.message_a[0].ssid") == ("network-a",)

    def test_keeps_array_order(self):
        assert parse_patterns(["a", "b"], "posts.message_a[0].ssid") == ("a", "b")

    @pytest.mark.parametrize("value", ["", []])
    def test_distinguishes_an_explicitly_empty_value_from_unspecified(self, value):
        assert parse_patterns(value, "posts.message_a[0].ssid") == ()

    @pytest.mark.parametrize("value", [123, {"a": 1}, True])
    def test_rejects_non_string_scalars(self, value):
        with pytest.raises(ValueError, match="must be a string or an array"):
            parse_patterns(value, "posts.message_a[0].ssid")

    @pytest.mark.parametrize("value", [[""], [None], [1]])
    def test_rejects_invalid_array_items(self, value):
        with pytest.raises(ValueError, match="must not contain empty values"):
            parse_patterns(value, "posts.message_a[0].ssid")


class TestParseExcludePatterns:
    def test_returns_empty_tuple_when_unspecified(self):
        assert parse_exclude_patterns(None, "posts.message_a[0].not_ssid") == ()

    def test_keeps_the_given_patterns(self):
        assert parse_exclude_patterns("network-b", "posts.message_a[0].not_ssid") == ("network-b",)

    @pytest.mark.parametrize("value", ["", []])
    def test_treats_an_empty_value_as_no_exclusion(self, value):
        assert parse_exclude_patterns(value, "posts.message_a[0].not_ssid") == ()


class TestRun:
    def test_returns_stdout_for_a_successful_command(self):
        assert ssid_mod._run(["echo", "ok"]) == "ok\n"

    def test_returns_none_for_a_failing_command(self):
        assert ssid_mod._run(["false"]) is None

    def test_returns_none_when_the_command_does_not_exist(self):
        assert ssid_mod._run(["slack_sender-no-such-command"]) is None


class TestWifiDevice:
    def test_picks_the_device_under_the_wifi_hardware_port(self, monkeypatch):
        output = (
            "Hardware Port: Ethernet\nDevice: en5\n\n"
            "Hardware Port: Wi-Fi\nDevice: en1\n"
        )
        monkeypatch.setattr(ssid_mod, "_run", lambda args: output)

        assert wifi_device() == "en1"

    def test_falls_back_to_default_when_command_fails(self, monkeypatch):
        monkeypatch.setattr(ssid_mod, "_run", lambda args: None)

        assert wifi_device() == ssid_mod.DEFAULT_WIFI_DEVICE


class TestCurrentSsid:
    @pytest.fixture(autouse=True)
    def _fixed_device(self, monkeypatch):
        monkeypatch.setattr(ssid_mod, "wifi_device", lambda: "en0")

    def _stub_commands(self, monkeypatch, ipconfig=None, networksetup=None):
        def fake_run(args: list[str]) -> str | None:
            if args[0] == "ipconfig":
                return ipconfig
            return networksetup

        monkeypatch.setattr(ssid_mod, "_run", fake_run)

    def test_reads_ssid_from_ipconfig_summary(self, monkeypatch):
        self._stub_commands(
            monkeypatch, ipconfig="  BSSID : aa:bb\n  SSID : network-a\n"
        )

        assert current_ssid() == "network-a"

    def test_returns_none_when_wifi_is_disconnected(self, monkeypatch):
        self._stub_commands(monkeypatch, ipconfig="  LinkStatusActive : FALSE\n")

        assert current_ssid() is None

    def test_falls_back_to_networksetup_when_ipconfig_has_no_ssid(self, monkeypatch):
        self._stub_commands(
            monkeypatch,
            ipconfig="  LinkStatusActive : TRUE\n",
            networksetup="Current Wi-Fi Network: network-b\n",
        )

        assert current_ssid() == "network-b"

    def test_raises_when_macos_redacts_the_ssid(self, monkeypatch):
        self._stub_commands(monkeypatch, ipconfig=f"  SSID : {REDACTED_MARKER}\n")

        with pytest.raises(SsidUnavailableError, match="setverbose"):
            current_ssid()

    def test_raises_when_ipconfig_cannot_run(self, monkeypatch):
        self._stub_commands(monkeypatch, ipconfig=None)

        with pytest.raises(SsidUnavailableError, match="ipconfig"):
            current_ssid()
