#!/usr/bin/env bash
#
# Install a macOS LaunchAgent that runs the watcher at login and restarts it if
# it exits. Pass the channel flags you want watched as arguments:
#
#   scripts/install-launchagent.sh -y @mkbhd -y @LinusTechTips -t shroud
#
# Re-run with new flags to update which channels are watched. Twitch
# credentials are read at runtime from ~/.config/count-chime/env (a file you
# create with TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET) and are never written
# into the plist.
set -euo pipefail

[ "$(uname)" = "Darwin" ] || { echo "launchd is macOS-only." >&2; exit 1; }
[ "$#" -gt 0 ] || {
    echo "usage: $0 <channel flags, e.g. -y @handle -t login>" >&2
    exit 2
}

bin="$(command -v youtube-live-count-chime || true)"
[ -n "$bin" ] || {
    echo "youtube-live-count-chime not found on PATH — install it first" >&2
    echo "(e.g. into a venv: python3 -m venv .venv && .venv/bin/pip install .)." >&2
    exit 1
}
# Absolutise: the LaunchAgent runs from a different cwd, so a relative match
# from PATH (e.g. .venv/bin) would fail to launch. The cd runs in a subshell
# (doesn't change this script's cwd); CDPATH= and -- keep it from resolving
# via CDPATH or choking on a leading dash.
bin="$(CDPATH= cd -- "$(dirname "$bin")" && pwd)/$(basename "$bin")"

label="com.youtube-live-count-chime.watcher"
conf_dir="$HOME/.config/count-chime"
env_file="$conf_dir/env"
wrapper="$conf_dir/run.sh"
log="$HOME/Library/Logs/count-chime.log"
plist="$HOME/Library/LaunchAgents/$label.plist"

# If any Twitch channel is requested (--twitch, its unique prefixes, or -t),
# the credentials file must resolve to non-empty values — otherwise the agent
# exits 2 and KeepAlive respawns it forever. Fail fast at install time instead.
# The subshell first unsets the two vars so it validates the file's values in
# isolation (matching launchd's minimal environment, not the installing shell),
# then sources the file the same way the runtime wrapper does. Placeholder
# values can't be detected here — those surface as a logged warning at runtime.
if printf '%s\n' "$@" | grep -qE '^(-t|--t)'; then
    [ -r "$env_file" ] || {
        echo "Twitch credentials file not found: $env_file" >&2
        echo "Create it first (see the README), then re-run." >&2
        exit 1
    }
    ( unset TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET
        set -a
        source "$env_file"
        [ -n "${TWITCH_CLIENT_ID:-}" ] && [ -n "${TWITCH_CLIENT_SECRET:-}" ]; ) \
        2>/dev/null || {
        echo "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set (non-empty) in" >&2
        echo "$env_file (see the README), then re-run." >&2
        exit 1
    }
fi

mkdir -p "$conf_dir" "$HOME/Library/LaunchAgents" "$(dirname "$log")"
chmod 700 "$conf_dir" # this directory holds the credentials file

# Wrapper: source credentials (kept out of the plist) then exec the watcher.
# printf %q quotes the binary path and every flag safely; write via a temp file
# and mv so the still-running old agent never reads a half-written script.
# Temp files live in conf_dir, not ~/Library/LaunchAgents, so a leftover temp
# is never scanned by launchd; the mv into place stays atomic (same filesystem).
wrapper_tmp="$wrapper.tmp.$$"
plist_tmp="$conf_dir/plist.tmp.$$"
trap 'rm -f "$wrapper_tmp" "$plist_tmp"' EXIT

# printf %q quotes the binary path and every flag safely.
{
    printf 'set -a\n[ -f %q ] && source %q\nset +a\nexec %q' \
        "$env_file" "$env_file" "$bin"
    printf ' %q' "$@"
    printf '\n'
} >"$wrapper_tmp"

cat >"$plist_tmp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$wrapper</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$log</string>
    <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
PLIST

# Both files are written to temp paths first, so the still-running old agent
# never reads a half-written wrapper and an interrupted write can't leave a
# corrupt plist. Unload the old instance (reading the on-disk old plist), then
# mv both new files into place atomically and load.
launchctl unload "$plist" 2>/dev/null || true
mv -f "$wrapper_tmp" "$wrapper"
mv -f "$plist_tmp" "$plist"
launchctl load -w "$plist"

echo "Loaded $label."
echo "  logs:  tail -f $log"
echo "  stop:  launchctl unload -w $plist"
