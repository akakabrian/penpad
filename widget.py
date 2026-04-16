#!/usr/bin/env python3
"""penpad desktop widget — undecorated GTK3 + WebKit2 view pinned to the desktop.

Two separate top-level windows are used (one full-size, one tiny dot) and
visibility is toggled between them. Resizing a single window between sizes
caused muffin/Cinnamon to recentre it on every grow — show/hide of two fixed
windows sidesteps that entirely.

Defaults to bottom-right of the primary monitor. Override with env vars:
  PENPAD_URL       (default http://localhost:8767)
  PENPAD_W         widget width  (default workarea.width // 3)
  PENPAD_H         widget height (default workarea.height // 2 - margin)
  PENPAD_COLLAPSED collapsed dot size (default 44)
  PENPAD_MARGIN    edge margin   (default 0 — flush with the corner)
  PENPAD_ANCHOR    bottom-right | bottom-left | top-right | top-left
  PENPAD_ON_TOP    1 to keep above other windows instead of below
"""
import os
import atexit
import signal
import threading
import time
import urllib.request
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, WebKit2, Gdk, GLib

def _env(*names, default=None):
    """Return the first set env var among *names* (or default)."""
    for n in names:
        v = os.environ.get(n)
        if v is not None:
            return v
    return default

URL       = _env("PENPAD_URL", "NOTEPAD_URL", default="http://localhost:8767")
W_ENV     = _env("PENPAD_W", "NOTEPAD_W")
H_ENV     = _env("PENPAD_H", "NOTEPAD_H")
COLLAPSED = int(_env("PENPAD_COLLAPSED", "NOTEPAD_COLLAPSED", default="44"))
MARGIN    = int(_env("PENPAD_MARGIN", "NOTEPAD_MARGIN", default="0"))
ANCHOR    = _env("PENPAD_ANCHOR", "NOTEPAD_ANCHOR", default="bottom-right")
ON_TOP    = _env("PENPAD_ON_TOP", "NOTEPAD_ON_TOP", default="0") == "1"
BG        = Gdk.RGBA(); BG.parse("#0e0e10")

# Watchdog tuning: if a drag is "active" (we saw drag-motion or drag-enter)
# but no motion events arrive for this many seconds, assume the grab wedged
# (VNC ate the drop) and force-ungrab.
DRAG_STALL_SECONDS = 4.0
WATCHDOG_INTERVAL_MS = 1500

CSS = b"""
window.np-transparent { background: transparent; }

.np-collapse-btn, .np-dot {
  outline: 0;
  -gtk-outline-radius: 0;
}
.np-collapse-btn:focus, .np-collapse-btn:active, .np-collapse-btn:checked,
.np-dot:focus, .np-dot:active, .np-dot:checked {
  outline: 0;
  box-shadow: none;
}

.np-collapse-btn,
.np-collapse-btn:hover,
.np-collapse-btn:active,
.np-collapse-btn:focus,
.np-collapse-btn:checked {
  background-image: none;
  background-color: rgba(22, 22, 26, 0.85);
  color: #d4a373;
  min-width: 26px; min-height: 26px;
  margin: 0; padding: 0;
  border: 1px solid rgba(212, 163, 115, 0.30);
  border-radius: 6px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  font-size: 12px;
  text-shadow: none;
}
.np-collapse-btn:hover {
  background-color: rgba(212, 163, 115, 0.32);
  color: #e6e3dc;
}
.np-dot {
  background: #16161a;
  color: #ffffff;
  border: 1px solid rgba(212, 163, 115, 0.55);
  border-radius: 22px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 22px;
  font-weight: bold;
  padding: 0;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.55);
}
.np-dot:hover {
  background: #1c1c22;
  border-color: #d4a373;
  color: #ffffff;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.60);
}
"""

def default_size(workarea):
    w = int(W_ENV) if W_ENV else max(360, workarea.width // 3)
    h = int(H_ENV) if H_ENV else max(300, workarea.height // 2 - MARGIN)
    return w, h

def position(workarea, w, h):
    x_left   = workarea.x + MARGIN
    x_right  = workarea.x + workarea.width  - w - MARGIN
    y_top    = workarea.y + MARGIN
    y_bottom = workarea.y + workarea.height - h - MARGIN
    return {
        "bottom-right": (x_right, y_bottom),
        "bottom-left":  (x_left,  y_bottom),
        "top-right":    (x_right, y_top),
        "top-left":     (x_left,  y_top),
    }.get(ANCHOR, (x_right, y_bottom))

def make_window(role, w, h, x, y, transparent=False):
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("penpad-widget-" + role)
    win.set_role("penpad-widget-" + role)
    win.set_default_size(w, h)
    win.set_size_request(w, h)
    win.set_position(Gtk.WindowPosition.NONE)
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.stick()
    win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    if transparent:
        screen = win.get_screen()
        rgba = screen.get_rgba_visual() if screen else None
        if rgba is not None and screen.is_composited():
            win.set_visual(rgba)
            win.set_app_paintable(True)
            win.get_style_context().add_class("np-transparent")
    if ON_TOP:
        win.set_keep_above(True)
    # Otherwise: normal stacking. WM lets clicks raise the widget to the
    # front; it naturally falls behind when the user focuses another window.

    # Re-apply position on every map so Cinnamon's "remember window state"
    # cache or smart-placement strategy can't override our anchor.
    def on_map(_w, _e):
        gw = win.get_window()
        if gw is not None: gw.move(x, y)
        else:              win.move(x, y)
        return False
    win.connect("map-event", on_map)
    win.move(x, y)
    return win

# --- Global ungrab helper ----------------------------------------------------
# Defined at module scope so atexit and signal handlers can reach it even if
# main() has exited abnormally. The `display` reference is filled in by main().
_display_ref = {"display": None}

def panic_ungrab(reason="manual"):
    """Last-ditch recovery for a wedged pointer/keyboard grab.

    Safe to call repeatedly and when nothing is grabbed.
    """
    display = _display_ref.get("display") or Gdk.Display.get_default()
    print(f"[widget] panic_ungrab reason={reason}", flush=True)
    # Modern GTK3 path: default seat ungrab.
    try:
        seat = display.get_default_seat() if display is not None else None
        if seat is not None:
            seat.ungrab()
    except Exception as e:
        print(f"[widget] seat.ungrab failed: {e}", flush=True)
    # Older Gdk fallbacks (deprecated but still present in GTK3).
    try:
        Gdk.pointer_ungrab(Gdk.CURRENT_TIME)
    except Exception:
        pass
    try:
        Gdk.keyboard_ungrab(Gdk.CURRENT_TIME)
    except Exception:
        pass
    # Flush so the X server actually processes the ungrab before we die.
    try:
        if display is not None:
            display.flush()
            display.sync()
    except Exception:
        pass


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    display  = Gdk.Display.get_default()
    _display_ref["display"] = display
    monitor  = display.get_primary_monitor() or display.get_monitor(0)
    workarea = monitor.get_workarea()
    WIDTH, HEIGHT = default_size(workarea)
    bx, by = position(workarea, WIDTH, HEIGHT)
    sx, sy = position(workarea, COLLAPSED, COLLAPSED)

    # Big window: WebView with a tiny corner collapse button.
    big = make_window("main", WIDTH, HEIGHT, bx, by)
    web = WebKit2.WebView()
    web.set_background_color(BG)
    s = web.get_settings()
    s.set_enable_developer_extras(True)
    s.set_enable_smooth_scrolling(True)
    web.load_uri(URL)

    def on_load_failed(_v, _e, _u, _err):
        GLib.timeout_add_seconds(3, lambda: (web.load_uri(URL), False)[1])
        return True
    web.connect("load-failed", on_load_failed)

    # ---- Drag/drop state machine -------------------------------------------
    # VNC does not reliably bridge host→guest file DnD, so drops frequently
    # never arrive. GTK's implicit grab during a drag is then never released.
    # We track active drag timestamps ourselves so a watchdog can force an
    # ungrab if events stop flowing. We also bind to a GtkEventBox wrapping
    # the WebView, because the WebView's inner native GdkWindow can silently
    # win the drop target lottery and eat the events otherwise.
    drag_state = {
        "active": False,          # set True on drag-enter / first motion
        "last_motion": 0.0,       # monotonic timestamp of last drag-motion
        "enter_time": 0.0,        # monotonic timestamp of drag-enter
        "drop_in_flight": False,  # True between drag-drop and drag-data-received
    }

    def post_uris(file_uris):
        try:
            body = "\n".join(file_uris).encode("utf-8")
            req = urllib.request.Request(
                URL + "/upload-uri", data=body,
                headers={"Content-Type": "text/uri-list"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).read()
            GLib.idle_add(web.reload)
        except Exception as e:
            print(f"[widget] drop upload failed: {e}", flush=True)

    def on_drag_enter(_w, _ctx, x, y, _time):
        now = time.monotonic()
        drag_state["active"] = True
        drag_state["enter_time"] = now
        drag_state["last_motion"] = now
        print(f"[widget] drag-enter at ({x},{y})", flush=True)
        # No return value needed (drag-motion is the authoritative handler).

    def on_drag_motion(_w, ctx, x, y, time_):
        now = time.monotonic()
        if not drag_state["active"]:
            drag_state["enter_time"] = now
        drag_state["active"] = True
        drag_state["last_motion"] = now
        # Advertise that we accept COPY for this motion.
        try:
            Gdk.drag_status(ctx, Gdk.DragAction.COPY, time_)
        except Exception:
            pass
        # Log occasionally; motion fires constantly so keep it quiet.
        # (Only first motion per drag is logged here.)
        if drag_state.get("_logged_motion_once") is not True:
            print(f"[widget] drag-motion at ({x},{y})", flush=True)
            drag_state["_logged_motion_once"] = True
        return True  # we're a valid drop zone

    def on_drag_drop(w, ctx, x, y, time_):
        print(f"[widget] drag-drop at ({x},{y}) — requesting data", flush=True)
        drag_state["drop_in_flight"] = True
        # Pick the uri-list target if the source offers it.
        target = None
        try:
            targets = ctx.list_targets() or []
            for t in targets:
                name = t.name() if hasattr(t, "name") else str(t)
                if name == "text/uri-list":
                    target = t
                    break
            if target is None and targets:
                target = targets[0]
        except Exception as e:
            print(f"[widget] drag-drop list_targets failed: {e}", flush=True)
        if target is None:
            try:
                target = Gdk.Atom.intern("text/uri-list", False)
            except Exception:
                target = None
        try:
            if target is not None:
                w.drag_get_data(ctx, target, time_)
        except Exception as e:
            print(f"[widget] drag_get_data failed: {e}", flush=True)
            try:
                Gtk.drag_finish(ctx, False, False, time_)
            except Exception:
                pass
            drag_state["active"] = False
            drag_state["drop_in_flight"] = False
            panic_ungrab(reason="drag-drop-failed")
        return True  # we handle this drop

    def on_drag_data_received(_w, ctx, x, y, data, _info, time_):
        try:
            uris = list(data.get_uris() or [])
        except Exception:
            uris = []
        print(f"[widget] drag-data-received at ({x},{y}) uris={uris}", flush=True)
        file_uris = [u for u in uris if u.startswith("file://")]
        success = bool(file_uris)
        if file_uris:
            threading.Thread(target=post_uris, args=(file_uris,),
                             daemon=True).start()
        try:
            Gtk.drag_finish(ctx, success, False, time_)
        except Exception as e:
            print(f"[widget] drag_finish failed: {e}", flush=True)
        drag_state["active"] = False
        drag_state["drop_in_flight"] = False
        drag_state.pop("_logged_motion_once", None)

    def on_drag_leave(_w, _ctx, _time):
        was_active = drag_state["active"]
        drop_pending = drag_state["drop_in_flight"]
        print(f"[widget] drag-leave (active={was_active} drop_pending={drop_pending})",
              flush=True)
        # If the drag exited without ever firing drop, the implicit grab
        # might still be dangling on some VNC setups. Force release.
        if was_active and not drop_pending:
            drag_state["active"] = False
            drag_state.pop("_logged_motion_once", None)
            panic_ungrab(reason="drag-leave-without-drop")

    overlay = Gtk.Overlay()

    # Wrap the WebView in an EventBox so drop signals reliably reach GTK
    # before WebKit's inner GdkWindow swallows them. EventBox must be
    # visible (has its own GdkWindow) for this to work.
    dropbox = Gtk.EventBox()
    dropbox.set_visible_window(True)
    dropbox.set_above_child(False)  # keep WebView interactive for clicks
    dropbox.add(web)
    overlay.add(dropbox)

    # Use a plain EventBox (no Gtk.Button) so no GTK theme paints its own
    # hover/active/focus rectangles on top of our CSS. All visuals are ours.
    collapse_btn = Gtk.EventBox()
    collapse_btn.set_above_child(True)
    collapse_btn.set_visible_window(True)
    collapse_btn.get_style_context().add_class("np-collapse-btn")
    collapse_btn.set_size_request(26, 26)
    collapse_btn.set_halign(Gtk.Align.END)
    collapse_btn.set_valign(Gtk.Align.END)
    collapse_btn.set_margin_end(14)
    collapse_btn.set_margin_bottom(14)
    collapse_btn.set_tooltip_text("Minimize")
    _cb_label = Gtk.Label(label="\u2013")
    _cb_label.set_halign(Gtk.Align.CENTER)
    _cb_label.set_valign(Gtk.Align.CENTER)
    collapse_btn.add(_cb_label)
    overlay.add_overlay(collapse_btn)
    big.add(overlay)

    # Click anywhere on the widget raises it to the front. Propagates so
    # WebKit still sees the click for its own handling.
    def on_click_raise(_w, _event):
        gw = big.get_window()
        if gw is not None:
            gw.raise_()
        return False
    dropbox.connect("button-press-event", on_click_raise)
    big.connect("button-press-event", on_click_raise)

    # When the pointer enters the collapse zone, WebKit under the EventBox
    # never sees a mouseleave and keeps whatever DOM element was last
    # hovered in :hover state — showing a stuck rectangular highlight
    # beneath our rounded one. Toggle pointer-events on the doc root to
    # force WebKit to re-evaluate and clear the stale :hover.
    _HOVER_CLEAR_JS = (
        "document.documentElement.style.pointerEvents='none';"
        "setTimeout(function(){document.documentElement.style.pointerEvents='';},40);"
    )
    def clear_web_hover(_w, _e):
        try:
            web.run_javascript(_HOVER_CLEAR_JS, None, None, None)
        except Exception:
            pass
        return False
    collapse_btn.connect("enter-notify-event", clear_web_hover)
    collapse_btn.connect("leave-notify-event", clear_web_hover)

    # Connect drop-signal chain BEFORE drag_dest_set so every signal is live
    # the first time GTK fires them. drag_dest_set itself is deferred until
    # after show_all() below, so the EventBox's GdkWindow is realized.
    targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
    dropbox.connect("drag-motion", on_drag_motion)
    dropbox.connect("drag-drop", on_drag_drop)
    dropbox.connect("drag-data-received", on_drag_data_received)
    dropbox.connect("drag-leave", on_drag_leave)
    # drag-enter isn't a real GTK signal on widgets — drag-motion is the
    # entry point. We log "enter" from the first motion instead.

    # Small window: just the dot.
    small = make_window("dot", COLLAPSED, COLLAPSED, sx, sy, transparent=True)
    dot = Gtk.Button(label="\u270e")
    dot.get_style_context().add_class("np-dot")
    dot.set_tooltip_text("Open penpad")
    dot.set_can_focus(False)
    small.add(dot)

    def collapse(_b=None):
        big.hide()
        small.show_all()
    def expand(_b=None):
        small.hide()
        big.show_all()

    def _collapse_click(_w, event):
        if event.button == 1:
            collapse()
            return True
        return False
    collapse_btn.connect("button-press-event", _collapse_click)
    dot.connect("clicked", expand)

    def on_key(_w, event):
        ctrl  = event.state & Gdk.ModifierType.CONTROL_MASK
        shift = event.state & Gdk.ModifierType.SHIFT_MASK
        if ctrl and shift and event.keyval in (Gdk.KEY_Escape,):
            panic_ungrab(reason="hotkey"); return True
        if ctrl and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            Gtk.main_quit(); return True
        if ctrl and event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            web.reload(); return True
        if ctrl and event.keyval in (Gdk.KEY_h, Gdk.KEY_H):
            web.load_uri(URL); return True
        if ctrl and event.keyval in (Gdk.KEY_m, Gdk.KEY_M):
            (collapse if big.get_visible() else expand)()
            return True
        return False
    big.connect("key-press-event", on_key)
    small.connect("key-press-event", on_key)

    big.connect("destroy", Gtk.main_quit)
    small.connect("destroy", Gtk.main_quit)

    big.show_all()

    # Install drop target AFTER show_all() so inner GdkWindows exist. Using
    # flags=0 (no DestDefaults.ALL) means we own the motion/drop/finish
    # lifecycle explicitly — this is what lets us guarantee drag_finish is
    # always called and the grab is always released.
    dropbox.drag_dest_set(Gtk.DestDefaults(0), targets, Gdk.DragAction.COPY)
    # Also offer the top-level window as a fallback drop target for the
    # slivers (window border, collapse button) that the EventBox doesn't
    # cover. Same manual signal chain, same cleanup guarantees.
    big.drag_dest_set(Gtk.DestDefaults(0), targets, Gdk.DragAction.COPY)
    big.connect("drag-motion", on_drag_motion)
    big.connect("drag-drop", on_drag_drop)
    big.connect("drag-data-received", on_drag_data_received)
    big.connect("drag-leave", on_drag_leave)

    web.grab_focus()

    # ---- Grab watchdog -----------------------------------------------------
    # Self-heal for the VNC "drop never fires, grab never releases" failure.
    # Two independent signals, OR'd together:
    #  1. drag_state timestamps: if we saw drag-motion/enter but no new
    #     motion for DRAG_STALL_SECONDS, the drag is wedged.
    #  2. seat grab probe: ask the default seat whether the pointer or
    #     keyboard device is currently grabbed. If we're not in a drag but
    #     the seat reports a grab owned by us, release it. This is best-
    #     effort: device_is_grabbed availability varies across GTK3 minor
    #     versions, so it's guarded.
    def seat_reports_grab():
        try:
            seat = display.get_default_seat()
            if seat is None:
                return False
            pointer = None
            keyboard = None
            try:
                pointer = seat.get_pointer()
            except Exception:
                pass
            try:
                keyboard = seat.get_keyboard()
            except Exception:
                pass
            for dev in (pointer, keyboard):
                if dev is None:
                    continue
                try:
                    if display.device_is_grabbed(dev):
                        return True
                except Exception:
                    # device_is_grabbed missing on some builds; ignore.
                    pass
        except Exception:
            pass
        return False

    def watchdog():
        try:
            now = time.monotonic()
            if drag_state["active"]:
                stalled = now - drag_state["last_motion"]
                if stalled > DRAG_STALL_SECONDS:
                    print(f"[widget] watchdog: drag stalled {stalled:.1f}s — forcing ungrab",
                          flush=True)
                    drag_state["active"] = False
                    drag_state["drop_in_flight"] = False
                    drag_state.pop("_logged_motion_once", None)
                    panic_ungrab(reason=f"watchdog-stalled-{stalled:.1f}s")
            else:
                # Not tracking a drag, but if the seat still reports a grab
                # we probably missed a leave. Only fire if the grab persists
                # across two polls to avoid racing legitimate grabs (menus,
                # click-drag selection) which clear within a few ms.
                if seat_reports_grab():
                    if drag_state.get("_orphan_grab_seen"):
                        print("[widget] watchdog: orphan grab detected — forcing ungrab",
                              flush=True)
                        drag_state["_orphan_grab_seen"] = False
                        panic_ungrab(reason="watchdog-orphan-grab")
                    else:
                        drag_state["_orphan_grab_seen"] = True
                else:
                    drag_state["_orphan_grab_seen"] = False
        except Exception as e:
            print(f"[widget] watchdog error: {e}", flush=True)
        return True  # keep ticking

    GLib.timeout_add(WATCHDOG_INTERVAL_MS, watchdog)

    # ---- Exit / signal cleanup --------------------------------------------
    # Belt + suspenders: even if Python dies abnormally, try to release any
    # grab we hold so the VNC session doesn't need a server restart.
    atexit.register(lambda: panic_ungrab(reason="atexit"))

    def _on_signal(signum, _frame):
        print(f"[widget] caught signal {signum} — ungrab + quit", flush=True)
        panic_ungrab(reason=f"signal-{signum}")
        # Defer Gtk.main_quit to the main loop so signal returns promptly.
        GLib.idle_add(Gtk.main_quit)

    for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(_sig, _on_signal)
        except (ValueError, OSError):
            # Non-main thread or signal unavailable; skip.
            pass

    print("[widget] ready — drop target on EventBox; watchdog armed", flush=True)
    Gtk.main()
    # Final cleanup on normal exit.
    panic_ungrab(reason="main-exit")

if __name__ == "__main__":
    main()
