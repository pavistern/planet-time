"""Application: owns the models, rings alarms and timers, keeps running in the background when needed."""

import os
import sys
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, VERSION, util
from .models import Alarms, Cities, Settings, Stopwatch, Store, Ticker, Timers
from .photos import CityPhotos
from .dialogs import PreferencesDialog
from .ringer import Ringer
from .timer_page import timer_title
from .weather import Weather
from .window import MainWindow

AUTOSTART_NAME = f"{APP_ID}-alarms.desktop"
RING_SECONDS = 5 * 60


class LiveClockApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without a window so alarms can ring", None)
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open a page: dashboard, alarms, stopwatch or timer", "PAGE")
        self._held = False
        self._ring_timeout = 0
        self.ringing = None

    # -- lifecycle ---------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(os.path.dirname(__file__), "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.store = Store()
        self.ticker = Ticker()
        self.settings = Settings(self.store)
        self.cities = Cities(self.store)
        self.weather = Weather(self.cities, self.settings)
        self.photos = CityPhotos()
        self.alarms = Alarms(self.store)
        self.timers = Timers(self.store)
        self.stopwatch = Stopwatch()
        self.ringer = Ringer()

        self.ticker.connect("second", lambda *_: self.alarms.check())
        self.alarms.connect("ring", self._on_alarm_ring)
        self.timers.connect("finished", self._on_timer_finished)
        self.alarms.connect("changed", lambda *_: self._sync_background())
        self.timers.connect("changed", lambda *_: self._sync_background())

        def add_action(name, callback, parameter=None):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(parameter) if parameter else None)
            action.connect("activate", lambda _a, value: callback(value.unpack() if value else None))
            self.add_action(action)

        add_action("show", lambda _v: self.activate())
        add_action("stop-ringing", lambda _v: self.stop_ringing())
        add_action("snooze", self.snooze, "s")
        add_action("restart-timer", self.restart_timer, "s")
        add_action("page", self._show_page, "s")
        add_action("preferences", lambda _v: self._show_preferences())
        add_action("about", lambda _v: self._show_about())
        add_action("quit", lambda _v: self._quit())

        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("window.close", ["<Control>w"])
        for i, page in enumerate(("dashboard", "alarms", "stopwatch", "timer"), start=1):
            self.set_accels_for_action(f"app.page::{page}", [f"<Alt>{i}"])

        self._sync_background()

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("background") and not self.get_windows():
            return 0
        self.activate()
        if options.get("page"):
            self._show_page(options["page"])
        return 0

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()
        if self.ringing:
            window.show_ringing(self.ringing)

    def _show_page(self, name):
        self.activate()
        self.props.active_window.show_page(name)

    def _quit(self):
        self.stop_ringing()
        self.quit()

    def _show_preferences(self):
        self.activate()
        PreferencesDialog(self.settings).present(self.props.active_window)

    def _show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                comments="World clocks, alarms, stopwatch and timers.",
                                developer_name=APP_NAME)
        about.present(self.props.active_window)

    # -- background --------------------------------------------------------

    def _sync_background(self):
        """Stay alive without a window while alarms are on or timers run, like GNOME Clocks."""
        needed = self.alarms.any_enabled() or self.timers.any_running()
        if needed and not self._held:
            self.hold()
            self._held = True
        elif not needed and self._held:
            self.release()
            self._held = False
        self._sync_autostart(self.alarms.any_enabled())

    def _sync_autostart(self, enabled):
        path = os.path.join(GLib.get_user_config_dir(), "autostart", AUTOSTART_NAME)
        if enabled and not os.path.exists(path):
            script = os.path.abspath(sys.argv[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nType=Application\n"
                         f"Name={APP_NAME} (alarms)\n"
                         f"Comment=Keeps {APP_NAME} alarms ringing after you log in\n"
                         f"Exec=/usr/bin/python3 {script} --background\n"
                         f"Icon={APP_ID}\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
        elif not enabled and os.path.exists(path):
            os.remove(path)

    # -- ringing -----------------------------------------------------------

    def _on_alarm_ring(self, _alarms, alarm_id):
        alarm = self.alarms.get(alarm_id)
        if alarm is None:
            return
        when = util.format_hm_text(datetime(2000, 1, 1, alarm["hour"], alarm["minute"]))
        self._ring({"kind": "alarm", "id": alarm_id, "kind_title": "Alarm", "headline": when,
                    "detail": alarm["name"] or "Alarm",
                    "secondary_label": f"Snooze {alarm.get('snooze_minutes', 10)} min"})

    def _on_timer_finished(self, _timers, timer_id):
        timer = self.timers.get(timer_id)
        if timer is None:
            return
        self._ring({"kind": "timer", "id": timer_id, "kind_title": "Timer", "headline": "Time's up",
                    "detail": timer_title(timer), "secondary_label": "Restart"})

    def _ring(self, info):
        self.stop_ringing()
        self.ringing = info
        self.ringer.start()

        if info["kind"] == "alarm":
            notification = Gio.Notification.new(f"Alarm · {info['headline']}")
        else:
            notification = Gio.Notification.new("Time's up")
        notification.set_body(info["detail"])
        notification.set_priority(Gio.NotificationPriority.URGENT)
        notification.set_default_action("app.show")
        notification.add_button("Stop", "app.stop-ringing")
        target = GLib.Variant.new_string(info["id"])
        if info["kind"] == "alarm":
            notification.add_button_with_target(info["secondary_label"], "app.snooze", target)
        else:
            notification.add_button_with_target("Restart", "app.restart-timer", target)
        self.send_notification("ringing", notification)

        window = self.props.active_window
        if window is not None and window.get_visible():
            window.show_ringing(info)
        self._ring_timeout = GLib.timeout_add_seconds(RING_SECONDS, self._on_ring_timeout)

    def _on_ring_timeout(self):
        self._ring_timeout = 0
        self.stop_ringing()
        return GLib.SOURCE_REMOVE

    def stop_ringing(self):
        if self.ringing is None:
            return
        self.ringing = None
        self.ringer.stop()
        self.withdraw_notification("ringing")
        if self._ring_timeout:
            GLib.source_remove(self._ring_timeout)
            self._ring_timeout = 0
        for window in self.get_windows():
            if isinstance(window, MainWindow):
                window.close_ringing()

    def snooze(self, alarm_id):
        self.stop_ringing()
        self.alarms.snooze(alarm_id)

    def restart_timer(self, timer_id):
        self.stop_ringing()
        self.timers.restart(timer_id)


def main(argv):
    return LiveClockApp().run(argv)
