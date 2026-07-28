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
`-t`/`--twitch` (login) flags. It polls every five seconds:

```bash
.venv/bin/youtube-live-count-chime -y @srosrosr -y @watchmepivot -t watchmepivot -t samtriestobuild
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
3. Put both in a git-ignored file you `source` (rather than typing the secret
   inline, where it lands in shell history):

   ```bash
   # twitch.env (already git-ignored) — then: source twitch.env
   export TWITCH_CLIENT_ID="your-client-id"
   export TWITCH_CLIENT_SECRET="your-client-secret"
   ```

The watcher exchanges these for an application token via the
`client_credentials` grant and refreshes it automatically. If a `--twitch`
handle is requested without both variables set, it exits with a message naming
the missing one.

## Example: watch a specific set of channels

Say you want to monitor these four channels:

| URL | Flag |
| --- | --- |
| `https://www.twitch.tv/watchmepivot` | `-t watchmepivot` |
| `https://www.twitch.tv/samtriestobuild` | `-t samtriestobuild` |
| `https://www.youtube.com/@srosrosr` | `-y @srosrosr` |
| `https://www.youtube.com/@watchmepivot` | `-y @watchmepivot` |

The login/handle is the last path segment of the URL — the Twitch name after
`twitch.tv/`, and the YouTube handle after `youtube.com/` (keep the `@`).

Because two of these are on Twitch, set up the Twitch app once (see
[Twitch](#twitch) above), then:

```bash
source twitch.env   # exports TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET

.venv/bin/youtube-live-count-chime \
  -t watchmepivot \
  -t samtriestobuild \
  -y @srosrosr \
  -y @watchmepivot
```

On start it prints one line naming every channel it is watching, then runs until
`Ctrl-C`. Each channel's first live count is a silent baseline; after that, a
rise plays Glass and a fall plays Basso. A channel that isn't live never chimes
— an offline Twitch channel is fully silent, while an offline YouTube channel
logs one warning per outage and then goes quiet (see
[Limitations](#limitations)) — and it starts chiming automatically once it goes
live. (If you only wanted the two YouTube channels, drop the `-t` flags and no
Twitch credentials are needed at all.)

## Options

```bash
.venv/bin/youtube-live-count-chime \
  -y @srosrosr -t watchmepivot \
  --up-sound /path/to/increase.aiff \
  --down-sound /path/to/decrease.aiff
```

Run `.venv/bin/youtube-live-count-chime --help` for the full command reference.

## Announcements and notifications

A rise in the viewer count is announced out loud and also posts a macOS
notification. Both say the same sentence — `2 new viewers on twitch
watchmepivot` — spoken through the macOS `say` voice and used as the banner
title. The announcement is the signal that survives streaming: OBS suppresses
notification banners while it captures the screen, so while you are live the
banner never displays, but audio is not suppressed.

The order on a rise is chime, then announcement, then banner. A rise briefly
serializes audio across every watched channel, so two channels rising at once
never talk over each other. Only a rise pays that: an unchanged poll costs
nothing at all, and a fall pays only the chime it has always paid.

The notification body is a digest of every channel being watched, always in the
same order:

```
twitch watchmepivot 3 · youtube srosrosr 4 · twitch offchannel offline
```

The digest is fixed-shape, so a channel never disappears from it: a channel that
has not been polled yet — or whose last poll failed — reads `?` rather than a
stale number. `offline` is reachable only where the platform says so, which
today means Twitch; a YouTube channel that isn't live is indistinguishable from
a failed poll and also reads `?` (see [Limitations](#limitations)). A fall
chimes only — it is neither announced nor posted.

Notifications go through `osascript`, so the first rise may prompt for
notification permission. If the announcement or the banner fails, the watcher
logs a warning and keeps watching; the chime always plays first and never waits
on either, and a failed announcement still leaves the banner posted.

## Run at login (macOS)

To keep the watcher running automatically, install it as a launchd
LaunchAgent. From a checkout of this repo, with the package installed and
`youtube-live-count-chime` on your `PATH` (activate the venv, or use its
`.venv/bin`), run the helper script with the channels you want watched:

```bash
scripts/install-launchagent.sh -y @srosrosr -y @watchmepivot
```

For Twitch channels, point `--env-file` at a file that exports your
`TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` — the `twitch.env` from the
[Twitch](#twitch) section works directly. It is copied into the service's
config with owner-only permissions and read at runtime, never written into the
plist:

```bash
scripts/install-launchagent.sh --env-file twitch.env \
  -y @srosrosr -t watchmepivot -t samtriestobuild
```

The LaunchAgent (`~/Library/LaunchAgents/com.youtube-live-count-chime.watcher.plist`)
starts at login and restarts the watcher if it exits, logging to
`~/Library/Logs/count-chime.log`. Because it runs in your GUI session, the
chimes play through your speakers.

Re-run the script to change channels or refresh credentials. To stop it:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.youtube-live-count-chime.watcher.plist
```

## Limitations

Each platform exposes a current total, not individual join and departure events.
A join and a departure between polls can cancel out, and only the resulting
displayed count is observable. Monitoring is limited to publicly live channels.

A failed poll — an unreachable page, or an unparseable YouTube page or Helix
response — makes that channel's count *unknown*: it never chimes on its own, it
renders as `?` in the notification digest, and its chime baseline is cleared, so
the first poll back re-baselines silently instead of chiming the whole gap-wide
drift. What differs is an *ended* stream: the Twitch source receives an explicit
offline signal and reports the channel as `offline`, while the YouTube source
cannot tell an ended stream from an unparseable page and reports both the same
way: one warning at the start of the outage, then silence until a poll succeeds.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy
```

## License

Released under the [MIT License](LICENSE).
