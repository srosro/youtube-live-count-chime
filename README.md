# Live Count Chime

A small, strictly typed macOS command-line watcher that monitors the live viewer
count of multiple YouTube and Twitch channels at once and plays one sound when a
count increases and a different sound when it decreases.

## Requirements

- macOS
- Python 3.11 or newer
- Internet access to the public YouTube watch pages and the Twitch Helix API
- A Twitch developer application (only if you monitor Twitch channels — see below)

The watcher has no third-party runtime dependencies. Development type checking
uses mypy.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Run

Pass one or more channels with repeatable `-y`/`--youtube` (handle) and
`-t`/`--twitch` (login) flags. It polls every five seconds by default:

```bash
.venv/bin/youtube-live-count-chime -y @mkbhd -y LinusTechTips -t shroud
```

For each channel, the first valid count is a silent baseline. After that:

- an increase plays `/System/Library/Sounds/Glass.aiff`;
- a decrease plays `/System/Library/Sounds/Basso.aiff`;
- an unchanged or temporarily unavailable observation is silent.

Press `Ctrl-C` to stop.

## YouTube

YouTube handles need no credentials — the watcher reads the public
`youtube.com/@<handle>/live` page. Any currently-live channel works.

## Twitch

Twitch viewer counts come from the public Helix `Get Streams` API, which works
for any live channel by login. It needs a one-time Twitch application for an
app-only access token (no per-user browser login):

1. Create an application at <https://dev.twitch.tv/console/apps>.
2. Copy its **Client ID** and generate a **Client Secret**.
3. Export both before running:

   ```bash
   export TWITCH_CLIENT_ID=…
   export TWITCH_CLIENT_SECRET=…
   ```

The watcher exchanges these for an application token via the
`client_credentials` grant and refreshes it automatically. If a `--twitch`
handle is requested without both variables set, it exits with a message naming
the missing one.

## Options

```bash
.venv/bin/youtube-live-count-chime \
  -y @mkbhd -t shroud \
  --up-sound /path/to/increase.aiff \
  --down-sound /path/to/decrease.aiff \
  --poll-interval 10
```

Run `.venv/bin/youtube-live-count-chime --help` for the full command reference.

## Limitations

Each platform exposes a current total, not individual join and departure events.
A join and a departure between polls can cancel out, and only the resulting
displayed count is observable. If a page or API response cannot be parsed, the
watcher warns and retains the previous valid count rather than producing a false
chime. Monitoring is limited to publicly live channels; ended streams read as
offline and reset that channel's baseline.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy
```
