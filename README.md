# slack-sender

A CLI tool for posting preset messages and submitting slash commands from `config.json` to Slack.

Each key can contain multiple posts or slash commands. For example, `slack-send message_a` can post to multiple channels with different tokens in one run.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Setup

```sh
uv sync
cp config.example.json config.json
# Edit config.json with your Slack tokens, channels, and messages.
```

## Usage

```sh
uv run slack-send message_a                     # Send posts.message_a
uv run slack-send message_b                     # Send posts.message_b
uv run slack-send message_b --append " (extra)" # Append text to each message
uv run slack-send message_a --ssid network-a    # Evaluate conditions for this SSID
uv run slack-send message_a --ignore-ssid       # Send all posts regardless of SSID
uv run slack-send message_a --dry-run           # Preview posts without sending
uv run slack-send command_a --dry-run           # Preview a slash command
uv run slack-send command_a                     # Submit the configured command
```

| Argument or option | Purpose |
| --- | --- |
| `key` | Select `posts.<key>` and `commands.<key>` in `config.json` |
| `--append TEXT` | Append `TEXT` to each message or command |
| `--ssid NAME` | Use `NAME` instead of detecting the current SSID |
| `--ignore-ssid` | Ignore `ssid` and `not_ssid` conditions for all entries |
| `--dry-run` | Show planned posts and commands without sending them |

If the selected key has no posts or commands, the CLI does nothing and exits successfully. If any operation fails, it exits with code 1.

## Dry run

`--dry-run` applies SSID filtering and `--append`, then shows the planned posts and commands without sending them.

```sh
$ uv run slack-send message_b --dry-run
dry-run: token=secondary channel=C0000000000 message=Sample message B
```

- You can combine it with other options. For example, `--ssid` shows which posts would be sent on a given network.
- Only token names (`token=`) are shown, never token values.
- A post whose `token` is missing from `slack_bot_tokens` is reported on stderr and causes exit code 1.
- Channel existence and token validity are not checked with Slack.
- Slash commands use a Slack browser session. A dry run does not check session validity or whether the target conversation exists.

## Configuration (`config.json`)

Copy `config.example.json` to get started.

```json
{
  "slack_bot_tokens": {
    "default": "xoxb-...",
    "secondary": "xoxb-..."
  },
  "slack_command_sessions": {
    "T0000000000": {
      "token": "xoxc-XXXXXXXXXX",
      "cookie": "xoxd-XXXXXXXXXX"
    }
  },
  "posts": {
    "message_a": [
      {"channel": "C0000000000", "message": "Sample message A", "token": "secondary",
       "ssid": ["network-a", "network-*-5g"], "not_ssid": "network-guest",
       "link_to": {"channel": "C1111111111", "message": "Posted: {link}",
                   "token": "default"}},
      {"channel": "C0000000000", "message": "Message for another network", "token": "secondary",
       "not_ssid": ["network-a", "network-*-5g"]}
    ],
    "message_b": [{"channel": "C0000000000", "message": "Sample message B", "token": "secondary"}]
  },
  "commands": {
    "command_a": [{"team": "T0000000000", "channel": "C0000000000",
                   "command": "/example_action"}],
    "command_b": [{"team": "T0000000000", "channel": "C0000000000",
                   "command": "/another_action"}]
  }
}
```

- **`slack_bot_tokens`**: Named tokens. Posts without a `token` use `default`. A value matching another key is resolved as an alias.
- **`slack_command_sessions`**: Browser session credentials keyed by workspace `team` ID. Commands use these credentials, independent of post tokens.
- **`posts.<key>`**: An array of posts that can target multiple channels and tokens. Keys can have any name and correspond to the `slack-send <key>` argument.
- **`ssid` / `not_ssid`**: Per-post Wi-Fi conditions, described below.
- **`link_to`**: Post a link to the original message in another channel, as described below.
- **`commands.<key>`**: Slash commands submitted through the private `chat.command` API. Each entry has a workspace `team` ID, a conversation `channel` ID, and a slash `command`.

`config.json` is ignored by Git. Keep your tokens out of commits.

## Slash commands

`commands.<key>` works with the existing key selection, Wi-Fi conditions, `--append`, `--ignore-ssid`, and `--dry-run` options. A key can contain multiple commands, posts, or both. Commands use the browser session for their `team` ID.

```json
"commands": {
  "command_a": [
    {
      "team": "T0000000000",
      "channel": "C0000000000",
      "command": "/example_action"
    }
  ]
}
```

The command must start with `/` and stay on one line. `team` and `channel` are Slack IDs, not names. Use a conversation where the command works when entered manually. The CLI sends the command name and any following text separately to `chat.command`. It does not need Slack Desktop or an unlocked screen. The host running the CLI must still be awake and connected to the network.

Copy the `xoxc-...` token from an authenticated Slack browser request and the paired `d` cookie (`xoxd-...`) from the same browser session into `slack_command_sessions.<team>`. Both credentials are sensitive and may expire. A bot token from `slack_bot_tokens` cannot replace them. The private API can change without notice.

The `submitted:` output means the API returned `ok: true`. It does not confirm that the connected app completed the action. Check the app's response before retrying a command that can have side effects.

For an iPhone Shortcut, change to this project directory and run `uv run slack-send command_a` or another configured key through “Run Script over SSH” on the Mac. The Mac must be awake for SSH to run, but its screen may be locked.

## Forwarding post links (`link_to`)

After sending a post with `link_to`, the tool retrieves its permalink and posts it to another channel.

```json
{"channel": "C0000000000", "message": "Sample message A", "token": "secondary",
 "link_to": {"channel": "C1111111111", "message": "Posted: {link}",
             "token": "default"}}
```

```
sent: token=secondary channel=C0000000000 ts=1700000000.000100
linked: token=default channel=C1111111111 ts=1700000000.000200
```

- `{link}` in `message` is replaced with the permalink. A `message` without `{link}` is a configuration error.
- If `message` is omitted, only the permalink is posted.
- If `token` is omitted, the original post's token is used. That token must also be able to post to the destination channel.
- Set `token` to a `slack_bot_tokens` key to use a different token for forwarding. The original post's token is always used to retrieve its permalink.
- If the original post fails, its link is not forwarded.
- If permalink retrieval or forwarding fails, the original post remains and the command exits with code 1.
- `--append` affects only the original message, not `link_to.message`.
- With `--dry-run`, the permalink is unavailable, so `{link}` appears as `(link unavailable)`.

## Wi-Fi SSID conditions

Add `ssid` (an allowlist) and `not_ssid` (an exclusion list) to a post to control sending based on the current Wi-Fi SSID. In the example above, the specified networks receive “Sample message A”; other networks receive “Message for another network.”

- Values can be strings or arrays. Wildcards such as `network-*` use `fnmatch` syntax.
- Matching is case sensitive.
- If both `ssid` and `not_ssid` are set, the SSID must match `ssid` and must not match `not_ssid`.
- Posts without either condition are always sent.
- When Wi-Fi is disconnected, posts with `ssid` are skipped, while posts with only `not_ssid` are sent.
- If no posts match, nothing is sent and the command exits successfully. Skipped posts are reported as `skipped:` on stderr.
- If a key has no posts requiring SSID detection, the tool does not attempt detection.

### Empty values

| Configuration | Meaning |
| --- | --- |
| Omit `ssid` | Send on any network, including when Wi-Fi is disconnected |
| `"ssid": "*"` | Send for any SSID when connected to Wi-Fi |
| `"ssid": ""` / `"ssid": []` | Never send, useful for temporarily disabling a post |
| `"not_ssid": ""` / `"not_ssid": []` | Exclude nothing, equivalent to omitting `not_ssid` |

A post with an empty `ssid` is skipped, with the reason reported on stderr:

```
skipped: channel=C0000000000 does not match any network (ssid is empty)
```

### macOS hides SSIDs by default

Since macOS 14.4, the SSID is hidden as `<redacted>` by default, causing `slack-send` to exit with an error:

```
error: SSID is hidden by macOS. Run `sudo ipconfig setverbose 1` to unhide it ...
```

Enable verbose mode in `ipconfig` to read the SSID:

```sh
sudo ipconfig setverbose 1
ipconfig getsummary en0 | grep ' SSID'
```

This setting exists only in configd memory, so it must be enabled **after every reboot**. To run it automatically at startup, install the LaunchDaemon:

```sh
uv run slack-send-setup
```

This installs `/Library/LaunchDaemons/local.ipconfig-setverbose.plist`, activates it immediately, and checks whether the SSID is readable. Installation requires root privileges, so `sudo` prompts for a password. Run `uv run slack-send-setup --uninstall` to remove it.

Verbose mode also increases IPConfiguration debug logging. If that is a concern, use `--ssid NAME` to provide the SSID explicitly or `--ignore-ssid` to ignore the conditions.

The CLI cannot request location permission to reveal the SSID. Permission requests require an app bundle, and requests from unbundled processes (such as Python or a standalone binary) are discarded before reaching locationd. Reading Wi-Fi information also requires the `com.apple.developer.networking.wifi-info` entitlement, which requires signing through the Apple Developer Program.

## Tests

```sh
uv run pytest
```
