#!/usr/bin/env bash
set -euo pipefail

install -Dm755 bin/niri-focus-ring-daemon.py "$HOME/.local/bin/niri-focus-ring-daemon.py"
install -Dm644 systemd/niri-focus-ring.service "$HOME/.config/systemd/user/niri-focus-ring.service"

echo "Installed daemon and service files."
echo "Run: systemctl --user daemon-reload && systemctl --user enable --now niri-focus-ring.service"
