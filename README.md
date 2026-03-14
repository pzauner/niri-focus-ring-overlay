# niri-focus-ring-overlay

Animated external focus ring overlay for [niri](https://github.com/YaLTeR/niri).

Status: **alpha**. Expect bugs and edge-case breakage.

## What this is

- External ring overlay (native niri ring disabled)
- Click-through layer-shell window
- Event-driven updates via `niri msg --json event-stream`
- Hides while niri overview is open
- Theme color sync from `~/.config/niri/dms/colors.kdl`

## Install

```bash
./install.sh
systemctl --user daemon-reload
systemctl --user enable --now niri-focus-ring.service
```

## Update

```bash
cp bin/niri-focus-ring-daemon.py ~/.local/bin/niri-focus-ring-daemon.py
cp systemd/niri-focus-ring.service ~/.config/systemd/user/niri-focus-ring.service
systemctl --user daemon-reload
systemctl --user restart niri-focus-ring.service
```

## Uninstall

```bash
systemctl --user disable --now niri-focus-ring.service
rm -f ~/.config/systemd/user/niri-focus-ring.service
rm -f ~/.local/bin/niri-focus-ring-daemon.py
systemctl --user daemon-reload
```

## Notes

- Tested with niri `25.11`.
- This project uses heuristics because niri IPC currently returns `tile_pos_in_workspace_view: null` in this setup.
- If your bar/struts differ, geometry may need tuning.

## Contributing

Issues and PRs are welcome, especially for edge-case layout transitions.
