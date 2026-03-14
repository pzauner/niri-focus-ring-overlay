#!/usr/bin/env python3
import json
import math
import subprocess
from pathlib import Path
import re
import time
import threading
import os

import gi
import cairo

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GLib, Gtk, Gdk, GtkLayerShell


RING_WIDTH = 3.0
RADIUS = 12.0
PADDING = 2.0
GAP_ESTIMATE = 4.0
POLL_MS = 60
FRAME_MS = 16
IDLE_FRAME_MS = 140
SMOOTH_TIME_MS = 55.0
DMS_SETTINGS = Path.home() / ".config" / "DankMaterialShell" / "settings.json"
DMS_COLORS_KDL = Path.home() / ".config" / "niri" / "dms" / "colors.kdl"
NIRI_CONFIG = Path.home() / ".config" / "niri" / "config.kdl"
THEME_REFRESH_MS = 1000
WATCHDOG_MS = 2000
PROBE_MS = 50
PROBE_WINDOW_MS = 2200
DEBUG_LOG = Path.home() / ".cache" / "niri-focus-ring-debug.log"


def run_json(args):
    out = subprocess.check_output(args, text=True, timeout=0.35)
    return json.loads(out)


def rounded_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    x2, y2 = x + w, y + h
    cr.new_sub_path()
    cr.arc(x2 - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x2 - r, y2 - r, r, 0, math.pi / 2)
    cr.arc(x + r, y2 - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def build_column_positions(widths_by_idx):
    starts = {}
    x = GAP_ESTIMATE
    for idx in sorted(widths_by_idx.keys()):
        starts[idx] = x
        x += widths_by_idx[idx] + GAP_ESTIMATE
    total = x
    return starts, total


def parse_layout_tile(window):
    layout = window.get("layout") if isinstance(window, dict) else None
    if not isinstance(layout, dict):
        return None
    pos = layout.get("pos_in_scrolling_layout")
    size = layout.get("tile_size")
    if (
        not isinstance(pos, (list, tuple))
        or len(pos) < 2
        or pos[0] is None
        or pos[1] is None
        or not isinstance(size, (list, tuple))
        or len(size) < 2
        or size[0] is None
        or size[1] is None
    ):
        return None
    try:
        return (int(pos[0]), int(pos[1]), float(size[0]), float(size[1]))
    except Exception:
        return None


def parse_layout_view_pos(window):
    layout = window.get("layout") if isinstance(window, dict) else None
    if not isinstance(layout, dict):
        return None
    p = layout.get("tile_pos_in_workspace_view")
    if not isinstance(p, (list, tuple)) or len(p) < 2 or p[0] is None or p[1] is None:
        return None
    try:
        return (float(p[0]), float(p[1]))
    except Exception:
        return None


def frame_ms_from_output(output):
    # niri reports refresh_rate in mHz (e.g. 60001 => 60.001 Hz).
    try:
        modes = output.get("modes")
        idx = int(output.get("current_mode", 0))
        if isinstance(modes, list) and 0 <= idx < len(modes):
            rr = float(modes[idx].get("refresh_rate", 0.0))
            if rr > 1000.0:
                hz = rr / 1000.0
            else:
                hz = rr
            if hz > 1.0:
                ms = int(round(1000.0 / hz))
                return max(6, min(33, ms))
    except Exception:
        pass
    return FRAME_MS


def estimate_dms_bar_insets():
    # Returns logical insets: (left, top, right, bottom).
    # Position mapping in DMS: 0=top, 1=bottom, 2=left, 3=right.
    if not DMS_SETTINGS.exists():
        return (0.0, 0.0, 0.0, 0.0)
    try:
        data = json.loads(DMS_SETTINGS.read_text(encoding="utf-8"))
        bars = data.get("barConfigs", [])
        if not bars:
            return (0.0, 0.0, 0.0, 0.0)
        b = bars[0]
        if not b.get("enabled", True) or not b.get("visible", True):
            return (0.0, 0.0, 0.0, 0.0)

        icon_scale = float(b.get("iconScale", 1.0))
        font_scale = float(b.get("fontScale", 1.0))
        inner = float(b.get("innerPadding", 8))
        widget = float(b.get("widgetPadding", 6))
        spacing = float(b.get("spacing", 4))
        border = float(b.get("borderThickness", 0)) if b.get("borderEnabled", False) else 0.0

        # Conservative estimate (previous one was too large on your setup).
        thickness = (24.0 * icon_scale) + (6.0 * font_scale) + (2.0 * inner) + widget + spacing + border + 6.0
        thickness = max(0.0, min(120.0, thickness))

        pos = int(b.get("position", 0))
        if pos == 2:
            return (thickness, 0.0, 0.0, 0.0)
        if pos == 3:
            return (0.0, 0.0, thickness, 0.0)
        if pos == 1:
            return (0.0, 0.0, 0.0, thickness)
        return (0.0, thickness, 0.0, 0.0)
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def estimate_niri_strut_insets():
    # Parse explicit struts from main niri config; this is part of layout geometry.
    if not NIRI_CONFIG.exists():
        return (0.0, 0.0, 0.0, 0.0)
    try:
        text = NIRI_CONFIG.read_text(encoding="utf-8")
        m = re.search(r"struts\s*\{([^}]*)\}", text, re.S)
        if not m:
            return (0.0, 0.0, 0.0, 0.0)
        body = m.group(1)
        def parse(name):
            mm = re.search(rf"{name}\s+(-?[0-9]+(?:\.[0-9]+)?)", body)
            return float(mm.group(1)) if mm else 0.0
        return (parse("left"), parse("top"), parse("right"), parse("bottom"))
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def hex_to_rgb(color, fallback=(0.53, 0.69, 0.94)):
    try:
        c = color.strip().lstrip("#")
        if len(c) == 6:
            r = int(c[0:2], 16) / 255.0
            g = int(c[2:4], 16) / 255.0
            b = int(c[4:6], 16) / 255.0
            return (r, g, b)
        if len(c) == 8:
            r = int(c[0:2], 16) / 255.0
            g = int(c[2:4], 16) / 255.0
            b = int(c[4:6], 16) / 255.0
            return (r, g, b)
    except Exception:
        pass
    return fallback


class FocusRing:
    def __init__(self):
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_accept_focus(False)
        self.win.set_sensitive(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.stick()
        self.win.set_app_paintable(True)
        self.win.connect("realize", self.on_realize)

        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.win.set_visual(visual)

        GtkLayerShell.init_for_window(self.win)
        GtkLayerShell.set_namespace(self.win, "niri-focus-ring")
        GtkLayerShell.set_layer(self.win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self.win, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self.win, -1)
        for edge in (
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
        ):
            GtkLayerShell.set_anchor(self.win, edge, True)

        self.area = Gtk.DrawingArea()
        self.area.set_app_paintable(True)
        self.area.set_sensitive(False)
        self.area.connect("draw", self.on_draw)
        self.win.add(self.area)
        self.win.show_all()

        self.current = [200.0, 200.0, 400.0, 300.0]
        self.target = [200.0, 200.0, 400.0, 300.0]

        self.prev_workspace_id = None
        self.scroll_x = None
        self.workspace_scroll_x = {}
        self.ring_rgb = (0.53, 0.69, 0.94)
        self.glow_rgb = (0.40, 0.62, 0.92)
        self._theme_mtime = 0.0
        self._theme_next_check_ms = 0
        self._overview_is_open = False
        self._snapshot_pending = False
        self._needs_redraw = True
        self._event_proc = None
        self._event_thread = None
        self._event_last_ms = 0
        self.cached_output = {}
        self.cached_workspaces = []
        self.cached_windows = []
        self.active_frame_ms = FRAME_MS
        self.idle_frame_ms = IDLE_FRAME_MS
        self._last_anim_ms = int(time.monotonic() * 1000)
        self._last_event_name = "startup"
        self._probe_until_ms = 0
        self._last_debug_ms = 0
        self.debug_enabled = os.environ.get("NIRI_FOCUS_RING_DEBUG", "1") not in ("0", "false", "False")
        self.visible = True

        if self.debug_enabled:
            try:
                DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
                with DEBUG_LOG.open("a", encoding="utf-8") as f:
                    f.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ===\n")
            except Exception:
                pass

        self.full_snapshot()
        self.start_event_stream()
        GLib.timeout_add(WATCHDOG_MS, self.watchdog_tick)
        GLib.timeout_add(PROBE_MS, self.probe_tick)
        GLib.timeout_add(FRAME_MS, self.animate)

    def on_realize(self, widget):
        # Hard click-through: empty input shape + pass-through flag.
        empty = cairo.Region()
        widget.input_shape_combine_region(empty)
        gdk_win = widget.get_window()
        if gdk_win is not None:
            gdk_win.set_pass_through(True)
            gdk_win.input_shape_combine_region(empty, 0, 0)

    def get_workarea_for_output(self, out_logical):
        # Fallback: full logical output area.
        ox = float(out_logical.get("x", 0.0))
        oy = float(out_logical.get("y", 0.0))
        ow = float(out_logical.get("width", 0.0))
        oh = float(out_logical.get("height", 0.0))
        best = (ox, oy, ow, oh)

        display = Gdk.Display.get_default()
        if display is None:
            return best

        try:
            n = display.get_n_monitors()
        except Exception:
            return best

        best_score = None
        for i in range(n):
            m = display.get_monitor(i)
            if m is None:
                continue
            g = m.get_geometry()
            wa = m.get_workarea()
            # Match monitor by geometry proximity.
            score = abs(g.x - ox) + abs(g.y - oy) + abs(g.width - ow) + abs(g.height - oh)
            if best_score is None or score < best_score:
                best_score = score
                best = (float(wa.x), float(wa.y), float(wa.width), float(wa.height))

        return best

    def on_draw(self, _widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        x, y, w, h = self.current
        if not self.visible:
            return False

        cr.set_source_rgba(self.glow_rgb[0], self.glow_rgb[1], self.glow_rgb[2], 0.20)
        cr.set_line_width(RING_WIDTH + 3.0)
        rounded_rect(cr, x - 1.0, y - 1.0, w + 2.0, h + 2.0, RADIUS + 1.0)
        cr.stroke()

        cr.set_source_rgba(self.ring_rgb[0], self.ring_rgb[1], self.ring_rgb[2], 0.92)
        cr.set_line_width(RING_WIDTH)
        rounded_rect(cr, x, y, w, h, RADIUS)
        cr.stroke()
        return False

    def refresh_theme_color(self):
        now_ms = int(time.monotonic() * 1000)
        if now_ms < self._theme_next_check_ms:
            return
        self._theme_next_check_ms = now_ms + THEME_REFRESH_MS
        try:
            mt = DMS_COLORS_KDL.stat().st_mtime
        except Exception:
            return
        if mt == self._theme_mtime:
            return
        self._theme_mtime = mt
        try:
            text = DMS_COLORS_KDL.read_text(encoding="utf-8")
            m = re.search(r'focus-ring\\s*\\{[^}]*active-color\\s+\"(#[0-9A-Fa-f]{6,8})\"', text, re.S)
            if not m:
                m = re.search(r'active-color\\s+\"(#[0-9A-Fa-f]{6,8})\"', text)
            rgb = hex_to_rgb(m.group(1) if m else "#87afef")
            self.ring_rgb = rgb
            self.glow_rgb = (min(1.0, rgb[0] * 0.85 + 0.12), min(1.0, rgb[1] * 0.85 + 0.12), min(1.0, rgb[2] * 0.85 + 0.12))
            self._needs_redraw = True
        except Exception:
            pass

    def full_snapshot(self):
        try:
            self.cached_output = run_json(["niri", "msg", "--json", "focused-output"])
            self.active_frame_ms = frame_ms_from_output(self.cached_output)
            self.idle_frame_ms = max(IDLE_FRAME_MS, self.active_frame_ms * 8)
            self.cached_workspaces = run_json(["niri", "msg", "--json", "workspaces"])
            self.cached_windows = run_json(["niri", "msg", "--json", "windows"])
            st = run_json(["niri", "msg", "--json", "overview-state"])
            self._overview_is_open = bool(st.get("is_open", False))
            self._event_last_ms = int(time.monotonic() * 1000)
            self.recompute_target()
        except Exception:
            pass

    def dlog(self, msg):
        if not self.debug_enabled:
            return
        try:
            now_ms = int(time.monotonic() * 1000)
            # Avoid spamming identical hot paths too fast.
            if now_ms - self._last_debug_ms < 25 and msg.startswith("geom "):
                return
            self._last_debug_ms = now_ms
            with DEBUG_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    def schedule_snapshot(self, delay_ms=40):
        if self._snapshot_pending:
            return
        self._snapshot_pending = True

        def _run():
            self._snapshot_pending = False
            self.full_snapshot()
            return False

        GLib.timeout_add(delay_ms, _run)

    def start_event_stream(self):
        try:
            if self._event_proc is not None and self._event_proc.poll() is None:
                return
            self._event_proc = subprocess.Popen(
                ["niri", "msg", "--json", "event-stream"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            self._event_proc = None
            self.schedule_snapshot(120)
            return

        def _reader():
            proc = self._event_proc
            if proc is None or proc.stdout is None:
                return
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                GLib.idle_add(self.handle_event_line, line)
            GLib.idle_add(self.on_event_stream_closed)

        self._event_thread = threading.Thread(target=_reader, daemon=True)
        self._event_thread.start()

    def on_event_stream_closed(self):
        self.schedule_snapshot(100)
        # Restart stream shortly after close/failure.
        GLib.timeout_add(250, self._restart_event_stream_once)
        return False

    def _restart_event_stream_once(self):
        self.start_event_stream()
        return False

    def handle_event_line(self, line):
        self._event_last_ms = int(time.monotonic() * 1000)
        try:
            ev = json.loads(line)
            if not isinstance(ev, dict) or not ev:
                return False
            name = next(iter(ev.keys()))
            self._last_event_name = name
            self._probe_until_ms = int(time.monotonic() * 1000) + PROBE_WINDOW_MS
            payload = ev.get(name) or {}
            self.dlog(f"event {name}")
            if name == "WorkspacesChanged":
                ws = payload.get("workspaces")
                if isinstance(ws, list):
                    self.cached_workspaces = ws
                self.recompute_target()
                return False
            if name == "WindowsChanged":
                wins = payload.get("windows")
                if isinstance(wins, list):
                    self.cached_windows = wins
                self.recompute_target()
                return False
            if name == "WindowLayoutsChanged":
                if self.apply_window_layout_changes(payload):
                    self.recompute_target()
                else:
                    self.dlog(f"event WindowLayoutsChanged no-apply keys={list(payload.keys()) if isinstance(payload, dict) else type(payload)}")
                    self.schedule_snapshot(25)
                return False
            if name == "WindowOpenedOrChanged":
                if self.apply_window_opened_or_changed(payload):
                    self.recompute_target()
                else:
                    self.schedule_snapshot(20)
                return False
            if name == "WindowClosed":
                if self.apply_window_closed(payload):
                    self.recompute_target()
                else:
                    self.schedule_snapshot(20)
                return False
            if name == "WorkspaceActiveWindowChanged":
                ws_id = payload.get("workspace_id")
                awid = payload.get("active_window_id")
                changed = False
                if ws_id is not None:
                    for ws in self.cached_workspaces:
                        if ws.get("id") == ws_id:
                            if ws.get("active_window_id") != awid:
                                ws["active_window_id"] = awid
                                changed = True
                            break
                if changed:
                    self.recompute_target()
                else:
                    self.schedule_snapshot(20)
                return False
            if name == "WindowFocusChanged":
                fid = payload.get("id")
                changed = False
                for w in self.cached_windows:
                    want = (w.get("id") == fid) if fid is not None else False
                    if bool(w.get("is_focused")) != want:
                        w["is_focused"] = want
                        changed = True
                if changed:
                    self.recompute_target()
                else:
                    self.schedule_snapshot(15)
                return False
            if name == "WorkspaceActivated":
                self.schedule_snapshot(20)
                return False
            if name == "OverviewOpenedOrClosed":
                self._overview_is_open = bool(payload.get("is_open", False))
                self.recompute_target()
                return False
            if name == "WindowFocusTimestampChanged":
                # Focus changed: get fresh window focus flags once.
                self.schedule_snapshot(15)
                return False

            # Any other structural event: coalesced snapshot fallback.
            self.schedule_snapshot(40)
        except Exception:
            self.schedule_snapshot(80)
        return False

    def apply_window_layout_changes(self, payload):
        # Accept multiple payload shapes to stay compatible across niri versions.
        entries = []
        if isinstance(payload, dict):
            for key in ("changes", "layouts", "window_layouts", "windows"):
                val = payload.get(key)
                if isinstance(val, list):
                    for e in val:
                        if isinstance(e, dict):
                            entries.append(e)
                            continue
                        # niri currently sends changes as tuple-like arrays:
                        # [[window_id, {layout...}], ...]
                        if (
                            isinstance(e, (list, tuple))
                            and len(e) == 2
                            and isinstance(e[0], int)
                            and isinstance(e[1], dict)
                        ):
                            entries.append({"id": e[0], "layout": e[1]})
            if isinstance(payload.get("id"), int) and isinstance(payload.get("layout"), dict):
                entries.append({"id": payload.get("id"), "layout": payload.get("layout")})
        if not entries:
            return False

        by_id = {w.get("id"): w for w in self.cached_windows if isinstance(w, dict)}
        changed = False
        for e in entries:
            wid = e.get("id")
            lay = e.get("layout")
            if wid is None or not isinstance(lay, dict):
                continue
            w = by_id.get(wid)
            if w is None:
                continue
            w["layout"] = lay
            changed = True
        return changed

    def apply_window_opened_or_changed(self, payload):
        if not isinstance(payload, dict):
            return False
        w = payload.get("window")
        if not isinstance(w, dict):
            return False
        wid = w.get("id")
        if wid is None:
            return False
        for i, old in enumerate(self.cached_windows):
            if old.get("id") == wid:
                self.cached_windows[i] = w
                return True
        self.cached_windows.append(w)
        return True

    def apply_window_closed(self, payload):
        if not isinstance(payload, dict):
            return False
        wid = payload.get("id")
        if wid is None:
            return False
        old_len = len(self.cached_windows)
        self.cached_windows = [w for w in self.cached_windows if w.get("id") != wid]
        return len(self.cached_windows) != old_len

    def recompute_target(self):
        try:
            self.refresh_theme_color()
            if self._overview_is_open:
                if self.visible:
                    self._needs_redraw = True
                self.visible = False
                return True

            out = self.cached_output or {}
            workspaces = self.cached_workspaces or []
            windows = self.cached_windows or []

            focused_ws = None
            for ws in workspaces:
                if ws.get("is_focused"):
                    focused_ws = ws
                    break
            if not focused_ws:
                if self.visible:
                    self._needs_redraw = True
                self.visible = False
                return True

            focused = None
            # Prefer the compositor's currently focused window; it is the most
            # reliable source during rapid layout changes/reflows.
            for w in windows:
                if w.get("is_focused"):
                    focused = w
                    break

            # Fallback: use workspace active window if no global focused flag.
            if focused is None:
                active_on_ws = focused_ws.get("active_window_id")
                if active_on_ws is not None:
                    for w in windows:
                        if w.get("id") == active_on_ws:
                            focused = w
                            break

            # No focused/active window on this workspace: hide ring.
            if not focused:
                if self.visible:
                    self._needs_redraw = True
                self.visible = False
                self.prev_workspace_id = focused_ws.get("id")
                self.scroll_x = None
                return True

            parsed_focused = parse_layout_tile(focused)
            if parsed_focused is None:
                # During some transitions niri exposes a focused window without
                # tile geometry for a short moment; do not freeze the callback.
                if self.visible:
                    self._needs_redraw = True
                self.visible = False
                return True

            if not self.visible:
                self._needs_redraw = True
            self.visible = True
            col_idx, row_idx, fw, fh = parsed_focused
            focused_id = focused.get("id")

            out_logical = out.get("logical", {})
            wx, wy, ww, wh = self.get_workarea_for_output(out_logical)
            ox = float(out_logical.get("x", 0.0))
            oy = float(out_logical.get("y", 0.0))
            ow = float(out_logical.get("width", ww))
            oh = float(out_logical.get("height", wh))

            # If workarea is not reduced yet, apply DMS bar inset once.
            reduced = (abs(wx - ox) > 0.5) or (abs(wy - oy) > 0.5) or (abs(ww - ow) > 0.5) or (abs(wh - oh) > 0.5)
            bl = bt = br = bb = 0.0
            if not reduced:
                bl, bt, br, bb = estimate_dms_bar_insets()

            sl, st, sr, sb = estimate_niri_strut_insets()

            # Effective geometry area for the tiling view.
            wx += bl + sl
            wy += bt + st
            ww = max(1.0, ww - (bl + br + sl + sr))
            wh = max(1.0, wh - (bt + bb + st + sb))

            ws_id = focused.get("workspace_id")

            # Prefer exact compositor-provided tile position if present.
            # This path is crucial for smooth updates while horizontally panning
            # when the focused tile can stay visible across multiple viewports.
            view_pos = parse_layout_view_pos(focused)
            if view_pos is not None:
                # tile_pos_in_workspace_view is already in workspace-view coordinates,
                # so do not add workarea/bar/strut insets again.
                tx = view_pos[0] - PADDING
                ty = view_pos[1] - PADDING
                tw = fw + 2.0 * PADDING
                th = fh + 2.0 * PADDING
                self.target = [tx, ty, tw, th]
                self._needs_redraw = True
                self.dlog(
                    f"geom path=tile_pos ev={self._last_event_name} wid={focused_id} ws={ws_id} "
                    f"pos={col_idx},{row_idx} tile={fw:.1f}x{fh:.1f} tile_pos={view_pos[0]:.1f},{view_pos[1]:.1f} "
                    f"target={tx:.1f},{ty:.1f},{tw:.1f},{th:.1f}"
                )
                return True

            # Workspace columns for horizontal math.
            same_ws = [w for w in windows if w.get("workspace_id") == ws_id and not w.get("is_floating")]
            widths_by_idx = {}
            for w in same_ws:
                parsed = parse_layout_tile(w)
                if parsed is None:
                    continue
                cidx, _ridx, w_w, _w_h = parsed
                widths_by_idx[cidx] = w_w

            starts, total_w = build_column_positions(widths_by_idx)
            col_start = starts.get(col_idx, GAP_ESTIMATE)
            col_end = col_start + fw

            scroll_ww = max(1.0, ww)
            max_scroll = max(0.0, total_w - scroll_ww)
            center_offset = max(0.0, (scroll_ww - total_w) / 2.0)

            # Keep a virtual viewport with minimal motion when focus changes.
            if self.scroll_x is None or self.prev_workspace_id != ws_id:
                remembered = self.workspace_scroll_x.get(ws_id)
                if remembered is not None:
                    self.scroll_x = max(0.0, min(max_scroll, float(remembered)))
                else:
                    centered = col_start + fw / 2.0 - scroll_ww / 2.0
                    self.scroll_x = max(0.0, min(max_scroll, centered))
            else:
                if col_start < self.scroll_x:
                    self.scroll_x = col_start
                elif col_end > self.scroll_x + scroll_ww:
                    self.scroll_x = col_end - scroll_ww
                self.scroll_x = max(0.0, min(max_scroll, self.scroll_x))

            self.prev_workspace_id = ws_id
            self.workspace_scroll_x[ws_id] = self.scroll_x
            tx = wx + center_offset + (col_start - self.scroll_x) - PADDING

            # Vertical position from row order/heights in this column.
            same_col = []
            for w in same_ws:
                parsed = parse_layout_tile(w)
                if parsed is None:
                    continue
                cidx, ridx, w_w, w_h = parsed
                if cidx == col_idx:
                    same_col.append((ridx, w_h))
            same_col.sort(key=lambda item: item[0])

            # Estimate vertical gaps from actual row heights to avoid center drift on resize.
            sum_h = sum(h for _r, h in same_col)
            nrows = max(1, len(same_col))
            v_gap = max(0.0, (wh - sum_h) / (nrows + 1))
            col_top = wy

            y = col_top + v_gap
            for r, h in same_col:
                if r == row_idx:
                    break
                y += h + v_gap

            ty = y - PADDING
            tw = fw + 2.0 * PADDING
            th = fh + 2.0 * PADDING
            self.target = [tx, ty, tw, th]
            self._needs_redraw = True
            self.dlog(
                f"geom path=heuristic ev={self._last_event_name} wid={focused_id} ws={ws_id} "
                f"pos={col_idx},{row_idx} tile={fw:.1f}x{fh:.1f} scroll_x={(self.scroll_x or 0.0):.1f} "
                f"target={tx:.1f},{ty:.1f},{tw:.1f},{th:.1f}"
            )
        except Exception:
            # Keep timer alive even if niri returns transient unexpected state.
            if self.visible:
                self._needs_redraw = True
            self.visible = False
            self.dlog("geom exception")
        return True

    def probe_tick(self):
        # During likely touchpad panning phases, fetch focused-window layout
        # directly. This catches viewport updates that may arrive without
        # a full WindowsChanged event.
        now = int(time.monotonic() * 1000)
        if now <= self._probe_until_ms and not self._overview_is_open:
            try:
                fw = run_json(["niri", "msg", "--json", "focused-window"])
                fid = fw.get("id")
                if fid is not None:
                    changed = False
                    for i, old in enumerate(self.cached_windows):
                        if old.get("id") == fid:
                            old_layout = old.get("layout")
                            new_layout = fw.get("layout")
                            old_ws = old.get("workspace_id")
                            new_ws = fw.get("workspace_id")
                            if old_layout != new_layout or old_ws != new_ws or not old.get("is_focused", False):
                                self.cached_windows[i] = fw
                                changed = True
                            break
                    else:
                        self.cached_windows.append(fw)
                        changed = True
                    if changed:
                        self._last_event_name = "probe-focused-window"
                        self.recompute_target()
            except Exception:
                pass
        return True

    def watchdog_tick(self):
        # Low-frequency safety net: refresh theme + recover if stream stalls.
        self.refresh_theme_color()
        now_ms = int(time.monotonic() * 1000)
        if self._event_last_ms == 0 or (now_ms - self._event_last_ms) > (WATCHDOG_MS * 2):
            self.schedule_snapshot(10)
            if self._event_proc is None or self._event_proc.poll() is not None:
                self.start_event_stream()
        return True

    def animate(self):
        now_ms = int(time.monotonic() * 1000)
        dt_ms = max(1.0, min(120.0, float(now_ms - self._last_anim_ms)))
        self._last_anim_ms = now_ms
        alpha = 1.0 - math.exp(-dt_ms / SMOOTH_TIME_MS)

        changed = False
        for i in range(4):
            delta = self.target[i] - self.current[i]
            if abs(delta) > 0.05:
                self.current[i] += delta * alpha
                changed = True
            else:
                if self.current[i] != self.target[i]:
                    self.current[i] = self.target[i]
                    changed = True
        if changed or self._needs_redraw:
            self.area.queue_draw()
            self._needs_redraw = False
            GLib.timeout_add(self.active_frame_ms, self.animate)
        else:
            GLib.timeout_add(self.idle_frame_ms, self.animate)
        return False


def main():
    FocusRing()
    Gtk.main()


if __name__ == "__main__":
    main()
