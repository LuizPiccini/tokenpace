# Install Token Pace with an AI agent

**People:** give this file to your coding agent (Codex, Claude Code, Cursor, …) with a
request like *"Install Token Pace by following
https://raw.githubusercontent.com/LuizPiccini/tokenpace/main/INSTALL.md"*.
The [README](README.md) has the same information for doing it by hand.

**Agents:** the rest of this file is for you. Work through the steps in order. At the
end, report back as described in step 8.

## Ground rules

- **Never reveal a secret.** Do not print, copy, paste, summarise or upload the contents
  of a login file, token, API key or cookie. Check whether a credential exists (a file is
  present, a variable is set) without displaying it. `tokenpace collect` reports whether
  each login works and never prints secrets.
- **Leave other tools' logins alone.** Do not edit, move or refresh `~/.codex/auth.json`,
  `~/.claude/.credentials.json` or the macOS Keychain. If a login is missing or expired,
  ask the person to sign in with that tool themselves.
- **Ask before** you: make Token Pace start automatically, listen on anything other than
  `127.0.0.1`, store an API key on disk, create a separate Claude login, set
  `refresh = true`, or install anything besides Token Pace and pipx.
- **Keep it private.** Never expose the server to the public internet. Reaching it from a
  phone goes through a private network such as Tailscale (step 7).

## 1. Check Python

Token Pace needs Python 3.11 or newer and nothing else. Run `python3 --version` (or
`python --version` on Windows). If it is older, stop and tell the person.

## 2. Install

```sh
pipx install git+https://github.com/LuizPiccini/tokenpace@v0.3.0
tokenpace --version
tokenpace collect --demo
```

The last command should print a block of JSON and exit without an error. These commands
work the same in Windows PowerShell.

If pipx is missing, install it (`python3 -m pip install --user pipx`, then
`python3 -m pipx ensurepath`) or use `python3 -m pip install --user
git+https://github.com/LuizPiccini/tokenpace@v0.3.0`. If `tokenpace` is not found afterwards, the
install's script directory is not on PATH; run `pipx ensurepath` and open a new shell.
Note the full path from `command -v tokenpace` (`where tokenpace` on Windows) for step 6.

## 3. Find what can be read automatically

Check existence only:

| Plan | Present when | Provider |
|---|---|---|
| ChatGPT (Plus/Pro/…) | `$CODEX_HOME/auth.json` or `~/.codex/auth.json` exists (the Codex CLI login) | `codex` |
| Claude (Pro/Max) | `$CLAUDE_CONFIG_DIR/.credentials.json` or `~/.claude/.credentials.json` exists; on macOS Claude Code may keep it in the Keychain instead, which the provider reads itself | `claude_code` |
| OpenRouter free models | `OPENROUTER_API_KEY` is set (test with `[ -n "$OPENROUTER_API_KEY" ]`, or `[bool]$env:OPENROUTER_API_KEY` in PowerShell; do not echo it) | `openrouter_free` |

Then ask the person, in one message:

1. Which of the detected plans to include, and whether they pay for other AI plans.
   Plans without a reader become `manual`: they type the numbers on the page. Ask
   each one's renewal period in days.
2. Whether they want groups (for example *Personal* and *Work*). One group is fine.
3. Whether some plans live on another computer, such as a work laptop. Those use
   `push`; see "Several machines" in the README and handle them after the rest works.

## 4. Write the config

Create `~/.config/tokenpace/config.toml` (on Windows, `%USERPROFILE%\.config\tokenpace\config.toml`).
Include only what the person chose. A typical result:

```toml
[server]
title = "AI subscriptions"
host = "127.0.0.1"
port = 8787

[[groups]]
id = "personal"
label = "Personal"

[[subscriptions]]
id = "chatgpt"
name = "ChatGPT"
group = "personal"
provider = "codex"
use_via = "Codex CLI"

[[subscriptions]]
id = "claude"
name = "Claude"
group = "personal"
provider = "claude_code"
use_via = "Claude Code"

[[subscriptions]]
id = "openrouter-free"
name = "OpenRouter free"
group = "personal"
provider = "openrouter_free"
free = true
api_key_env = "OPENROUTER_API_KEY"

[[subscriptions]]
id = "other-plan"
name = "Other plan"
group = "personal"
provider = "manual"
period_days = 30
```

Every `id` must be unique, and every `group` must match a `[[groups]]` id. All options
are in [config.example.toml](https://raw.githubusercontent.com/LuizPiccini/tokenpace/main/config.example.toml).
Validate:

```sh
tokenpace check --config ~/.config/tokenpace/config.toml
```

## 5. Check the readings

```sh
tokenpace collect --config ~/.config/tokenpace/config.toml
```

This reads every plan once and prints JSON. For each item in `subscriptions`, look at
`status` and `message`:

- `ok`: working.
- `codex` unavailable or failing: ask the person to run `codex login`, then retry.
- `claude_code` unavailable or failing: ask them to open Claude Code and sign in, then
  retry. Claude logins expire after about 8 hours and renew only when Claude Code runs.
  If this machine does not use Claude Code every day, explain the README's option of a
  separate login that Token Pace renews (section "Providers"), including its note about
  Anthropic's terms, and set it up only if they agree.
- `openrouter_free` unavailable: the key is not visible to Token Pace. If it should run
  in the background, the key must be in that environment too (step 6).
- `manual` "Not entered yet": expected. The person enters numbers on the page.

## 6. Start it

```sh
tokenpace serve --config ~/.config/tokenpace/config.toml
```

Confirm it answers at `http://127.0.0.1:8787/healthz` (expect `"ok": true`): `curl -s`
on Linux and macOS, `Invoke-RestMethod` in Windows PowerShell. Then give the person
`http://localhost:8787`. Stop this foreground server before step 6b.

**6b. Start automatically (ask first).**

- Linux: download [contrib/systemd/tokenpace.service](https://raw.githubusercontent.com/LuizPiccini/tokenpace/main/contrib/systemd/tokenpace.service)
  to `~/.config/systemd/user/`, set `ExecStart` to the path from step 2, then
  `systemctl --user daemon-reload && systemctl --user enable --now tokenpace`. Ask
  before running `loginctl enable-linger`, which keeps it running after logout.
- macOS: download [contrib/launchd/app.tokenpace.plist](https://raw.githubusercontent.com/LuizPiccini/tokenpace/main/contrib/launchd/app.tokenpace.plist)
  to `~/Library/LaunchAgents/`, replace the binary path and `/Users/YOU`, then
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/app.tokenpace.plist`.
- Windows: there is no bundled service. Create a Task Scheduler task that runs at the
  person's logon: `<path from step 2> serve --config <config path>`.

A background service does not see variables set in an interactive shell. If
`OPENROUTER_API_KEY` is used, ask whether to put it in a file readable only by the
person and point `api_key_file` at it, or add it to the service's environment (the
systemd unit has a commented `EnvironmentFile` line). Check `/healthz` again afterwards.

## 7. Phone and Android widget (optional, ask first)

The phone has to reach this machine privately. With Tailscale installed on both:

1. Set a token so nothing else on the network can read the page:
   `tokenpace set-token --config ~/.config/tokenpace/config.toml`. It writes a random token
   into `[server]`, makes the file readable only by its owner on Linux and macOS, and does
   not print the token. Do not open, print or paste it yourself; tell the person it is the
   `token` line in that file.
   If it refuses to edit the file, stop and ask the person to add the token themselves.
2. Either set `[server] host` to the machine's Tailscale IP, or keep `127.0.0.1` and run
   `tailscale serve --bg 8787`. Restart Token Pace.
3. The widget APK is attached to the
   [latest release](https://github.com/LuizPiccini/tokenpace/releases/latest). The person
   downloads it on the phone, allows the install, adds the **Token Pace** widget, and
   enters the server address and token.

## 7b. Windows mini window (optional, ask first)

On Windows, `tokenpace mini` shows the ranking as a small floating pill above the taskbar,
plus a tray icon. Start it with `pythonw -m tokenpace mini`, using the Python Token Pace was
installed into (for pipx, the `pythonw.exe` in its tokenpace environment). It opens a
settings window where the person enters the server address and token; do not type the
token for them. "Start with Windows" in its tray menu makes it start at logon.

## 8. Report back

Tell the person, briefly:

- the page address, and whether Token Pace starts automatically;
- which plans read correctly and which need them to do something (sign in, type numbers);
- the config path, so they can edit it later;
- for the phone, the address to enter in the widget and where the token is stored.
