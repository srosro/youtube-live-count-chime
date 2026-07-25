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

label="com.youtube-live-count-chime.watcher"
conf_dir="$HOME/.config/count-chime"
env_file="$conf_dir/env"
wrapper="$conf_dir/run.sh"
log="$HOME/Library/Logs/count-chime.log"
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$conf_dir" "$HOME/Library/LaunchAgents" "$(dirname "$log")"

# Wrapper: source credentials (kept out of the plist) then exec the watcher.
# printf %q quotes the binary path and every flag safely.
{
    printf '#!/usr/bin/env bash\nset -a\n[ -f %q ] && source %q\nset +a\nexec %q' \
        "$env_file" "$env_file" "$bin"
    printf ' %q' "$@"
    printf '\n'
} >"$wrapper"
chmod +x "$wrapper"

cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$wrapper</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$log</string>
    <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
PLIST

launchctl unload "$plist" 2>/dev/null || true
launchctl load -w "$plist"

echo "Loaded $label."
echo "  logs:  tail -f $log"
echo "  stop:  launchctl unload -w $plist"
if printf '%s\n' "$@" | grep -q '^-t$'; then
    echo "  creds: ensure TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are in $env_file"
fi
