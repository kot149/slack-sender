"""Send preset Slack posts defined in config.json.

config.json schema (posts section):
{
  "slack_bot_tokens": {
    "default": "xoxb-...",
    "secondary": "xoxb-..."
  },
  "posts": {
    "<key>": [
      {"channel": "C0123", "message": "...", "token": "default",
       "ssid": ["network-*"], "not_ssid": "network-b"}
    ]
  }
}

If `token` is omitted, "default" is used.
The legacy `slack_bot_token` field maps to `slack_bot_tokens.default`.

Posts with `ssid` / `not_ssid` are sent only when the current Wi-Fi SSID
matches their conditions. Values may be strings or arrays with `fnmatch` wildcards.

Example keys: "message_a", "message_b".
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import load_config
from .ssid import (
    SsidCondition,
    SsidUnavailableError,
    current_ssid,
    parse_exclude_patterns,
    parse_patterns,
)

DEFAULT_TOKEN_NAME = "default"
LINK_PLACEHOLDER = "{link}"
UNRESOLVED_LINK = "(link unavailable)"


@dataclass(frozen=True)
class LinkTo:
    """Settings for posting the original message's permalink to another channel."""

    channel: str
    message: str = LINK_PLACEHOLDER
    token: str = DEFAULT_TOKEN_NAME


@dataclass(frozen=True)
class Post:
    channel: str
    message: str
    token: str = DEFAULT_TOKEN_NAME
    ssid: SsidCondition = field(default_factory=SsidCondition)
    link_to: LinkTo | None = None


def load_tokens(cfg: dict) -> dict[str, str]:
    """Read `slack_bot_tokens` into a name-to-token dictionary.

    A value matching another key is resolved as an alias. For example,
    `"default": "bot"` shares the `bot` token.
    """
    tokens_raw = cfg.get("slack_bot_tokens")
    raw: dict[str, str] = {}

    if isinstance(tokens_raw, dict):
        for name, value in tokens_raw.items():
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"config.json: slack_bot_tokens.{name} must be a non-empty string"
                )
            raw[str(name)] = value
    elif tokens_raw is not None:
        raise ValueError("config.json: slack_bot_tokens must be an object")

    legacy = cfg.get("slack_bot_token")
    if legacy:
        raw.setdefault(DEFAULT_TOKEN_NAME, str(legacy))

    if not raw:
        raise ValueError("config.json: slack_bot_tokens is empty")

    resolved: dict[str, str] = {}
    for name in raw:
        seen: list[str] = []
        current = name
        while current in raw and raw[current] in raw and raw[current] != current:
            if current in seen:
                raise ValueError(
                    f"config.json: slack_bot_tokens has a reference cycle: "
                    f"{' -> '.join(seen + [current])}"
                )
            seen.append(current)
            current = raw[current]
        resolved[name] = raw[current]

    return resolved


def parse_posts(cfg: dict, key: str) -> list[Post]:
    entries = cfg.get("posts", {}).get(key, [])
    if not isinstance(entries, list):
        raise ValueError(f"config.json: posts.{key} must be a list")

    posts: list[Post] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "channel" not in e or "message" not in e:
            raise ValueError(
                f"config.json: posts.{key}[{i}] must have 'channel' and 'message'"
            )
        token = str(e.get("token", DEFAULT_TOKEN_NAME))
        posts.append(
            Post(
                channel=str(e["channel"]),
                message=str(e["message"]),
                token=token,
                ssid=SsidCondition(
                    include=parse_patterns(e.get("ssid"), f"posts.{key}[{i}].ssid"),
                    exclude=parse_exclude_patterns(
                        e.get("not_ssid"), f"posts.{key}[{i}].not_ssid"
                    ),
                ),
                link_to=parse_link_to(
                    e.get("link_to"), f"posts.{key}[{i}].link_to", token
                ),
            )
        )
    return posts


def parse_link_to(value: object, path: str, post_token: str) -> LinkTo | None:
    """Parse `link_to`, defaulting to the original post's token."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value.get("channel"):
        raise ValueError(f"config.json: {path} must be an object with 'channel'")

    message = str(value.get("message", LINK_PLACEHOLDER))
    if LINK_PLACEHOLDER not in message:
        raise ValueError(
            f"config.json: {path}.message must contain {LINK_PLACEHOLDER}"
        )
    return LinkTo(
        channel=str(value["channel"]),
        message=message,
        token=str(value.get("token", post_token)),
    )


def filter_by_ssid(posts: list[Post], ssid: str | None) -> list[Post]:
    """Return posts matching the SSID conditions and report skipped posts to stderr."""
    kept: list[Post] = []
    for p in posts:
        if p.ssid.matches(ssid):
            kept.append(p)
        else:
            print(
                f"skipped: channel={p.channel} does not match {_reason(p, ssid)}",
                file=sys.stderr,
            )
    return kept


def _reason(post: Post, ssid: str | None) -> str:
    if post.ssid.matches_nothing:
        return "any network (ssid is empty)"
    return f"ssid={ssid}" if ssid else "wi-fi not connected"


def preview_all(tokens: dict[str, str], posts: list[Post]) -> int:
    """Print planned posts to stdout without sending; count undefined tokens as failures."""
    failures = 0
    for p in posts:
        if p.token not in tokens:
            failures += 1
            print(_undefined_token_message(p.token), file=sys.stderr)
            continue
        print(f"dry-run: token={p.token} channel={p.channel} message={p.message}")
        if p.link_to:
            if p.link_to.token not in tokens:
                failures += 1
                print(_undefined_token_message(p.link_to.token), file=sys.stderr)
                continue
            message = p.link_to.message.replace(LINK_PLACEHOLDER, UNRESOLVED_LINK)
            print(
                f"dry-run: token={p.link_to.token} channel={p.link_to.channel} "
                f"message={message}"
            )
    return failures


def _undefined_token_message(token_name: str) -> str:
    return f"error: token '{token_name}' is not defined in slack_bot_tokens"


def send_all(tokens: dict[str, str], posts: list[Post]) -> int:
    grouped: dict[str, list[Post]] = defaultdict(list)
    for p in posts:
        grouped[p.token].append(p)

    client_for = _client_cache(tokens)
    failures = 0
    for token_name, group in grouped.items():
        client = client_for(token_name)
        if client is None:
            failures += len(group)
            print(_undefined_token_message(token_name), file=sys.stderr)
            continue

        for p in group:
            try:
                resp = client.chat_postMessage(channel=p.channel, text=p.message)
                print(
                    f"sent: token={token_name} channel={resp['channel']} ts={resp['ts']}"
                )
            except SlackApiError as e:
                failures += 1
                _report_api_error(token_name, p.channel, e)
                continue

            if p.link_to:
                failures += _send_link(
                    client, token_name, client_for, p.link_to, resp
                )
    return failures


def _client_cache(tokens: dict[str, str]):
    """Look up WebClient by token name, returning None for undefined names."""
    clients: dict[str, WebClient | None] = {}

    def client_for(name: str) -> WebClient | None:
        if name not in clients:
            token = tokens.get(name)
            clients[name] = WebClient(token=token) if token else None
        return clients[name]

    return client_for


def _send_link(
    source: WebClient, source_token: str, client_for, link_to: LinkTo, resp
) -> int:
    """Post the original message's permalink to `link_to.channel`; return failures.

    Fetch the permalink with `source`, which can read the original channel.
    Use the `link_to.token` client only to post the link.
    """
    client = client_for(link_to.token)
    if client is None:
        print(_undefined_token_message(link_to.token), file=sys.stderr)
        return 1

    try:
        permalink = source.chat_getPermalink(
            channel=resp["channel"], message_ts=resp["ts"]
        )["permalink"]
    except SlackApiError as e:
        _report_api_error(source_token, resp["channel"], e)
        return 1

    text = link_to.message.replace(LINK_PLACEHOLDER, permalink)
    try:
        linked = client.chat_postMessage(channel=link_to.channel, text=text)
        print(
            f"linked: token={link_to.token} channel={linked['channel']} "
            f"ts={linked['ts']}"
        )
        return 0
    except SlackApiError as e:
        _report_api_error(link_to.token, link_to.channel, e)
        return 1


def _report_api_error(token_name: str, channel: str, e: SlackApiError) -> None:
    print(
        f"error: slack api failed for token={token_name} "
        f"channel={channel}: {e.response['error']}",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "key", help="Key in posts (for example, message_a or message_b)"
    )
    parser.add_argument(
        "--append",
        default="",
        help="Text to append to each message",
    )
    parser.add_argument(
        "--ssid",
        default=None,
        help="Use this SSID instead of detecting the current one",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned posts without sending them",
    )
    parser.add_argument(
        "--ignore-ssid",
        action="store_true",
        help="Ignore ssid and not_ssid conditions and send all posts",
    )
    args = parser.parse_args()

    try:
        cfg = load_config()
        tokens = load_tokens(cfg)
        posts = parse_posts(cfg, args.key)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not posts:
        print(f"no posts configured for key='{args.key}', skipping")
        return 0

    if not args.ignore_ssid and any(not p.ssid.is_empty for p in posts):
        ssid = args.ssid
        # Empty include patterns already determine the result, so detection is unnecessary.
        if ssid is None and any(p.ssid.needs_current_ssid for p in posts):
            try:
                ssid = current_ssid()
            except SsidUnavailableError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        posts = filter_by_ssid(posts, ssid)
        if not posts:
            print("no posts match the current wi-fi network, skipping")
            return 0

    if args.append:
        posts = [replace(p, message=p.message + args.append) for p in posts]

    failures = preview_all(tokens, posts) if args.dry_run else send_all(tokens, posts)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
