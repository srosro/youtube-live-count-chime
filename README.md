# YouTube Live Count Chime

A small, strictly typed macOS command-line watcher that plays one sound when a
YouTube livestream's displayed viewer count increases and a different sound when
it decreases.

## Requirements

- macOS
- Python 3.11 or newer
- Internet access to the public YouTube watch page

The watcher has no third-party runtime dependencies. Development type checking
uses mypy.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Run

The configured livestream is the default, and it polls every five seconds:

```bash
.venv/bin/youtube-live-count-chime
```

The first valid count is a silent baseline. After that:

- an increase plays `/System/Library/Sounds/Glass.aiff`;
- a decrease plays `/System/Library/Sounds/Basso.aiff`;
- an unchanged or invalid observation is silent.

Press `Ctrl-C` to stop.

## Options

Pass the livestream URL explicitly:

```bash
.venv/bin/youtube-live-count-chime \
  'https://www.youtube.com/watch?v=zUMYDcYRsFg'
```

Use different audio files:

```bash
.venv/bin/youtube-live-count-chime \
  --up-sound /path/to/increase.aiff \
  --down-sound /path/to/decrease.aiff
```

Run `.venv/bin/youtube-live-count-chime --help` for the full command reference.

## Limitations

YouTube exposes a current total, not individual join and departure events. A join
and departure that happen between polls can cancel each other out, and only the
resulting displayed count is observable. YouTube may also change its page format;
if parsing fails, the watcher warns and retains the previous valid count rather
than producing a false chime.

## Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy
```
