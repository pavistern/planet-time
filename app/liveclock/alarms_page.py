"""Alarms page: list of alarms with on/off switches and an editor dialog."""

import calendar
import time
from datetime import datetime

from gi.repository import Adw, Gtk, Pango

from . import util
from .dialogs import AlarmDialog
from .widgets import button, label, pill, two_tone_heading


def describe_days(days):
    days = sorted(days)
    if not days:
        return "Once"
    if len(days) == 7:
        return "Every day"
    if days == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if days == [5, 6]:
        return "Weekends"
    return ", ".join(calendar.day_abbr[d] for d in days)


def ring_in_text(due):
    return f"in {util.format_span(max(60, due - time.time()))}"


class AlarmRow(Gtk.Box):
    def __init__(self, alarm, on_edit, on_toggle):
        super().__init__(spacing=16, css_classes=["lc-card", "alarm-row"])
        self.alarm = alarm
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        time_row = Gtk.Box(spacing=8)
        text, meridiem = util.format_hm(datetime(2000, 1, 1, alarm["hour"], alarm["minute"]))
        time_row.append(label(text, "alarm-time"))
        if meridiem:
            time_row.append(label(meridiem, "alarm-meridiem", valign=Gtk.Align.END, margin_bottom=8))
        info.append(time_row)
        meta = " · ".join(p for p in (alarm["name"], describe_days(alarm["days"])) if p)
        info.append(label(meta, "card-note", ellipsize=Pango.EllipsizeMode.END))
        self.status = pill("", "tag-accent")
        self.status.set_margin_top(6)
        self.status.set_visible(False)
        info.append(self.status)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: on_edit(alarm))
        info.add_controller(click)
        info.set_cursor_from_name("pointer")
        self.append(info)

        edit = Gtk.Button(icon_name="document-edit-symbolic", css_classes=["chip-button"],
                          valign=Gtk.Align.CENTER, tooltip_text="Edit alarm")
        edit.connect("clicked", lambda *_: on_edit(alarm))
        self.append(edit)
        self.switch = Gtk.Switch(active=alarm["enabled"], valign=Gtk.Align.CENTER,
                                 tooltip_text="Turn alarm on or off")
        self.switch.connect("notify::active", lambda sw, _p: on_toggle(alarm, sw.get_active()))
        self.append(self.switch)
        if not alarm["enabled"]:
            self.add_css_class("disabled")
        self.update()

    def update(self):
        snoozed = self.alarm.get("snoozed_until")
        if snoozed and self.alarm["enabled"]:
            self.status.set_label(f"Snoozed {ring_in_text(snoozed)}")
            self.status.set_visible(True)
        elif self.alarm["enabled"] and self.alarm.get("next_ring") \
                and self.alarm["next_ring"] - time.time() < 12 * 3600:
            self.status.set_label(f"Rings {ring_in_text(self.alarm['next_ring'])}")
            self.status.set_visible(True)
        else:
            self.status.set_visible(False)


class AlarmsPage(Gtk.ScrolledWindow):
    def __init__(self, app, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.app = app
        self.window = window
        self.signals = util.SignalBin()
        self.rows = []

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=6, margin_bottom=32,
                      margin_start=32, margin_end=32)
        header = Gtk.Box(spacing=12)
        heading, self.subtitle = two_tone_heading("Alarms", "")
        heading.set_hexpand(True)
        header.append(heading)
        add = button("Add alarm", "list-add-symbolic", "primary", "large")
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda *_: self.edit(None))
        header.append(add)
        box.append(header)

        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.CENTER, vexpand=True, margin_top=40, css_classes=["empty-state"])
        empty.append(Gtk.Image(icon_name="alarm-symbolic", css_classes=["empty-icon"], halign=Gtk.Align.CENTER))
        empty.append(Gtk.Label(label="No alarms yet", css_classes=["empty-title"]))
        empty.append(Gtk.Label(label="Alarms ring even after you close the window.",
                               css_classes=["empty-note"]))
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.list, "list")
        self.stack.add_named(empty, "empty")
        box.append(self.stack)

        self.set_child(Adw.Clamp(maximum_size=860, child=box))
        self.signals.connect(app.alarms, "changed", lambda *_: self.rebuild())
        self.signals.connect(app.ticker, "second", lambda *_: self.tick())
        self.signals.connect(util.interface_settings(), "changed::clock-format", lambda *_: self.rebuild())
        self.rebuild()

    def teardown(self):
        self.signals.disconnect_all()

    def rebuild(self):
        util.clear_box(self.list)
        self.rows = [AlarmRow(a, self.edit, self._toggle) for a in self.app.alarms.sorted()]
        for row in self.rows:
            self.list.append(row)
        self.stack.set_visible_child_name("list" if self.rows else "empty")
        self.tick(force=True)

    def tick(self, force=False):
        if not force and time.localtime().tm_sec != 0:
            return
        due, alarm = self.app.alarms.next_alarm()
        if alarm:
            self.subtitle.set_label(f"Next alarm {ring_in_text(due)}")
        else:
            self.subtitle.set_label("Nothing scheduled" if self.rows else "Wake up on time")
        for row in self.rows:
            row.update()

    def _toggle(self, alarm, active):
        if alarm["enabled"] == active:
            return
        self.app.alarms.set_enabled(alarm["id"], active)
        if active:
            self.window.toast(f"Alarm set for {util.format_span(alarm['next_ring'] - time.time())} from now")

    def edit(self, alarm):
        if alarm is None:
            def save(**values):
                created = self.app.alarms.add(**values)
                self.window.toast(f"Alarm set for {util.format_span(created['next_ring'] - time.time())} from now")
            AlarmDialog(None, save).present(self.window)
        else:
            def save(**values):
                values["enabled"] = True
                updated = self.app.alarms.update(alarm["id"], **values)
                self.window.toast(f"Alarm set for {util.format_span(updated['next_ring'] - time.time())} from now")
            AlarmDialog(alarm, save, lambda: self.app.alarms.remove(alarm["id"])).present(self.window)
