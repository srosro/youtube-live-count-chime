#!/usr/bin/env bash
#
# Install a macOS LaunchAgent that runs the watcher at login and restarts it if
# it exits. Pass the channel flags you want watched as arguments:
#
#   scripts/install-launchagent.sh -y @mkbhd -y @LinusTechTips
#
# For Twitch channels, point --env-file at a file that exports
# TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET (e.g. the twitch.env from the README's
# Twitch section); it is copied, owner-only, into the service's config and read
# at runtime — never written into the plist:
#
#   scripts/install-launchagent.sh --env-file twitch.env -t shroud
#
# Re-run to update channels or credentials.
set -euo pipefail

[ "$(uname)" = "Darwin" ] || { echo "launchd is macOS-only." >&2; exit 1; }

# Pull --env-file out of the arguments; everything else is a watcher flag.
usage() { echo "usage: $0 [--env-file PATH] <channel flags, e.g. -y @handle -t login>"; }
src_env=""
watch_args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h | --help | --h*) usage; exit 0 ;;
        # --check and any argparse abbreviation of it make the watcher exit
        # without running — baked into the agent they would respawn-loop under
        # KeepAlive. (--check is the only --c* watcher flag; --help the only --h*.)
        --check | --c*)
            echo "$1 makes the watcher exit without watching — omit it" >&2; exit 2 ;;
        --env-file) [ "$#" -ge 2 ] || { echo "--env-file needs a path" >&2; exit 2; }
            src_env="$2"; shift 2 ;;
        --env-file=*) src_env="${1#--env-file=}"; shift ;;
        *) watch_args+=("$1"); shift ;;
    esac
done
[ "${#watch_args[@]}" -gt 0 ] || { usage >&2; exit 2; }
set -- "${watch_args[@]}"

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

if [ -n "$src_env" ]; then
    [ -r "$src_env" ] || { echo "--env-file not readable: $src_env" >&2; exit 1; }
    src_env="$(CDPATH= cd -- "$(dirname "$src_env")" && pwd)/$(basename "$src_env")"
fi

# Validate the whole configuration through the canonical CLI before changing
# anything on disk — otherwise a bad reinstall could overwrite working
# credentials, or KeepAlive could respawn an agent that just exits. `--check`
# runs the same parse_config/build_sources the watcher uses, so a malformed
# flag, a bad handle, or missing/empty Twitch credentials all fail here.
# (It cannot check that credentials actually authenticate or that a handle is
# live — those surface as a logged warning at runtime.) The subshell mirrors
# the agent's runtime environment: cd to the WorkingDirectory the plist sets
# below (so a relative sound path validates the same way it will resolve at
# runtime) and the two vars unset before sourcing the candidate file (so the
# installing shell's exported credentials can't mask an empty file).
candidate_env="${src_env:-$env_file}"
( set +e
    cd "$HOME"
    unset TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET
    set -a
    [ -r "$candidate_env" ] && source "$candidate_env"
    set +a
    "$bin" --check "$@" >/dev/null ) || {
    echo "Configuration is invalid — nothing was changed (see the error above)." >&2
    if [ -z "$src_env" ] && printf '%s\n' "$@" | grep -qE '^(-t|--t)'; then
        echo "For Twitch, pass --env-file with a file exporting your credentials" >&2
        echo "(launchd does not see variables you export in this shell)." >&2
    fi
    exit 1
}

# Validation passed; now commit to disk.
mkdir -p "$conf_dir" "$HOME/Library/LaunchAgents" "$(dirname "$log")"
chmod 700 "$conf_dir" # this directory holds the credentials file
# Copy the credentials in (unless --env-file already points at the destination).
if [ -n "$src_env" ] && ! [ "$src_env" -ef "$env_file" ]; then
    install -m 600 -- "$src_env" "$env_file"
fi

# Write both files to temp paths first, so the still-running old agent never
# reads a half-written wrapper and an interrupted write can't leave a corrupt
# plist. Each temp sits next to its destination, so the mv is a same-directory
# rename (atomic); the plist temp ends in .tmp.<pid>, not .plist, so launchd
# never loads it.
wrapper_tmp="$wrapper.tmp.$$"
plist_tmp="$plist.tmp.$$"
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
    <key>WorkingDirectory</key><string>$HOME</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$log</string>
    <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
PLIST

# Unload the old instance (reading the on-disk old plist), then swap both new
# files into place and load. A failed write leaves the previous files intact.
launchctl unload "$plist" 2>/dev/null || true
mv -f "$wrapper_tmp" "$wrapper"
mv -f "$plist_tmp" "$plist"
launchctl load -w "$plist"

echo "Loaded $label."
echo "  logs:  tail -f $log"
echo "  stop:  launchctl unload -w $plist"
