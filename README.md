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
- If your bar/struts differ, geometry may need tuning.

## Required niri patch

For pixel-accurate tracking during horizontal workspace-view panning, this overlay expects
`layout.tile_pos_in_workspace_view` to be populated for tiled windows in the scrolling layout.

On upstream `25.11`, this field may be `null` for scrolling tiles. In that case the overlay falls
back to heuristics, which is less reliable.

Patch applied in our setup:

- `niri/src/layout/scrolling.rs`
- function: `tiles_with_ipc_layouts()`
- set `WindowLayout.tile_pos_in_workspace_view` from current view-space tile coordinates.

Minimal flow to build and install patched niri:

```bash
cd ~/gits/GitHub/niri
cargo build --release
sudo install -Dm755 /usr/bin/niri /usr/bin/niri.backup.pre-ipc-tilepos
sudo install -m755 ./target/release/niri /usr/bin/niri
```

Then restart your niri session and verify:

```bash
niri msg --json windows | jq '.[0].layout.tile_pos_in_workspace_view'
```

It should return coordinates, not `null`.

## Contributing

Issues and PRs are welcome, especially for edge-case layout transitions.
