"""Stopwatch page with laps."""

from gi.repository import Adw, Gtk

from . import util
from .widgets import button, label, pill, two_tone_heading


class StopwatchPage(Gtk.ScrolledWindow):
    def __init__(self, app, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.app = app
        self.stopwatch = app.stopwatch
        self.signals = util.SignalBin()
        self._tick_id = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=6, margin_bottom=32,
                      margin_start=32, margin_end=32)
        heading, _ = two_tone_heading("Stopwatch", "every second counts")
        box.append(heading)

        display = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=24, css_classes=["stopwatch-display"])
        self.main = label("00:00", "stopwatch-time")
        self.cents = label(".00", "stopwatch-time", "big-time-dim")
        display.append(self.main)
        display.append(self.cents)
        box.append(display)

        controls = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER)
        self.secondary = button("Lap", "view-pin-symbolic", "large")
        self.secondary.connect("clicked", self._on_secondary)
        self.primary = button("Start", "media-playback-start-symbolic", "primary", "large")
        self.primary.connect("clicked", self._on_primary)
        controls.append(self.secondary)
        controls.append(self.primary)
        box.append(controls)

        self.laps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12)
        box.append(self.laps)

        self.set_child(Adw.Clamp(maximum_size=760, child=box))
        self.signals.connect(self.stopwatch, "changed", lambda *_: self.sync())
        self.connect("map", lambda *_: self.sync())
        self.connect("unmap", lambda *_: self._stop_ticking())
        self.sync()

    def teardown(self):
        self._stop_ticking()
        self.signals.disconnect_all()

    def _on_primary(self, *_):
        if self.stopwatch.state == "running":
            self.stopwatch.pause()
        else:
            self.stopwatch.start()

    def _on_secondary(self, *_):
        if self.stopwatch.state == "running":
            self.stopwatch.lap()
        else:
            self.stopwatch.reset()

    def _set_button(self, btn, text, icon):
        btn.get_child().set_label(text)
        btn.get_child().set_icon_name(icon)

    def sync(self):
        state = self.stopwatch.state
        self._set_button(self.primary, {"running": "Pause", "paused": "Resume"}.get(state, "Start"),
                         "media-playback-pause-symbolic" if state == "running" else "media-playback-start-symbolic")
        if state == "running":
            self._set_button(self.secondary, "Lap", "view-pin-symbolic")
        else:
            self._set_button(self.secondary, "Reset", "view-refresh-symbolic")
        self.secondary.set_sensitive(state != "stopped")
        self._render()
        self._render_laps()
        if state == "running" and self.get_mapped():
            self._start_ticking()
        else:
            self._stop_ticking()

    def _start_ticking(self):
        if not self._tick_id:
            self._tick_id = self.main.add_tick_callback(lambda *_: self._render() or True)

    def _stop_ticking(self):
        if self._tick_id:
            self.main.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _render(self):
        main, cents = util.format_stopwatch(self.stopwatch.elapsed())
        self.main.set_label(main)
        self.cents.set_label(cents)

    def _render_laps(self):
        util.clear_box(self.laps)
        laps = self.stopwatch.laps
        if not laps:
            return
        durations = [lap for lap, _ in laps]
        fastest = durations.index(min(durations)) if len(laps) > 2 else -1
        slowest = durations.index(max(durations)) if len(laps) > 2 else -1
        for i in range(len(laps) - 1, -1, -1):
            lap, total = laps[i]
            row = Gtk.Box(spacing=12, css_classes=["lc-card", "lap-row"])
            row.append(label(f"Lap {i + 1}", "card-note", width_chars=6))
            split = "".join(util.format_stopwatch(lap))
            row.append(label(f"+{split}", "lap-split", hexpand=True))
            if i == fastest:
                row.append(pill("Fastest", "tag-accent"))
            elif i == slowest:
                row.append(pill("Slowest", "tag-soft"))
            row.append(label("".join(util.format_stopwatch(total)), "lap-total", xalign=1))
            self.laps.append(row)
