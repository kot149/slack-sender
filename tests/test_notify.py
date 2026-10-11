from __future__ import annotations

import pytest
from slack_sdk.errors import SlackApiError

from slack_sender import notify
from slack_sender.notify import Post, filter_by_ssid, parse_posts, preview_all
from slack_sender.ssid import SsidCondition, SsidUnavailableError


def post(channel: str, *, include=None, exclude=()) -> Post:
    return Post(
        channel=channel,
        message="hello",
        ssid=SsidCondition(include=include, exclude=exclude),
    )


class TestParsePosts:
    def test_post_without_ssid_keys_has_an_empty_condition(self):
        cfg = {"posts": {"message_a": [{"channel": "C1", "message": "m"}]}}

        assert parse_posts(cfg, "message_a")[0].ssid.is_empty is True

    def test_parses_ssid_and_not_ssid_into_the_condition(self):
        cfg = {
            "posts": {
                "message_a": [
                    {
                        "channel": "C1",
                        "message": "m",
                        "ssid": ["network-*", "network-5g"],
                        "not_ssid": "network-guest",
                    }
                ]
            }
        }

        cond = parse_posts(cfg, "message_a")[0].ssid

        assert cond.include == ("network-*", "network-5g")
        assert cond.exclude == ("network-guest",)

    def test_an_empty_ssid_means_the_post_is_never_sent(self):
        cfg = {"posts": {"message_a": [{"channel": "C1", "message": "m", "ssid": ""}]}}

        assert parse_posts(cfg, "message_a")[0].ssid.matches_nothing is True

    @pytest.mark.parametrize("value", ["", []])
    def test_an_empty_not_ssid_excludes_nothing(self, value):
        cfg = {
            "posts": {"message_a": [{"channel": "C1", "message": "m", "not_ssid": value}]}
        }

        cond = parse_posts(cfg, "message_a")[0].ssid

        assert cond.exclude == ()
        assert cond.is_empty is True
        assert cond.matches("any-wifi") is True

    def test_reports_the_offending_index_for_an_invalid_ssid_value(self):
        cfg = {
            "posts": {
                "message_a": [
                    {"channel": "C1", "message": "m"},
                    {"channel": "C2", "message": "m", "ssid": 42},
                ]
            }
        }

        with pytest.raises(ValueError, match=r"posts\.message_a\[1\]\.ssid"):
            parse_posts(cfg, "message_a")


class TestFilterBySsid:
    def test_keeps_only_posts_matching_the_current_ssid(self):
        posts = [
            post("C-network-a", include=("network-a",)),
            post("C-network-b", include=("network-b",)),
            post("C-always"),
        ]

        kept = filter_by_ssid(posts, "network-a")

        assert [p.channel for p in kept] == ["C-network-a", "C-always"]

    def test_reports_skipped_posts_on_stderr(self, capsys):
        filter_by_ssid([post("C-network-b", include=("network-b",))], "network-a")

        assert "skipped: channel=C-network-b" in capsys.readouterr().err

    def test_drops_posts_with_an_empty_ssid_and_explains_why(self, capsys):
        kept = filter_by_ssid([post("C-never", include=())], "network-a")

        assert kept == []
        assert "does not match any network (ssid is empty)" in capsys.readouterr().err

    def test_treats_a_disconnected_network_as_no_ssid(self, capsys):
        posts = [post("C-network-a", include=("network-a",)), post("C-offline")]

        kept = filter_by_ssid(posts, None)

        assert [p.channel for p in kept] == ["C-offline"]
        assert "wi-fi not connected" in capsys.readouterr().err


class TestPreviewAll:
    def test_prints_each_post_without_sending(self, capsys):
        posts = [
            Post(channel="C1", message="Message A", token="secondary"),
            Post(channel="C2", message="hello"),
        ]

        failures = preview_all({"default": "xoxb-1", "secondary": "xoxb-1"}, posts)

        out = capsys.readouterr().out
        assert failures == 0
        assert "dry-run: token=secondary channel=C1 message=Message A" in out
        assert "dry-run: token=default channel=C2 message=hello" in out

    def test_reports_an_undefined_token_as_a_failure(self, capsys):
        failures = preview_all({"default": "xoxb-1"}, [Post("C1", "m", token="ghost")])

        assert failures == 1
        assert "token 'ghost' is not defined" in capsys.readouterr().err

    def test_never_reveals_the_token_value(self, capsys):
        preview_all({"default": "xoxb-secret"}, [Post("C1", "m")])

        assert "xoxb-secret" not in capsys.readouterr().out


class TestMainSsidHandling:
    @pytest.fixture
    def sent(self, monkeypatch):
        captured: list[Post] = []

        def fake_send_all(tokens, posts):
            captured.extend(posts)
            return 0

        monkeypatch.setattr(notify, "send_all", fake_send_all)
        return captured

    @pytest.fixture
    def config(self, monkeypatch):
        cfg = {
            "slack_bot_tokens": {"default": "xoxb-test"},
            "posts": {
                "message_a": [
                    {"channel": "C-network-a", "message": "Message A", "ssid": "network-a"},
                    {"channel": "C-network-b", "message": "Message B", "not_ssid": "network-a"},
                ],
                "plain": [{"channel": "C-any", "message": "m"}],
            },
        }
        monkeypatch.setattr(notify, "load_config", lambda: cfg)
        return cfg

    def _detect(self, monkeypatch, ssid=None, error=None):
        def fake_current_ssid():
            if error:
                raise error
            return ssid

        monkeypatch.setattr(notify, "current_ssid", fake_current_ssid)

    def test_sends_only_the_post_matching_the_detected_ssid(
        self, monkeypatch, config, sent
    ):
        self._detect(monkeypatch, ssid="network-a")
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a"])

        assert notify.main() == 0
        assert [p.channel for p in sent] == ["C-network-a"]

    def test_ssid_option_overrides_detection(self, monkeypatch, config, sent):
        self._detect(monkeypatch, error=AssertionError("must not detect"))
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a", "--ssid", "other-free"])

        assert notify.main() == 0
        assert [p.channel for p in sent] == ["C-network-b"]

    def test_ignore_ssid_sends_every_post(self, monkeypatch, config, sent):
        self._detect(monkeypatch, error=AssertionError("must not detect"))
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a", "--ignore-ssid"])

        assert notify.main() == 0
        assert [p.channel for p in sent] == ["C-network-a", "C-network-b"]

    def test_skips_detection_when_no_post_has_an_ssid_condition(
        self, monkeypatch, config, sent
    ):
        self._detect(monkeypatch, error=AssertionError("must not detect"))
        monkeypatch.setattr("sys.argv", ["slack-sender", "plain"])

        assert notify.main() == 0
        assert [p.channel for p in sent] == ["C-any"]

    def test_fails_when_the_ssid_cannot_be_read(self, monkeypatch, config, sent, capsys):
        self._detect(monkeypatch, error=SsidUnavailableError("SSID is hidden by macOS"))
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a"])

        assert notify.main() == 1
        assert sent == []
        assert "SSID is hidden by macOS" in capsys.readouterr().err

    def test_exits_successfully_when_no_post_matches(
        self, monkeypatch, sent, capsys
    ):
        cfg = {
            "slack_bot_tokens": {"default": "xoxb-test"},
            "posts": {"message_a": [{"channel": "C1", "message": "m", "ssid": "network-a"}]},
        }
        monkeypatch.setattr(notify, "load_config", lambda: cfg)
        self._detect(monkeypatch, ssid="network-b")
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a"])

        assert notify.main() == 0
        assert sent == []
        assert "no posts match" in capsys.readouterr().out

    def test_does_not_detect_the_ssid_when_every_condition_is_empty(
        self, monkeypatch, sent, capsys
    ):
        cfg = {
            "slack_bot_tokens": {"default": "xoxb-test"},
            "posts": {"message_a": [{"channel": "C1", "message": "m", "ssid": ""}]},
        }
        monkeypatch.setattr(notify, "load_config", lambda: cfg)
        self._detect(monkeypatch, error=AssertionError("must not detect"))
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a"])

        assert notify.main() == 0
        assert sent == []
        assert "no posts match" in capsys.readouterr().out

    def test_dry_run_previews_the_filtered_posts_without_sending(
        self, monkeypatch, config, sent, capsys
    ):
        self._detect(monkeypatch, ssid="network-a")
        monkeypatch.setattr(
            "sys.argv", ["slack-sender", "message_a", "--dry-run", "--append", " (extra)"]
        )

        assert notify.main() == 0
        assert sent == []
        assert "dry-run: token=default channel=C-network-a message=Message A (extra)" in (
            capsys.readouterr().out
        )

    def test_dry_run_fails_when_a_token_is_undefined(self, monkeypatch, sent, capsys):
        cfg = {
            "slack_bot_tokens": {"default": "xoxb-test"},
            "posts": {"message_a": [{"channel": "C1", "message": "m", "token": "ghost"}]},
        }
        monkeypatch.setattr(notify, "load_config", lambda: cfg)
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a", "--dry-run"])

        assert notify.main() == 1
        assert sent == []
        assert "token 'ghost' is not defined" in capsys.readouterr().err

    def test_append_keeps_the_ssid_condition(self, monkeypatch, config, sent):
        self._detect(monkeypatch, ssid="network-a")
        monkeypatch.setattr("sys.argv", ["slack-sender", "message_a", "--append", " (extra)"])

        assert notify.main() == 0
        assert sent[0].message == "Message A (extra)"
        assert sent[0].ssid.include == ("network-a",)


class TestParseLinkTo:
    def test_post_without_link_to_has_none(self):
        cfg = {"posts": {"message_a": [{"channel": "C1", "message": "m"}]}}

        assert parse_posts(cfg, "message_a")[0].link_to is None

    def test_parses_channel_and_message(self):
        cfg = {
            "posts": {
                "message_a": [
                    {
                        "channel": "C1",
                        "message": "m",
                        "link_to": {"channel": "C2", "message": "Posted: {link}"},
                    }
                ]
            }
        }

        link_to = parse_posts(cfg, "message_a")[0].link_to

        assert link_to.channel == "C2"
        assert link_to.message == "Posted: {link}"

    def test_message_defaults_to_the_bare_link(self):
        cfg = {
            "posts": {"message_a": [{"channel": "C1", "message": "m", "link_to": {"channel": "C2"}}]}
        }

        assert parse_posts(cfg, "message_a")[0].link_to.message == "{link}"

    def test_rejects_a_message_without_the_link_placeholder(self):
        cfg = {
            "posts": {
                "message_a": [
                    {
                        "channel": "C1",
                        "message": "m",
                        "link_to": {"channel": "C2", "message": "Posted"},
                    }
                ]
            }
        }

        with pytest.raises(ValueError, match=r"posts\.message_a\[0\]\.link_to\.message"):
            parse_posts(cfg, "message_a")

    def test_rejects_a_link_to_without_a_channel(self):
        cfg = {"posts": {"message_a": [{"channel": "C1", "message": "m", "link_to": {}}]}}

        with pytest.raises(ValueError, match=r"posts\.message_a\[0\]\.link_to"):
            parse_posts(cfg, "message_a")


class FakeClient:
    """WebClient stand-in that records chat_postMessage and chat_getPermalink calls."""

    def __init__(self, token: str, *, permalink_error=None, post_errors=()):
        self.token = token
        self.posted: list[tuple[str, str]] = []
        self.permalink_error = permalink_error
        self.post_errors = set(post_errors)
        self.permalinks: list[tuple[str, str]] = []
        self._ts = 0

    def chat_postMessage(self, *, channel: str, text: str):
        if channel in self.post_errors:
            raise slack_error("channel_not_found")
        self.posted.append((channel, text))
        self._ts += 1
        return {"channel": channel, "ts": f"170000000.00000{self._ts}"}

    def chat_getPermalink(self, *, channel: str, message_ts: str):
        self.permalinks.append((channel, message_ts))
        if self.permalink_error:
            raise slack_error(self.permalink_error)
        return {"permalink": f"https://example.slack.com/archives/{channel}/p{message_ts}"}


def slack_error(code: str) -> SlackApiError:
    return SlackApiError(code, {"error": code})


class TestSendAllLinkTo:
    @pytest.fixture
    def client_for(self, monkeypatch):
        clients: list[FakeClient] = []

        def factory(**kwargs):
            def build(token):
                client = FakeClient(token, **kwargs)
                clients.append(client)
                return client

            monkeypatch.setattr(notify, "WebClient", lambda token: build(token))
            return clients

        return factory

    def test_posts_the_permalink_to_the_other_channel(self, client_for, capsys):
        clients = client_for()
        posts = [
            Post(
                channel="C1",
                message="Message A",
                link_to=notify.LinkTo(channel="C2", message="Post: {link}"),
            )
        ]

        failures = notify.send_all({"default": "xoxb-1"}, posts)

        assert failures == 0
        assert clients[0].posted == [
            ("C1", "Message A"),
            ("C2", "Post: https://example.slack.com/archives/C1/p170000000.000001"),
        ]
        assert "linked: token=default channel=C2" in capsys.readouterr().out

    def test_uses_the_same_token_as_the_original_post(self, client_for):
        clients = client_for()
        posts = [Post("C1", "m", token="secondary", link_to=notify.LinkTo("C2"))]

        notify.send_all({"secondary": "xoxb-1"}, posts)

        assert len(clients) == 1
        assert clients[0].token == "xoxb-1"

    def test_skips_the_link_when_the_original_post_fails(self, client_for, capsys):
        clients = client_for(post_errors=("C1",))
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2"))]

        failures = notify.send_all({"default": "xoxb-1"}, posts)

        assert failures == 1
        assert clients[0].posted == []

    def test_counts_a_permalink_failure_without_losing_the_original_post(
        self, client_for, capsys
    ):
        clients = client_for(permalink_error="message_not_found")
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2"))]

        failures = notify.send_all({"default": "xoxb-1"}, posts)

        assert failures == 1
        assert clients[0].posted == [("C1", "m")]
        assert "message_not_found" in capsys.readouterr().err

    def test_counts_a_failed_link_post_as_a_failure(self, client_for, capsys):
        clients = client_for(post_errors=("C2",))
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2"))]

        failures = notify.send_all({"default": "xoxb-1"}, posts)

        assert failures == 1
        assert clients[0].posted == [("C1", "m")]
        assert "channel_not_found" in capsys.readouterr().err


class TestPreviewLinkTo:
    def test_shows_the_link_post_with_an_unresolved_link(self, capsys):
        posts = [Post("C1", "Message A", link_to=notify.LinkTo("C2", "Post: {link}"))]

        failures = preview_all({"default": "xoxb-1"}, posts)

        out = capsys.readouterr().out
        assert failures == 0
        assert "dry-run: token=default channel=C2 message=Post: (link unavailable)" in out


class TestParseLinkToToken:
    def _link_to(self, link_to: dict, *, token: str | None = None):
        entry = {"channel": "C1", "message": "m", "link_to": link_to}
        if token:
            entry["token"] = token
        return parse_posts({"posts": {"message_a": [entry]}}, "message_a")[0].link_to

    def test_token_defaults_to_the_token_of_the_original_post(self):
        assert self._link_to({"channel": "C2"}, token="secondary").token == "secondary"

    def test_token_defaults_to_the_default_token_when_the_post_omits_it(self):
        assert self._link_to({"channel": "C2"}).token == "default"

    def test_an_explicit_token_overrides_the_one_of_the_original_post(self):
        link_to = self._link_to({"channel": "C2", "token": "bot"}, token="secondary")

        assert link_to.token == "bot"


class TestSendAllLinkToToken:
    @pytest.fixture
    def client_for(self, monkeypatch):
        clients: list[FakeClient] = []

        def factory(**kwargs):
            def build(token):
                client = FakeClient(token, **kwargs)
                clients.append(client)
                return client

            monkeypatch.setattr(notify, "WebClient", lambda token: build(token))
            return clients

        return factory

    def test_posts_the_link_with_its_own_token(self, client_for):
        clients = client_for()
        posts = [
            Post(
                channel="C1",
                message="m",
                token="secondary",
                link_to=notify.LinkTo("C2", token="bot"),
            )
        ]

        failures = notify.send_all({"secondary": "xoxb-1", "bot": "xoxb-1"}, posts)

        assert failures == 0
        assert [(c.token, c.posted) for c in clients] == [
            ("xoxb-1", [("C1", "m")]),
            ("xoxb-1", [("C2", "https://example.slack.com/archives/C1/p170000000.000001")]),
        ]

    def test_reuses_one_client_per_token(self, client_for):
        clients = client_for()
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2"))]

        notify.send_all({"default": "xoxb-1"}, posts)

        assert len(clients) == 1

    def test_reports_an_undefined_link_token_without_losing_the_original_post(
        self, client_for, capsys
    ):
        clients = client_for()
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2", token="ghost"))]

        failures = notify.send_all({"default": "xoxb-1"}, posts)

        assert failures == 1
        assert clients[0].posted == [("C1", "m")]
        assert "token 'ghost' is not defined" in capsys.readouterr().err

    def test_reads_the_permalink_with_the_token_of_the_original_post(self, client_for):
        clients = client_for()
        posts = [
            Post("C1", "m", token="secondary", link_to=notify.LinkTo("C2", token="bot"))
        ]

        notify.send_all({"secondary": "xoxb-1", "bot": "xoxb-1"}, posts)

        assert clients[0].permalinks == [("C1", "170000000.000001")]
        assert clients[1].permalinks == []


class TestPreviewLinkToToken:
    def test_shows_the_token_used_for_the_link_post(self, capsys):
        posts = [Post("C1", "m", token="secondary", link_to=notify.LinkTo("C2", token="bot"))]

        failures = preview_all({"secondary": "xoxb-1", "bot": "xoxb-1"}, posts)

        assert failures == 0
        assert "dry-run: token=bot channel=C2" in capsys.readouterr().out

    def test_reports_an_undefined_link_token_as_a_failure(self, capsys):
        posts = [Post("C1", "m", link_to=notify.LinkTo("C2", token="ghost"))]

        failures = preview_all({"default": "xoxb-1"}, posts)

        assert failures == 1
        assert "token 'ghost' is not defined" in capsys.readouterr().err


class TestLinkErrorAttribution:
    @pytest.fixture
    def client_for(self, monkeypatch):
        clients: list[FakeClient] = []

        def factory(**kwargs):
            def build(token):
                client = FakeClient(token, **kwargs)
                clients.append(client)
                return client

            monkeypatch.setattr(notify, "WebClient", lambda token: build(token))
            return clients

        return factory

    def test_a_permalink_failure_names_the_token_of_the_original_post(
        self, client_for, capsys
    ):
        client_for(permalink_error="message_not_found")
        posts = [
            Post("C1", "m", token="secondary", link_to=notify.LinkTo("C2", token="bot"))
        ]

        notify.send_all({"secondary": "xoxb-1", "bot": "xoxb-1"}, posts)

        err = capsys.readouterr().err
        assert "token=secondary channel=C1" in err
        assert "message_not_found" in err

    def test_a_failed_link_post_names_the_link_token(self, client_for, capsys):
        client_for(post_errors=("C2",))
        posts = [
            Post("C1", "m", token="secondary", link_to=notify.LinkTo("C2", token="bot"))
        ]

        notify.send_all({"secondary": "xoxb-1", "bot": "xoxb-1"}, posts)

        assert "token=bot channel=C2" in capsys.readouterr().err
