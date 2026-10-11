"""Dashboard: home clock, world city cards, focus clock with map, today and sun cards."""

from datetime import datetime, timedelta, timezone

from gi.repository import GLib, Gtk, Pango

from . import places, util
from .dialogs import CitySearchDialog
from .sky import MoonGlyph, PhotoView, SkyScene, is_bright
from .weather import describe as weather_describe
from .widgets import SunArc, WeatherGlyph, WorldMap, icon_chip, label, pill, two_tone_heading


class HeroCard(Gtk.Overlay):
    """Home city card: living sky (or a city photo) behind the time, with a frosted info panel."""

    def __init__(self, app, on_change_home):
        super().__init__(css_classes=["hero-card"])
        self.app = app
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._minute_key = None
        self._mode_class = None

        self.backdrop = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=400)
        self.sky = SkyScene()
        self.backdrop.add_named(self.sky, "sky")
        photo = Gtk.Overlay()
        self.picture = PhotoView()
        photo.set_child(self.picture)
        photo.add_overlay(Gtk.Box(css_classes=["photo-scrim"]))
        self.backdrop.add_named(photo, "photo")
        self.set_child(self.backdrop)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["hero-content"])
        top = Gtk.Box(spacing=8)
        self.city = label("", "hero-city", hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        top.append(self.city)
        change = Gtk.Button(icon_name="mark-location-symbolic", css_classes=["chip-button", "on-hero"],
                            tooltip_text="Change home city", valign=Gtk.Align.START)
        change.connect("clicked", lambda *_: on_change_home())
        top.append(change)

        body = Gtk.Box(spacing=16, vexpand=True)
        digits = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.hours = label("", "hero-digits")
        self.minutes = label("", "hero-digits", "hero-digits-dim")
        digits.append(self.hours)
        digits.append(self.minutes)
        body.append(digits)

        self.info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, css_classes=["hero-info"],
                            valign=Gtk.Align.CENTER, halign=Gtk.Align.END, hexpand=True)
        self.weather_row, self.weather_icon, self.weather_text = self._info_row(Gtk.Image())
        self.moon_row, self.moon_icon, self.moon_text = self._info_row(MoonGlyph())
        self.daylight_row, _icon, self.daylight_text = self._info_row(
            Gtk.Image.new_from_icon_name("weather-clear-symbolic"))
        self.alarm_row, _icon, self.alarm_text = self._info_row(Gtk.Image.new_from_icon_name("alarm-symbolic"))
        body.append(self.info)

        bottom = Gtk.Box(spacing=10)
        self.offset = pill("", "tag-accent")
        self.day = label("", "hero-day")
        self.credit = label("", "hero-credit", hexpand=True, xalign=1, ellipsize=Pango.EllipsizeMode.MIDDLE,
                            max_width_chars=34)
        bottom.append(self.offset)
        bottom.append(self.day)
        bottom.append(self.credit)

        content.append(top)
        content.append(body)
        content.append(bottom)
        self.add_overlay(content)
        self.set_measure_overlay(content, True)

    def _info_row(self, icon):
        row = Gtk.Box(spacing=9, css_classes=["hero-info-row"])
        text = label("", ellipsize=Pango.EllipsizeMode.END)
        row.append(icon)
        row.append(text)
        self.info.append(row)
        return row, icon, text

    def _set_mode_class(self, name):
        if name != self._mode_class:
            if self._mode_class:
                self.remove_css_class(self._mode_class)
            self.add_css_class(name)
            self._mode_class = name

    def update(self, home, force=False):
        now = places.now_in(home)
        self.city.set_label(home["name"])
        if util.uses_12h():
            self.hours.set_label(f"{now.hour % 12 or 12:02d}")
            self.day.set_label(f"{'AM' if now.hour < 12 else 'PM'} · {now:%A}")
        else:
            self.hours.set_label(f"{now.hour:02d}")
            self.day.set_label(f"{now:%A}")
        self.minutes.set_label(f"{now.minute:02d}")
        self.offset.set_label(util.format_offset(now.utcoffset()))

        minute_key = (places.city_key(home), now.strftime("%Y%m%d%H%M"))
        if force or minute_key != self._minute_key:
            self._minute_key = minute_key
            self._refresh(home, now)

    def _refresh(self, home, now):
        # Backdrop: a cached city photo if that's the chosen style, otherwise the living sky.
        photo = None
        if self.app.settings.get("hero_background") == "photo":
            photo = self.app.photos.lookup(home)
            if photo is None:
                self.app.photos.request(home)
        if photo:
            self.picture.set_path(photo["path"])
            self.backdrop.set_visible_child_name("photo")
            self.credit.set_label(photo["credit"])
            self.credit.set_tooltip_text(photo["credit"])
            self.credit.set_visible(True)
            self._set_mode_class("photo")
        else:
            self.sky.show_city(home)
            self.backdrop.set_visible_child_name("sky")
            self.credit.set_visible(False)
            self._set_mode_class("sky-day" if is_bright(self.sky.altitude()) else "sky-night")

        # Info panel
        report = self.app.weather.report_for(home)
        self.weather_row.set_visible(report is not None)
        if report:
            description, icon = weather_describe(report)
            self.weather_icon.set_from_icon_name(icon)
            self.weather_text.set_label(f"{self.app.weather.format_temp(report['temp'])} · {description}")

        phase = util.moon_phase(now.astimezone(timezone.utc))
        self.moon_icon.set_phase(phase)
        self.moon_text.set_label(f"{util.moon_phase_name(phase)} · {round(util.moon_illumination(phase) * 100)}%")

        sunrise, sunset, state = util.sun_times(home["lat"], home["lon"], now.date(), places.zone_of(home))
        if state != "normal":
            self.daylight_text.set_label("Sun up all day" if state == "polar-day" else "Sun down all day")
        elif now < sunrise:
            self.daylight_text.set_label(f"Sunrise in {util.format_span((sunrise - now).total_seconds())}")
        elif now <= sunset:
            self.daylight_text.set_label(f"Daylight left {util.format_span((sunset - now).total_seconds())}")
        else:
            tomorrow, _, _ = util.sun_times(home["lat"], home["lon"], now.date() + timedelta(days=1),
                                            places.zone_of(home))
            self.daylight_text.set_label(f"Sunrise {util.format_hm_text(tomorrow)}" if tomorrow else "Night")

        due, alarm = self.app.alarms.next_alarm()
        if alarm:
            ring = datetime.fromtimestamp(due)
            when = util.format_hm_text(ring)
            relation = util.day_relation(ring, datetime.now())
            self.alarm_text.set_label(f"Next alarm {when}" + ("" if relation == "Today" else f" · {relation}"))
        else:
            self.alarm_text.set_label("No alarms set")


class CityCard(Gtk.Overlay):
    def __init__(self, city, on_remove, weather, on_hover=None):
        super().__init__(css_classes=["city-card-wrap"])
        self.city = city
        self.weather = weather
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["lc-card", "city-card"])
        self.name = label(city["name"], "card-caption", ellipsize=Pango.EllipsizeMode.END, margin_end=44)
        card.append(self.name)
        card.append(label(city.get("country", ""), "card-note", ellipsize=Pango.EllipsizeMode.END,
                          margin_end=44))

        time_row = Gtk.Box(spacing=6, vexpand=True, valign=Gtk.Align.CENTER, margin_top=6)
        self.time = label("", "card-time")
        self.meridiem = label("", "card-meridiem", valign=Gtk.Align.END, margin_bottom=6)
        self.temp = label("", "card-temp", hexpand=True, xalign=1, valign=Gtk.Align.CENTER)
        time_row.append(self.time)
        time_row.append(self.meridiem)
        time_row.append(self.temp)
        card.append(time_row)

        self.difference = label("", "card-note", ellipsize=Pango.EllipsizeMode.END)
        card.append(self.difference)
        self.conditions = label("", "card-note", ellipsize=Pango.EllipsizeMode.END)
        card.append(self.conditions)

        footer = Gtk.Box(spacing=8, margin_top=8)
        self.offset = pill("", "tag-accent")
        footer.append(self.offset)
        status = Gtk.Box(spacing=6, hexpand=True, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        self.status_dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        self.status = label("", "status-label")
        status.append(self.status_dot)
        status.append(self.status)
        self.status_box = status
        footer.append(status)
        card.append(footer)
        self.set_child(card)

        self.glyph = WeatherGlyph()
        self.chip = Gtk.Box(css_classes=["chip"], halign=Gtk.Align.END, valign=Gtk.Align.START,
                            margin_top=12, margin_end=12)
        self.chip.append(self.glyph)
        self.glyph.set_hexpand(True)
        self.add_overlay(self.chip)

        # Hover: the weather icon comes alive and the map shows this city (flight path + pulse).
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: (self.glyph.play(), on_hover and on_hover(city)))
        motion.connect("leave", lambda *_: (self.glyph.stop(), on_hover and on_hover(None)))
        self.add_controller(motion)
        remove = Gtk.Button(icon_name="window-close-symbolic", css_classes=["chip-button", "remove-button"],
                            tooltip_text=f"Remove {city['name']}", halign=Gtk.Align.END,
                            valign=Gtk.Align.START, margin_top=12, margin_end=12)
        remove.connect("clicked", lambda *_: on_remove(city))
        self.add_overlay(remove)
        self._status_kind = None

    def update(self, here, focused):
        now = places.now_in(self.city)
        text, meridiem = util.format_hm(now)
        self.time.set_label(text)
        self.meridiem.set_label(meridiem)
        self.meridiem.set_visible(bool(meridiem))
        self.offset.set_label(util.format_offset(now.utcoffset()))

        diff = (now.utcoffset() - here.utcoffset()).total_seconds()
        relation = util.day_relation(now.replace(tzinfo=None), here.replace(tzinfo=None))
        self.difference.set_label(f"{util.format_signed_span(diff)} · {relation}")
        self.difference.set_tooltip_text(util.describe_difference(diff) + " you")

        kind, text, hint = util.availability(now)
        if kind != self._status_kind:
            if self._status_kind:
                self.status_dot.remove_css_class(self._status_kind)
            self.status_dot.add_css_class(kind)
            self._status_kind = kind
        self.status.set_label(text)
        self.status_box.set_tooltip_text(hint)

        report = self.weather.report_for(self.city)
        if report:
            description, icon = weather_describe(report)
            self.glyph.set_icon(icon)
            self.chip.set_tooltip_text(description)
            self.temp.set_label(self.weather.format_temp(report["temp"]))
            extremes = ""
            if report.get("high") is not None and report.get("low") is not None:
                extremes = (f" · H {self.weather.format_temp(report['high'])}"
                            f" L {self.weather.format_temp(report['low'])}")
            self.conditions.set_label(description + extremes)
        else:
            day = util.is_daylight(self.city["lat"], self.city["lon"])
            self.glyph.set_icon("weather-clear" if day else "weather-clear-night")
            self.chip.set_tooltip_text("Daytime" if day else "Night")
            self.temp.set_label("")
            self.conditions.set_label("Weather unavailable")
        self.conditions.set_visible(self.weather.enabled_for(self.city))
        if focused:
            self.add_css_class("focused")
        else:
            self.remove_css_class("focused")


class TodayCard(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["lc-card", "today-card"])
        top = Gtk.Box()
        self.caption = label("Today", "card-caption", hexpand=True)
        top.append(self.caption)
        top.append(icon_chip("x-office-calendar-symbolic", "chip"))
        self.date = label("", "today-date", vexpand=True, valign=Gtk.Align.END, wrap=True)
        bottom = Gtk.Box()
        self.week = pill("", "tag-soft")
        self.year = label("", "card-caption", hexpand=True, xalign=1)
        bottom.append(self.week)
        bottom.append(self.year)
        self.append(top)
        self.append(self.date)
        self.append(bottom)

    def update(self, now, city_name, is_home):
        self.caption.set_label("Today" if is_home else f"Today in {city_name}")
        self.date.set_label(now.strftime("%a, %B %-d"))
        self.week.set_label(f"Week {now.isocalendar()[1]}")
        self.year.set_label(str(now.year))


class SunCard(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["lc-card", "sun-card"])
        top = Gtk.Box()
        top.append(label("Sun", "card-caption", hexpand=True))
        self.icon = icon_chip("weather-clear-symbolic", "chip")
        top.append(self.icon)
        self.arc = SunArc()

        times = Gtk.Box()
        rise = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        rise.append(label("Sunrise", "card-note"))
        self.sunrise = label("", "sun-time")
        rise.append(self.sunrise)
        sets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sets.append(label("Sunset", "card-note", xalign=1))
        self.sunset = label("", "sun-time", xalign=1)
        sets.append(self.sunset)
        times.append(rise)
        times.append(sets)

        self.length = pill("", "tag-soft")
        self.append(top)
        self.append(self.arc)
        self.append(times)
        self.append(self.length)
        self._cache_key = None
        self._times = (None, None, "normal")

    def update(self, city, now):
        key = (places.city_key(city), city["lat"], city["lon"], now.date())
        if key != self._cache_key:
            self._cache_key = key
            self._times = util.sun_times(city["lat"], city["lon"], now.date(), places.zone_of(city))
        sunrise, sunset, state = self._times
        if state != "normal":
            self.sunrise.set_label("—")
            self.sunset.set_label("—")
            self.length.set_label("Polar day" if state == "polar-day" else "Polar night")
            self.arc.set_fraction(0.5 if state == "polar-day" else None)
            return
        self.sunrise.set_label(util.format_hm_text(sunrise))
        self.sunset.set_label(util.format_hm_text(sunset))
        day_seconds = (sunset - sunrise).total_seconds()
        self.length.set_label(util.format_span(day_seconds))
        self.arc.set_fraction((now - sunrise).total_seconds() / day_seconds)
        self.icon.set_from_icon_name("weather-clear-symbolic" if sunrise <= now <= sunset
                                     else "weather-clear-night-symbolic")


class CityRail(Gtk.Box):
    """Vertical carousel of city chips: five at a time, with arrows (and the scroll wheel) to see more."""

    VISIBLE = 5

    def __init__(self, on_select, on_hover):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                         css_classes=["city-rail"])
        self.on_select = on_select
        self.on_hover = on_hover  # on_hover(city) while a code is under the pointer, on_hover(None) after
        self.entries = []  # (city, is_home)
        self.home = None
        self.focus = None
        self.start = 0
        self._serial = 0

        self.up = Gtk.Button(icon_name="go-up-symbolic", css_classes=["rail-arrow"], halign=Gtk.Align.CENTER,
                             tooltip_text="Show previous cities")
        self.up.connect("clicked", lambda *_: self.shift(-1))
        self.down = Gtk.Button(icon_name="go-down-symbolic", css_classes=["rail-arrow"], halign=Gtk.Align.CENTER,
                               tooltip_text="Show more cities")
        self.down.connect("clicked", lambda *_: self.shift(1))
        self.home_chip = Gtk.Button(icon_name="mark-location-symbolic", css_classes=["rail-chip"],
                                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                    tooltip_text="Home (local time)")
        self.home_chip.connect("clicked", lambda *_: self.on_select(self.home))
        # Fixed-height row so selecting home (which enlarges the pin) doesn't shift the arrows.
        home_slot = Gtk.Box(height_request=72, margin_bottom=4)
        home_slot.append(Gtk.Box(hexpand=True))
        home_slot.append(self._hover_slot(self.home_chip, lambda: self.home))
        home_slot.append(Gtk.Box(hexpand=True))
        self.pages = Gtk.Stack(transition_duration=220, vhomogeneous=False)
        # Room for four chips plus the larger selected one, so the arrows don't move as the list pages.
        self.pages.set_size_request(-1, 4 * 48 + 71 + 4 * 10)

        self.append(home_slot)
        self.append(self.up)
        self.append(self.pages)
        self.append(self.down)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL
                                               | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _max_start(self):
        return max(0, len(self.entries) - self.VISIBLE)

    def set_entries(self, home, entries, focus):
        self.home, self.entries, self.focus = home, entries, focus
        (self.home_chip.add_css_class if focus is home else self.home_chip.remove_css_class)("selected")
        self.start = min(self.start, self._max_start())
        index = next((i for i, (city, _) in enumerate(entries) if city is focus), None)
        if index is not None and not self.start <= index < self.start + self.VISIBLE:
            self.start = max(0, min(index - self.VISIBLE // 2, self._max_start()))
        self._render(Gtk.StackTransitionType.NONE)

    def shift(self, step):
        start = max(0, min(self.start + step, self._max_start()))
        if start != self.start:
            self.start = start
            self._render(Gtk.StackTransitionType.SLIDE_UP if step > 0 else Gtk.StackTransitionType.SLIDE_DOWN)

    def _on_scroll(self, _controller, _dx, dy):
        if dy:
            self.shift(1 if dy > 0 else -1)
        return True

    def _render(self, transition):
        codes = places.unique_codes([city["name"] for city, _ in self.entries])
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
        window = range(self.start, min(self.start + self.VISIBLE, len(self.entries)))
        for i in window:
            city, is_home = self.entries[i]
            chip = Gtk.Button(css_classes=["rail-chip"], halign=Gtk.Align.CENTER,
                              tooltip_text=f"{city['name']}{' (home)' if is_home else ''}")
            if is_home:
                chip.set_icon_name("mark-location-symbolic")
            else:
                chip.set_label(codes[i])
            if city is self.focus:
                chip.add_css_class("selected")
            chip.connect("clicked", lambda _b, c=city: self.on_select(c))
            column.append(self._hover_slot(chip, lambda c=city: c))

        self.on_hover(None)
        old = self.pages.get_visible_child()
        self._serial += 1
        self.pages.add_named(column, f"page{self._serial}")
        self.pages.set_transition_type(transition)
        self.pages.set_visible_child(column)
        if old is not None:
            if transition == Gtk.StackTransitionType.NONE:
                self.pages.remove(old)
            else:
                GLib.timeout_add(self.pages.get_transition_duration() + 60, self._drop_page, old)

        self.up.set_sensitive(self.start > 0)
        self.down.set_sensitive(self.start < self._max_start())

    def _hover_slot(self, chip, city_of):
        """Wraps a code in a slot that stays put: hovering the slot lifts the code (CSS) and lights up the map."""
        slot = Gtk.Box(css_classes=["rail-slot"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        slot.append(chip)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.on_hover(city_of()))
        motion.connect("leave", lambda *_: self.on_hover(None))
        slot.add_controller(motion)
        return slot

    def _drop_page(self, page):
        if page.get_parent() is self.pages and page is not self.pages.get_visible_child():
            self.pages.remove(page)
        return GLib.SOURCE_REMOVE


class DashboardPage(Gtk.ScrolledWindow):
    def __init__(self, app, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.app = app
        self.window = window
        self.signals = util.SignalBin()
        self.focus_key = None  # None means the home city
        self.cards = []
        self.city_index = places.CityIndex()

        self.root = Gtk.Box(spacing=28, margin_top=6, margin_bottom=28, margin_start=32, margin_end=24)

        self.left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=380)
        heading_row = Gtk.Box(spacing=12)
        heading, _ = two_tone_heading("Your day", "across the globe")
        heading.set_hexpand(True)
        heading_row.append(heading)
        add_city = Gtk.Button(icon_name="list-add-symbolic", css_classes=["chip-button", "large"],
                              tooltip_text="Add city", valign=Gtk.Align.CENTER)
        add_city.connect("clicked", lambda *_: self._add_city())
        heading_row.append(add_city)
        self.left.append(heading_row)

        # Hero and city cards stack in the wide layout and sit side by side in the compact one.
        self.hero_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.hero = HeroCard(app, self._choose_home)
        back_home = Gtk.GestureClick()
        back_home.connect("released", lambda *_: self._set_focus_city(None))
        self.hero.add_controller(back_home)
        self.hero.set_tooltip_text("Show local time")
        self.hero_row.append(self.hero)
        self.flow = Gtk.FlowBox(min_children_per_line=2, max_children_per_line=2, homogeneous=True,
                                column_spacing=14, row_spacing=14, selection_mode=Gtk.SelectionMode.NONE,
                                valign=Gtk.Align.START, css_classes=["city-flow"])
        self.flow.connect("child-activated", self._on_card_activated)
        self.hero_row.append(self.flow)
        self.left.append(self.hero_row)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        location = Gtk.Box(spacing=8, css_classes=["focus-location"])
        location.append(Gtk.Image.new_from_icon_name("mark-location-symbolic"))
        self.location_name = label("", "focus-name", ellipsize=Pango.EllipsizeMode.END)
        self.location_note = label("", "focus-note", ellipsize=Pango.EllipsizeMode.END)
        location.append(self.location_name)
        location.append(self.location_note)
        right.append(location)

        big = Gtk.Box(css_classes=["big-time-row"])
        self.big_main = label("", "big-time")
        self.big_seconds = label("", "big-time", "big-time-dim")
        self.big_meridiem = label("", "big-meridiem", valign=Gtk.Align.END)
        big.append(self.big_main)
        big.append(self.big_seconds)
        big.append(self.big_meridiem)
        right.append(big)

        self.map = WorldMap()
        self.map.on_city_clicked = self._set_focus_city
        right.append(self.map)

        self.cards_row = Gtk.Box(spacing=16, homogeneous=True, margin_top=8)
        self.today = TodayCard()
        self.sun = SunCard()
        self.cards_row.append(self.today)
        self.cards_row.append(self.sun)
        right.append(self.cards_row)

        self.rail = CityRail(self._set_focus_city, self._on_rail_hover)

        main = Gtk.Box(spacing=18, hexpand=True, valign=Gtk.Align.START)
        main.append(right)
        main.append(self.rail)

        self.root.append(self.left)
        self.root.append(main)
        self.set_child(self.root)
        # Give content its natural height (not its minimum) so the map keeps its proportions when scrolling.
        self.get_child().set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)

        self.signals.connect(app.cities, "changed", lambda *_: self.rebuild())
        self.signals.connect(app.weather, "updated", lambda *_: self.tick(refresh_hero=True))
        self.signals.connect(app.photos, "ready", lambda *_: self.tick(refresh_hero=True))
        self.signals.connect(app.settings, "changed", lambda *_: self.tick(refresh_hero=True))
        self.signals.connect(app.alarms, "changed", lambda *_: self.tick(refresh_hero=True))
        app.weather.acquire()
        self.signals.connect(app.ticker, "second", lambda *_: self.tick())
        self.signals.connect(util.interface_settings(), "changed::clock-format", lambda *_: self.tick())
        self.rebuild()

    def teardown(self):
        self.app.weather.release()
        self.signals.disconnect_all()

    def set_layout(self, mode):
        """mode: 'wide' (two columns), 'compact' (stacked, hero beside cities) or 'narrow' (single column)."""
        vertical, horizontal = Gtk.Orientation.VERTICAL, Gtk.Orientation.HORIZONTAL
        self.root.set_orientation(horizontal if mode == "wide" else vertical)
        self.left.set_size_request(380 if mode == "wide" else -1, -1)
        self.hero_row.set_orientation(horizontal if mode == "compact" else vertical)
        self.hero.set_size_request(360 if mode == "compact" else -1, -1)
        self.flow.set_hexpand(mode == "compact")
        self.flow.set_min_children_per_line(1 if mode == "narrow" else 2)
        self.cards_row.set_orientation(vertical if mode == "narrow" else horizontal)
        self.rail.set_visible(mode != "narrow")
        margin = 16 if mode == "narrow" else 32
        self.root.set_margin_start(margin)
        self.root.set_margin_end(margin - 8 if mode == "wide" else margin)

    # -- focus -------------------------------------------------------------

    @property
    def home(self):
        return self.app.cities.home

    def focus_city(self):
        if self.focus_key is not None:
            for city in self.app.cities.items:
                if places.city_key(city) == self.focus_key:
                    return city
            self.focus_key = None
        return self.home

    def _set_focus_city(self, city):
        self.focus_key = None if city is self.home or city is None else places.city_key(city)
        self.rebuild_rail()
        self.tick(refresh_map=True)

    def _on_city_hover(self, city):
        self.map.set_highlight(city)

    def _on_rail_hover(self, city):
        self.map.set_highlight(city)

    def _on_card_activated(self, _flow, child):
        widget = child.get_child()
        if isinstance(widget, CityCard):
            focused = self.focus_key == places.city_key(widget.city)
            self._set_focus_city(None if focused else widget.city)
        elif getattr(widget, "is_add_card", False):
            self._add_city()

    # -- structure ---------------------------------------------------------

    def rebuild(self):
        self.flow.remove_all()
        self.cards = []
        for city in self.app.cities.items:
            card = CityCard(city, self.app.cities.remove, self.app.weather, self._on_city_hover)
            self.cards.append(card)
            self.flow.append(card)
        if not self.cards:
            add = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.FILL,
                          css_classes=["lc-card", "add-city-card"])
            add.is_add_card = True
            add.append(Gtk.Image(icon_name="list-add-symbolic", css_classes=["add-city-icon"],
                                 vexpand=True, valign=Gtk.Align.END))
            add.append(Gtk.Label(label="Add a city", css_classes=["card-caption"], vexpand=True,
                                 valign=Gtk.Align.START))
            self.flow.append(add)
        self.rebuild_rail()
        self.tick(refresh_map=True, refresh_hero=True)

    def rebuild_rail(self):
        entries = [(c, False) for c in self.app.cities.items]
        self.rail.set_entries(self.home, entries, self.focus_city())

    # -- live updates ------------------------------------------------------

    def tick(self, refresh_map=False, refresh_hero=False):
        home = self.home
        here = places.now_in(home)
        self.hero.update(home, force=refresh_hero)
        focus = self.focus_city()
        for card in self.cards:
            card.update(here, focus is card.city)

        now = places.now_in(focus)
        is_home = focus is home
        self.location_name.set_label(", ".join(p for p in (focus["name"], focus.get("country")) if p))
        if is_home:
            self.location_note.set_label("· Local time")
        else:
            diff = (now.utcoffset() - here.utcoffset()).total_seconds()
            self.location_note.set_label(f"· {util.describe_difference(diff)}")
        text, meridiem = util.format_hm(now)
        self.big_main.set_label(f"{text}:")
        self.big_seconds.set_label(f"{now.second:02d}")
        self.big_meridiem.set_label(meridiem)
        self.big_meridiem.set_visible(bool(meridiem))

        offset_label = util.format_offset(now.utcoffset())
        if refresh_map or now.second == 0 or self.map.focus is not focus or self.map.focus_label != offset_label:
            self.map.update(self.app.cities.items, home, focus, offset_label)
        self.today.update(now, focus["name"], is_home)
        self.sun.update(focus, now)

    # -- dialogs -----------------------------------------------------------

    def _add_city(self):
        existing = [places.city_key(c) for c in self.app.cities.items]
        dialog = CitySearchDialog(self.city_index, self.app.cities.add, exclude=existing)
        dialog.present(self.window)

    def _choose_home(self):
        dialog = CitySearchDialog(self.city_index, self.app.cities.set_home, title="Set Home City")
        dialog.present(self.window)

