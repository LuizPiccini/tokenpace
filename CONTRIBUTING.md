# Contributing to Token Pace

Contributions are welcome. The most useful ones are readers for plans Token Pace does
not cover yet. Bug reports help too: include the message the page shows and your
Python version, but never a token, key or raw credentials file.

## Adding a provider

Open an issue with the **New provider** template first, so we can agree on how the
plan's usage can be read before you write code. Then:

1. Write a function `(subscription_config, now) -> dict` in `tokenpace/providers.py`
   and register it in `PROVIDERS`.
2. Return its windows built with `make_window(kind, used_percent, resets_at, seconds)`.
   Use a kind from `WINDOW_KINDS` in `tokenpace/pace.py` when one fits; otherwise
   pass `seconds` so the pace can still be computed.
3. Make network calls through `http_json`. It never follows redirects, so a key cannot
   be resent to another host.
4. Raise `ProviderError("unavailable", ...)` when there is nothing to read (no login,
   no key) and `ProviderError("error", ...)` when a read fails.
5. Add tests to `tests/test_providers.py` with `http_json` mocked, as
   `test_openrouter_free` does. Tests must not touch the network or a real login.
6. Add the provider to the Providers table in the README and an example to
   `config.example.toml`.

## Credentials

- Read credentials only from where the provider's own tool keeps them, or from a file
  or environment variable the user names in the config.
- Never log, print or return a credential, and never write one anywhere new. Writing a
  renewed token back to the file it came from is fine (`claude_code` does this with
  `refresh = true`).
- Only read usage. A provider must not make calls that spend quota or change the
  account.
- If a reader relies on an undocumented endpoint, say so in the README, as the ChatGPT
  and Claude readers do.

## Before opening a pull request

```sh
python -m unittest discover -s tests -t .
python -m tokenpace collect --demo
```

For widget changes, also run `./gradlew testDebugUnitTest` in `android/` (Android
SDK platform 34 and JDK 17). CI runs all of this on Python 3.11 to 3.13 and builds the
APK.

The server uses only the Python standard library; please keep it that way unless an
issue agrees otherwise. Keep each pull request to one change, and say what you tested
against a real account. Contributions are released under the project's MIT license.
