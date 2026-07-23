# YouTube Live Count Chime Design

## Goal

Create a macOS command-line watcher for the YouTube livestream at
`https://www.youtube.com/watch?v=zUMYDcYRsFg`. The watcher polls the public page,
detects changes in the displayed live viewer count, and plays one sound when the
count increases and a different sound when it decreases.

## Scope

The first valid count establishes a silent baseline. Each later observed change
produces exactly one chime, regardless of the size of the change. An unchanged
count produces no sound.

The tool observes count changes, not individual join and leave events. YouTube
only exposes a current total, so simultaneous joins and departures that result in
the same total cannot be detected.

The tool is a foreground command-line program. A menu-bar application,
background launch agent, graphical interface, and YouTube Data API integration
are outside this version's scope.

## Repository

The local repository will be:

`/Users/so/Hacking/youtube-live-count-chime`

It will use a `main` branch and be published to a private personal GitHub
repository named `youtube-live-count-chime`.

## Architecture

The project will target Python 3.11 or newer and use no third-party runtime
dependencies.

- `youtube.py` fetches the public watch page with a browser-like user agent and
  extracts the live viewer count.
- `monitor.py` owns polling and count-transition detection. It emits a typed
  transition only when two valid consecutive observations differ.
- `sounds.py` plays a supplied audio file through macOS `/usr/bin/afplay`.
- `cli.py` parses options, wires the components together, prints status, and
  handles shutdown.

The code will live in a `src/youtube_live_count_chime` package. Tests will live
under `tests`.

## Data Flow and Behavior

1. The CLI resolves the URL, polling interval, and sound paths.
2. The watcher fetches and parses the first valid count, prints it, and stores it
   without playing a sound.
3. The watcher polls every five seconds by default.
4. If a new valid count is greater than the previous valid count, it prints the
   transition and plays the increase sound once.
5. If the count is lower, it prints the transition and plays the decrease sound
   once.
6. If the count is unchanged, it does nothing.
7. After a valid observation, the watcher stores the new count for the next
   comparison.

The default increase sound will be
`/System/Library/Sounds/Glass.aiff`. The default decrease sound will be
`/System/Library/Sounds/Basso.aiff`.

The command will accept an optional URL plus `--interval`, `--up-sound`, and
`--down-sound`. The supplied livestream URL and the two macOS system sounds will
be the defaults.

## Error Handling

Network errors, non-successful HTTP responses, and unrecognized page content
will produce concise warnings. They will not play a sound or replace the last
valid count. The watcher will continue polling so temporary failures recover
automatically.

Invalid CLI values, such as a non-positive interval or a missing sound file, will
fail immediately with a clear message. `Ctrl-C` will stop the watcher cleanly.

If YouTube changes the watch-page representation, the parser will fail visibly
instead of treating an unknown value as zero and causing a false decrease alert.

## Typing and Testability

All production functions and meaningful state will be type-annotated. Typed
dataclasses, enums, and protocols will represent observations, transition
direction, and injected fetch/sound behavior where appropriate. The project will
pass `mypy --strict`.

Tests will use Python's standard `unittest` framework and local HTML fixtures.
They will cover:

- extraction of valid live counts;
- rejection of missing or malformed counts;
- silent initial baseline;
- one increase notification for any upward change;
- one decrease notification for any downward change;
- no notification for an unchanged count;
- preservation of the last valid count across fetch or parse failures;
- CLI validation for interval and sound files.

Implementation will follow test-driven development: each behavior test must fail
for the expected reason before its production code is added.

## Documentation and Verification

The README will document prerequisites, setup, usage, customization, limitations,
and how to stop the watcher. The final verification will run the complete unit
test suite, strict type checking, CLI help, and a live fetch/parsing smoke test
against the supplied YouTube URL.

After local verification, the implementation will be committed, the private
GitHub repository will be created with the GitHub CLI, and `main` will be pushed.

## Acceptance Criteria

- The first count does not produce a chime.
- Every observed upward count change produces exactly one increase chime.
- Every observed downward count change produces exactly one decrease chime.
- Unchanged or invalid observations produce no chime.
- The defaults work on macOS without third-party runtime packages.
- Tests and strict type checking pass.
- The completed `main` branch is available in the private personal GitHub
  repository.
