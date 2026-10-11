"""Dialogs: city search, alarm editor and the ringing screen."""

import calendar

from gi.repository import Adw, GLib, Gtk

from . import places, util
from .widgets import TimePicker, button, label

SNOOZE_CHOICES = [5, 10, 15, 20, 30]


class CitySearchDialog(Adw.Dialog):
    def __init__(self, index, on_selected, title="Add City", exclude=()):
        super().__init__(title=title, content_width=440, content_height=560)
        self.add_css_class("lc-dialog")
        self.index = index
        self.on_selected = on_selected
        self.exclude = set(exclude)

        header = Adw.HeaderBar()
        self.entry = Gtk.SearchEntry(placeholder_text="Search for a city or country", hexpand=True)
        self.entry.connect("search-changed", self._on_search)
        self.entry.connect("activate", self._on_activate_first)

        self.results = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                   valign=Gtk.Align.START)
        self.results.connect("row-activated", self._on_row_activated)
        self.placeholder = Adw.StatusPage(icon_name="globe-symbolic", title="Find a city",
                                          description="Type a city or country name", vexpand=True)
        self.placeholder.add_css_class("compact")

        self.stack = Gtk.Stack()
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(Adw.Clamp(child=self.results, margin_top=6, margin_bottom=18,
                                     margin_start=12, margin_end=12))
        self.stack.add_named(self.placeholder, "empty")
        self.stack.add_named(scroller, "results")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Adw.Clamp(child=self.entry, margin_start=12, margin_end=12, margin_top=6))
        box.append(self.stack)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(box)
        self.set_child(view)
        self.set_focus(self.entry)

    def _on_search(self, entry):
        util.clear_box(self.results)
        query = entry.get_text()
        matches = [c for c in self.index.search(query) if places.city_key(c) not in self.exclude]
        if not matches:
            self.placeholder.set_title("No matches" if query.strip() else "Find a city")
            self.placeholder.set_description("Try another spelling" if query.strip()
                                             else "Type a city or country name")
            self.stack.set_visible_child_name("empty")
            return
        for city in matches:
            now = places.now_in(city)
            row = Adw.ActionRow(title=GLib.markup_escape_text(city["name"]),
                                subtitle=GLib.markup_escape_text(
                                    f"{city['country']} · {util.format_offset(now.utcoffset())}"),
                                activatable=True)
            row.add_suffix(label(util.format_hm_text(now), "numeric", "dim-label"))
            row.city = city
            self.results.append(row)
        self.stack.set_visible_child_name("results")

    def _on_activate_first(self, _entry):
        first = self.results.get_row_at_index(0)
        if first is not None:
            self._on_row_activated(self.results, first)

    def _on_row_activated(self, _listbox, row):
        self.on_selected(row.city)
        self.close()


class AlarmDialog(Adw.Dialog):
    def __init__(self, alarm, on_save, on_delete=None):
        super().__init__(title="Edit Alarm" if alarm else "New Alarm", content_width=460)
        self.add_css_class("lc-dialog")
        self.on_save = on_save
        self.on_delete = on_delete
        self.twelve = util.uses_12h()

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save" if alarm else "Add", css_classes=["suggested-action"])
        save.connect("clicked", self._on_save)
        header.pack_start(cancel)
        header.pack_end(save)

        hour = alarm["hour"] if alarm else 7
        minute = alarm["minute"] if alarm else 0
        self.picker = TimePicker(hour_max=12 if self.twelve else 23)
        if self.twelve:
            self.picker.hours.set_range(1, 12)
        self.picker.set_values(hour % 12 or 12 if self.twelve else hour, minute)

        time_row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        time_row.append(self.picker)
        self.meridiem = None
        if self.twelve:
            self.meridiem = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                                    css_classes=["linked"])
            self.am = Gtk.ToggleButton(label="AM", active=hour < 12)
            self.pm = Gtk.ToggleButton(label="PM", group=self.am, active=hour >= 12)
            self.meridiem.append(self.am)
            self.meridiem.append(self.pm)
            time_row.append(self.meridiem)

        days_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, css_classes=["day-toggles"])
        self.day_buttons = []
        selected_days = set(alarm["days"]) if alarm else set()
        for day in range(7):
            toggle = Gtk.ToggleButton(label=calendar.day_abbr[day][:2], active=day in selected_days,
                                      css_classes=["day-toggle"], tooltip_text=calendar.day_name[day])
            self.day_buttons.append(toggle)
            days_box.append(toggle)

        prefs = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Name", text=alarm["name"] if alarm else "")
        prefs.add(self.name_row)
        self.snooze_row = Adw.ComboRow(title="Snooze for",
                                       model=Gtk.StringList.new([f"{m} minutes" for m in SNOOZE_CHOICES]))
        snooze = alarm.get("snooze_minutes", 10) if alarm else 10
        self.snooze_row.set_selected(SNOOZE_CHOICES.index(snooze) if snooze in SNOOZE_CHOICES else 1)
        prefs.add(self.snooze_row)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24,
                          margin_start=24, margin_end=24)
        content.append(time_row)
        content.append(label("Repeat", "section-caption"))
        content.append(days_box)
        content.append(prefs)
        if alarm and on_delete:
            delete = Gtk.Button(label="Delete Alarm", css_classes=["destructive-action", "pill"],
                                halign=Gtk.Align.CENTER, margin_top=6)
            delete.connect("clicked", self._on_delete)
            content.append(delete)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(content)
        self.set_child(view)
        self.set_default_widget(save)

    def _on_save(self, *_):
        hour, minute = self.picker.get_values()
        if self.twelve:
            hour = hour % 12 + (12 if self.pm.get_active() else 0)
        days = [i for i, b in enumerate(self.day_buttons) if b.get_active()]
        self.on_save(hour=hour, minute=minute, days=days, name=self.name_row.get_text().strip(),
                     snooze_minutes=SNOOZE_CHOICES[self.snooze_row.get_selected()])
        self.close()

    def _on_delete(self, *_):
        self.on_delete()
        self.close()


class RingingDialog(Adw.Dialog):
    """Full-attention prompt while an alarm or timer is ringing."""

    def __init__(self, info, on_stop, on_secondary):
        super().__init__(title=info["kind_title"], content_width=380, can_close=True)
        self.add_css_class("lc-dialog")
        self.add_css_class("ringing-dialog")
        self.connect("closed", lambda *_: on_stop())

        icon = Gtk.Image.new_from_icon_name("alarm-symbolic" if info["kind"] == "alarm" else "timer-symbolic")
        icon.add_css_class("ringing-icon")
        icon.set_halign(Gtk.Align.CENTER)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=36, margin_bottom=28,
                      margin_start=28, margin_end=28)
        box.append(icon)
        box.append(Gtk.Label(label=info["headline"], css_classes=["ringing-headline"]))
        box.append(Gtk.Label(label=info["detail"], css_classes=["ringing-detail"], wrap=True,
                             justify=Gtk.Justification.CENTER))

        actions = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=20, homogeneous=True)
        stop = button("Stop", None, "primary", "large")
        stop.connect("clicked", lambda *_: self.close())
        secondary = button(info["secondary_label"], None, "large")
        secondary.connect("clicked", lambda *_: (on_secondary(), self.close()))
        actions.append(secondary)
        actions.append(stop)
        box.append(actions)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_title=False))
        view.set_content(box)
        self.set_child(view)
        self.set_default_widget(stop)


class PreferencesDialog(Adw.PreferencesDialog):
    BACKGROUNDS = [("sky", "Living sky", "Drawn on this computer from the real sun and moon"),
                   ("photo", "City photo", "Downloads your home city's photo from Wikipedia")]

    def __init__(self, settings):
        super().__init__(title="Preferences")
        self.add_css_class("lc-dialog")
        self.settings = settings
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        hero = Adw.PreferencesGroup(title="Home card",
                                    description="The large card for your home city on the dashboard.")
        self.background = Adw.ComboRow(title="Background",
                                       model=Gtk.StringList.new([name for _, name, _ in self.BACKGROUNDS]))
        keys = [key for key, _, _ in self.BACKGROUNDS]
        current = settings.get("hero_background")
        self.background.set_selected(keys.index(current) if current in keys else 0)
        self.background.connect("notify::selected", self._on_background)
        self._sync_background_subtitle()
        hero.add(self.background)
        hero.add(self._switch("home_weather", "Home weather",
                              "Sends your home city's location to Open-Meteo"))
        page.add(hero)

        cities = Adw.PreferencesGroup(title="World clocks")
        cities.add(self._switch("city_weather", "Weather on city cards",
                                "Sends the locations of your added cities to Open-Meteo"))
        page.add(cities)
        self.add(page)

    def _switch(self, key, title, subtitle):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=self.settings.get(key))
        row.connect("notify::active", lambda r, _p: self.settings.set(key, r.get_active()))
        return row

    def _sync_background_subtitle(self):
        self.background.set_subtitle(self.BACKGROUNDS[self.background.get_selected()][2])

    def _on_background(self, row, _param):
        self.settings.set("hero_background", self.BACKGROUNDS[row.get_selected()][0])
        self._sync_background_subtitle()
