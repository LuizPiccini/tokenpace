# Token Pace

**Which AI subscription should you use first?** Token Pace reads the usage limits of
your AI plans (ChatGPT/Codex, Claude, OpenRouter's free tier, anything you type in by
hand) and ranks them by how much quota you are about to leave on the table before each
reset. It is a small self-hosted page, a JSON API and an Android home-screen widget.
On Windows and macOS there is also a floating mini window (`tokenpace mini`).

![The tokenpace page in demo mode](docs/screenshot.png)

The page answers in three seconds: one tile per group names the subscription to use
first, a timeline shows every reset in the next three days, and each ranked row has a
single bar. The blue fill is the quota used, the white tick is how much of the period
has passed, and the hatched band between them is slack you would lose at the reset
(teal) or use ahead of pace (amber). Short windows such as Claude's 5 hours sit under
each row as chips, and "Why" explains the numbers.

## How the ranking works

Every plan has windows that reset: 5 hours, a week, a month, a day. For each
subscription Token Pace takes its longest window and compares the share of quota left
with the share of time left:

> **needed pace** = % of quota left ÷ % of the period left

The steady pace is the rate that would spend 100% of the quota evenly over the whole
period. At 1× what is left matches the time left. At 3× you would have to spend at
three times that steady rate from now on, or lose the rest at the reset. So:

| Verdict | Meaning |
|---|---|
| **Use first** | the highest needed pace in its group, above 1.15× |
| **Use too** | other subscriptions above 1.15× |
| **On pace** | 0.85× to 1.15× |
| **Save** | below 0.85×: at this rate the quota runs out before the reset |
| **Used up now** | a short window (say, Claude's 5 hours) is exhausted; it moves to the end until it frees up |
| **Free reserve** | free tiers, kept at the end for light tasks |

Subscriptions are ranked within groups (for example *Personal* and *Work*), each with
an optional logo.

## Quick start

To let your AI coding agent install it, ask it to follow [INSTALL.md](INSTALL.md):
*"Install Token Pace by following https://raw.githubusercontent.com/LuizPiccini/tokenpace/main/INSTALL.md"*.

Python 3.11 or newer, no other dependencies.

```sh
git clone https://github.com/LuizPiccini/tokenpace && cd tokenpace
python -m tokenpace serve --demo          # synthetic data at http://localhost:8787
```

Or install the command: `pipx install git+https://github.com/LuizPiccini/tokenpace@v0.4.1`.

Then describe your own subscriptions:

```sh
cp config.example.toml tokenpace.toml     # or ~/.config/tokenpace/config.toml
python -m tokenpace check                 # validate
python -m tokenpace collect               # read everything once, print JSON
python -m tokenpace serve
```

## Providers

| `provider` | Reads | Notes |
|---|---|---|
| `codex` | ChatGPT plan limits (5 hours, week) with the Codex CLI login in `~/.codex/auth.json` | read-only; the login renews whenever you use Codex |
| `claude_code` | Claude plan limits (5 hours, week, per-model weeks) with the Claude Code login (`~/.claude/.credentials.json` or the macOS Keychain) | read-only by default, see below |
| `openrouter_free` | OpenRouter's daily allowance of `:free` model requests | needs an API key in an env var or file |
| `manual` | numbers you type on the page | for consoles without an API |
| `push` | readings sent by `tokenpace push` from another machine | for a work laptop or a second computer |
| `demo` | synthetic data | for trying things out |

The ChatGPT and Claude readers call the same undocumented usage endpoints the
official apps use. They can change without notice; when they do, the row shows an
error instead of wrong numbers.

**Claude logins expire after about 8 hours** and are renewed only when Claude Code
runs. If the machine running tokenpace does not use Claude Code every day, give
tokenpace its own login and let it renew that one:

```sh
CLAUDE_CONFIG_DIR=~/.config/tokenpace/claude claude auth login
```

```toml
[[subscriptions]]
id = "claude"
provider = "claude_code"
credentials_file = "~/.config/tokenpace/claude/.credentials.json"
refresh = true
```

Never point `refresh = true` at the login Claude Code itself uses: refresh tokens
rotate, and two programs renewing the same login will log one of them out. Renewal
uses Claude Code's OAuth client, so check that this fits Anthropic's terms for your
account.

## Running it all the time

- Linux: `contrib/systemd/tokenpace.service` (a user unit; `loginctl enable-linger`
  keeps it running after logout).
- macOS: `contrib/launchd/app.tokenpace.plist`.

State (the last readings) lives in `~/.local/state/tokenpace` (`%LOCALAPPDATA%\tokenpace`
on Windows).

## Reaching it from your phone

Token Pace listens on `127.0.0.1` unless you change `[server] host`. The simplest
safe setup is a private network such as [Tailscale](https://tailscale.com): bind to
the machine's tailnet address, or keep localhost and run
`tailscale serve --bg 8787`. If anyone else can reach the port, set a token
(`[server] token` or `TOKENPACE_TOKEN`); the API and the widget then require it, and
the page asks for it once (or open it as `http://host:8787/#token=…`). Do not expose
it to the public internet without a token and TLS.

Without a token, Token Pace only answers requests addressed to an IP address,
`localhost` or the machine's own name, which stops other web pages from reaching it
through DNS rebinding. If you open it by another DNS name (a Tailscale MagicDNS name,
say), list it in `[server] allowed_hosts` or set a token.

## Several machines

Subscriptions that live on another computer (a work laptop with its own Claude login,
say) are declared on the server with `provider = "push"`. The other machine runs the
same tool with its own config and sends the numbers:

```sh
tokenpace push --config laptop.toml --to http://my-server:8787 --token "$TOKENPACE_TOKEN"
```

Run it from cron or launchd every 10 minutes. Pushes always need the server's token.
Only numbers, reset times, plan names and the optional `--source` label travel;
logins stay on each machine.

## Android widget

The widget shows the same ranking on the home screen, with a scrollable list on
Android 12+ and a height-fitted list on older versions. Tapping the title refreshes;
tapping a row opens the page.

```sh
cd android
./gradlew assembleDebug        # build/outputs/apk/debug/tokenpace-widget-debug.apk
./gradlew testDebugUnitTest    # Robolectric tests
```

It needs the Android SDK (platform 34) and JDK 17. Install the APK, add the
**Token Pace** widget, and enter your server address (and token, if set). To offer the
APK from your own server, set `[android] apk` in the config; the page then links to
it, and the widget announces newer versions when `version_code` goes up. Group logos
appear in the widget when they are PNG, JPEG or WebP.

## Mini window (Windows and macOS)

`tokenpace mini` keeps the answer on screen: a small pill just above the taskbar with the
plan to use first, its needed pace and reset, and the same pace bar as the page. Click it
to see every plan; drag it anywhere. A tray icon by the clock draws the top plan's bar and
opens a menu (refresh, open the page, settings, start with Windows, reset position, quit).
It reads `/api/widget` like the Android widget. On a work laptop you can put your work group
first: `tokenpace mini --focus Work` (with the mini closed) lists that group on top and only
its plans can be the one to use first; `--focus ""` goes back to the server's order.

<img src="docs/windows-mini.png" alt="The Windows mini window, expanded with synthetic demo data" width="368">

```powershell
pythonw -m tokenpace mini        # no console window
```

It is part of the Python package: no extra download, only the standard library. On first
run it asks for the server address (and token, if set) and keeps them in
`%APPDATA%\TokenPace\mini.json`, with the token encrypted for your Windows user. It runs
on PCs with Smart App Control, which blocks unsigned programs, because Python itself is
signed. Use the Python Token Pace was installed into: with pipx that is the one in its
tokenpace environment, not the `pythonw` on your PATH. Since `pythonw` shows no errors,
check first with `python -m tokenpace mini --help` from the same folder.

### macOS

<img src="docs/macos-mini.png" alt="The macOS mini window, expanded with synthetic demo data" width="368">

```sh
tokenpace mini                   # or: python3 -m tokenpace mini
```

The same pill, drawn from the same layout code, starts in the top-right corner under the
menu bar. It floats above other windows on every Space and never takes focus from the app
you are in. A menu bar icon draws the top plan's bar; it and a right-click on the pill open
the same menu (refresh, open the page, settings, start at login, reset position, quit). On a
MacBook whose menu bar is full, macOS hides icons that don't fit beside the notch: the
right-click menu still has everything, and a Cmd-drag moves the icon, which it remembers.
**Show first** in that menu picks the group that leads, as `--focus` does.

It uses only the standard library (ctypes into AppKit, Core Graphics and Core Text), so
there is nothing to compile or sign; with pipx, the `tokenpace` command already runs the
right Python. Settings are kept in `~/Library/Application Support/TokenPace/mini.json` and
the token in your login Keychain. "Start at login" adds
`~/Library/LaunchAgents/app.tokenpace.mini.plist`; macOS then says "python" can run in
the background and lists it under Login Items. Tested with Homebrew's Python 3.14 on
macOS 26.

## Development

```sh
python -m unittest discover -s tests -t .
```

A provider is a function `(subscription_config, now) -> {"windows": [...], "plan_label": ..., "message": ...}`
in `tokenpace/providers.py`, registered in `PROVIDERS`. Windows come from
`make_window(kind, used_percent, resets_at, seconds)`. Raise `ProviderError("unavailable", ...)`
when there is nothing to read and `ProviderError("error", ...)` when reading failed.
Providers must never log, print or return credentials.

Contributions, especially new providers, are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md)
and open a **New provider** issue to start.

Token Pace is not affiliated with OpenAI, Anthropic, OpenRouter or any other provider.

## License

MIT, see [LICENSE](LICENSE).
