"""Timer page: a setup card with presets and a grid of running timers."""

import time
from datetime import datetime

from gi.repository import Gtk, Pango

from . import util
from .widgets import ProgressRing, TimePicker, button, label, two_tone_heading

PRESETS = [(60, "1 min"), (120, "2 min"), (180, "3 min"), (300, "5 min"), (600, "10 min"),
           (900, "15 min"), (1800, "30 min"), (2700, "45 min"), (3600, "1 h")]


def timer_title(timer):
    if timer["name"]:
        return timer["name"]
    hours, rest = divmod(int(timer["duration"]), 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [f"{hours} h" if hours else "", f"{minutes} min" if minutes else "", f"{seconds} s" if seconds else ""]
    return " ".join(p for p in parts if p) + " timer"


class TimerCard(Gtk.Box):
    def __init__(self, app, timer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["lc-card", "timer-card"])
        self.app = app
        self.timer = timer

        top = Gtk.Box(spacing=8)
        top.append(label(timer_title(timer), "card-caption", hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        delete = Gtk.Button(icon_name="window-close-symbolic", css_classes=["chip-button"],
                            tooltip_text="Delete timer")
        delete.connect("clicked", lambda *_: app.timers.remove(timer["id"]))
        top.append(delete)
        self.append(top)

        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.ring = ProgressRing(176)
        overlay.set_child(self.ring)
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.remaining = Gtk.Label(css_classes=["timer-remaining"])
        self.note = Gtk.Label(css_classes=["card-note"])
        center.append(self.remaining)
        center.append(self.note)
        overlay.add_overlay(center)
        self.append(overlay)

        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.reset = button(None, "view-refresh-symbolic", "round", tooltip="Reset")
        self.reset.connect("clicked", lambda *_: app.timers.reset(timer["id"]))
        self.toggle = button(None, "media-playback-pause-symbolic", "round", "primary")
        self.toggle.connect("clicked", self._on_toggle)
        controls.append(self.reset)
        controls.append(self.toggle)
        self.append(controls)
        self.update()

    def _on_toggle(self, *_):
        state = self.timer["state"]
        if state == "running":
            self.app.timers.pause(self.timer["id"])
        elif state == "done":
            self.app.timers.restart(self.timer["id"])
        else:
            self.app.timers.resume(self.timer["id"])

    def update(self):
        timer = self.timer
        remaining = self.app.timers.remaining(timer)
        state = timer["state"]
        self.remaining.set_label(util.format_countdown(remaining + 0.999))
        self.ring.set_fraction(remaining / timer["duration"] if timer["duration"] else 0)
        if state == "running":
            ends = datetime.fromtimestamp(time.time() + remaining)
            self.note.set_label(f"Ends {util.format_hm_text(ends)}")
            self.toggle.set_icon_name("media-playback-pause-symbolic")
            self.toggle.set_tooltip_text("Pause")
        elif state == "done":
            self.note.set_label("Time's up")
            self.toggle.set_icon_name("view-refresh-symbolic")
            self.toggle.set_tooltip_text("Restart")
        else:
            self.note.set_label("Paused" if state == "paused" else "Ready")
            self.toggle.set_icon_name("media-playback-start-symbolic")
            self.toggle.set_tooltip_text("Start")
        self.reset.set_visible(state != "done")
        for css, active in (("running", state == "running"), ("done", state == "done")):
            (self.add_css_class if active else self.remove_css_class)(css)


class TimerPage(Gtk.ScrolledWindow):
    def __init__(self, app, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.app = app
        self.signals = util.SignalBin()
        self.cards = []
        self._tick_id = 0

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, margin_top=6, margin_bottom=32,
                            margin_start=32, margin_end=32)
        heading, _ = two_tone_heading("Timer", "count down to anything")
        self.root.append(heading)
        self.content = Gtk.Box(spacing=28)
        self.root.append(self.content)

        setup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=400,
                        valign=Gtk.Align.START)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, css_classes=["lc-card", "timer-setup"])
        card.append(label("New timer", "card-caption"))
        self.picker = TimePicker(with_seconds=True, hour_max=99)
        self.picker.set_values(0, 5, 0)
        self.picker.connect("changed", lambda *_: self._sync_start())
        card.append(self.picker)
        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                              max_children_per_line=5, min_children_per_line=3, homogeneous=True)
        for seconds, text in PRESETS:
            chip = Gtk.Button(label=text, css_classes=["preset-chip"], tooltip_text=f"Start a {text} timer")
            chip.connect("clicked", lambda _b, s=seconds: self.app.timers.add(s))
            presets.append(chip)
        card.append(presets)
        self.name = Gtk.Entry(placeholder_text="Label (optional)", css_classes=["lc-entry"])
        self.name.connect("activate", lambda *_: self._start())
        card.append(self.name)
        self.start = button("Start timer", "media-playback-start-symbolic", "primary", "large")
        self.start.connect("clicked", lambda *_: self._start())
        card.append(self.start)
        setup.append(card)

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=16, row_spacing=16,
                                min_children_per_line=1, max_children_per_line=3, homogeneous=True,
                                valign=Gtk.Align.START)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.CENTER, vexpand=True, css_classes=["empty-state"])
        empty.append(Gtk.Image(icon_name="timer-symbolic", css_classes=["empty-icon"], halign=Gtk.Align.CENTER))
        empty.append(Gtk.Label(label="No timers running", css_classes=["empty-title"]))
        empty.append(Gtk.Label(label="Pick a preset or set your own time.", css_classes=["empty-note"]))
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.stack.add_named(self.grid, "grid")
        self.stack.add_named(empty, "empty")

        self.content.append(setup)
        self.content.append(self.stack)
        self.setup = setup
        self.set_child(self.root)

        self.signals.connect(app.timers, "changed", lambda *_: self.rebuild())
        self.connect("map", lambda *_: self._sync_ticking())
        self.connect("unmap", lambda *_: self._stop_ticking())
        self.rebuild()
        self._sync_start()

    def teardown(self):
        self._stop_ticking()
        self.signals.disconnect_all()

    def set_layout(self, mode):
        wide = mode == "wide"
        self.content.set_orientation(Gtk.Orientation.HORIZONTAL if wide else Gtk.Orientation.VERTICAL)
        self.setup.set_size_request(400 if wide else -1, -1)
        margin = 16 if mode == "narrow" else 32
        self.root.set_margin_start(margin)
        self.root.set_margin_end(margin)

    def _duration(self):
        hours, minutes, seconds = self.picker.get_values()
        return hours * 3600 + minutes * 60 + seconds

    def _sync_start(self):
        self.start.set_sensitive(self._duration() > 0)

    def _start(self):
        if self._duration() > 0:
            self.app.timers.add(self._duration(), self.name.get_text().strip())
            self.name.set_text("")

    def rebuild(self):
        self.grid.remove_all()
        self.cards = [TimerCard(self.app, t) for t in self.app.timers.items]
        for card in self.cards:
            self.grid.append(card)
        self.stack.set_visible_child_name("grid" if self.cards else "empty")
        self._sync_ticking()

    def _sync_ticking(self):
        if self.app.timers.any_running() and self.get_mapped():
            if not self._tick_id:
                self._last = 0
                self._tick_id = self.grid.add_tick_callback(self._on_frame)
        else:
            self._stop_ticking()

    def _stop_ticking(self):
        if self._tick_id:
            self.grid.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _on_frame(self, _widget, _clock):
        now = time.monotonic()
        if now - self._last >= 0.1:
            self._last = now
            for card in self.cards:
                if card.timer["state"] == "running":
                    card.update()
        return True
