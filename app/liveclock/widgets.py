"""Custom-drawn widgets: analog clock, dotted world map, sun arc, progress ring, time picker."""

import math
import os
from datetime import datetime, timezone

from gi.repository import Adw, GLib, GObject, Graphene, Gsk, Gtk, Pango

from . import util
from .util import point, round_stroke, with_alpha


def places_key(city):
    return (city["name"], city.get("country", ""), city["tz"])


def dark_mode():
    return Adw.StyleManager.get_default().get_dark()


def icon_chip(icon_name, css_class="chip", tooltip=None):
    chip = Gtk.Image.new_from_icon_name(icon_name)
    chip.add_css_class(css_class)
    if tooltip:
        chip.set_tooltip_text(tooltip)
    return chip


def pill(text, *classes):
    label = Gtk.Label(label=text, css_classes=["tag", *classes], halign=Gtk.Align.START,
                      valign=Gtk.Align.CENTER)
    return label


def label(text="", *classes, **props):
    widget = Gtk.Label(label=text, css_classes=list(classes), **props)
    if "xalign" not in props and "halign" not in props:
        widget.set_xalign(0)
    return widget


def button(text=None, icon=None, *classes, tooltip=None):
    btn = Gtk.Button(css_classes=["lc-button", *classes])
    if icon and text:
        content = Adw.ButtonContent(icon_name=icon, label=text)
        btn.set_child(content)
    elif icon:
        btn.set_icon_name(icon)
    else:
        btn.set_label(text)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    return btn


def two_tone_heading(title, subtitle):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(label(title, "hero-title", wrap=True))
    sub = label(subtitle, "hero-subtitle", wrap=True)
    box.append(sub)
    return box, sub


# ---------------------------------------------------------------------------

class AnalogClock(Gtk.Widget):
    """Ticking analog face. Uses the widget's CSS colour for ink and the palette orange for accents."""

    def __init__(self, size=160):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class("analog-clock")
        self.size = size
        self.time = datetime.now()

    def set_time(self, dt):
        self.time = dt
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        fg = self.get_color()
        accent = util.palette_color(dark_mode(), "orange")
        r = min(width, height) / 2 - 2
        if r <= 0:
            return

        def polar(angle, dist):
            return dist * math.sin(angle), -dist * math.cos(angle)

        def segments(pairs):
            builder = Gsk.PathBuilder.new()
            for (x1, y1), (x2, y2) in pairs:
                builder.move_to(x1, y1)
                builder.line_to(x2, y2)
            return builder.to_path()

        snapshot.save()
        snapshot.translate(point(width / 2, height / 2))
        face = util.circle_path(0, 0, r)
        snapshot.append_fill(face, Gsk.FillRule.WINDING, with_alpha(fg, 0.07))
        snapshot.append_stroke(face, round_stroke(1.2), with_alpha(fg, 0.18))

        outer = r * 0.9
        for major in (False, True):
            length = r * (0.1 if major else 0.04)
            angles = [i * math.pi / 30 for i in range(60) if (i % 5 == 0) == major]
            marks = segments((polar(a, outer - length), polar(a, outer)) for a in angles)
            snapshot.append_stroke(marks, round_stroke(max(2, r * 0.025) if major else 1),
                                   with_alpha(fg, 0.85 if major else 0.3))

        t = self.time
        seconds = t.second
        minutes = t.minute + seconds / 60
        hours = t.hour % 12 + minutes / 60
        hands = [
            (hours * math.pi / 6, r * 0.5, r * 0.08, max(3, r * 0.055), fg),
            (minutes * math.pi / 30, r * 0.74, r * 0.08, max(2.5, r * 0.035), fg),
            (seconds * math.pi / 30, r * 0.82, r * 0.18, 1.6, accent),
        ]
        for angle, length, tail, width_px, color in hands:
            snapshot.append_stroke(segments([(polar(angle, -tail), polar(angle, length))]),
                                   round_stroke(width_px), color)
        snapshot.append_fill(util.circle_path(0, 0, r * 0.05), Gsk.FillRule.WINDING, accent)
        snapshot.restore()


# ---------------------------------------------------------------------------

_MASK = None


def land_mask():
    """(cols, rows, lat_top, lat_bottom, [(row, col), ...]) from worldmap.txt."""
    global _MASK
    if _MASK is None:
        path = os.path.join(os.path.dirname(__file__), "worldmap.txt")
        with open(path, encoding="utf-8") as fh:
            # Map rows contain only "#" (land) and "." (sea); anything else is a comment.
            rows = [line.strip() for line in fh if line.strip() and set(line.strip()) <= {"#", "."}]
        cells = [(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row) if ch == "#"]
        _MASK = (len(rows[0]), len(rows), 84.0, -60.0, cells)
    return _MASK


class WorldMap(Gtk.Widget):
    """Dotted world map shaded by daylight, with city markers and a time-zone line for the focus city."""

    LABEL_SPACE = 22

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("world-map")
        self.cities = []  # dicts with name, lat, lon
        self.home = None
        self.focus = None
        self.focus_label = ""
        self.on_city_clicked = None
        self.highlight = None  # city under the pointer elsewhere (card or strip): gets a flight path
        self._highlight_start = 0
        self._highlight_tick = 0
        self._geometry = None
        self._dots_cache = None
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_query_tooltip)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)
        self.set_cursor_from_name("default")

    def update(self, cities, home, focus, focus_label):
        self.cities, self.home, self.focus, self.focus_label = cities, home, focus, focus_label
        self.queue_draw()

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def set_highlight(self, city):
        if city is self.highlight:
            return
        self.highlight = city
        self._highlight_start = GLib.get_monotonic_time()
        if city is not None and animations_enabled():
            if not self._highlight_tick:
                self._highlight_tick = self.add_tick_callback(lambda *_: self.queue_draw() or True)
        elif self._highlight_tick:
            self.remove_tick_callback(self._highlight_tick)
            self._highlight_tick = 0
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 220, 520, -1, -1
        cols, rows, *_ = land_mask()
        natural = int(for_size * rows / cols) + 2 * self.LABEL_SPACE if for_size > 0 else 260
        return 150, max(150, natural), -1, -1

    def _layout(self):
        cols, rows, lat_top, lat_bottom, _ = land_mask()
        width, height = self.get_width(), self.get_height() - 2 * self.LABEL_SPACE
        cell = max(1.0, min(width / cols, height / rows))
        map_w, map_h = cell * cols, cell * rows
        ox = (width - map_w) / 2
        oy = self.LABEL_SPACE + (height - map_h) / 2
        return cell, ox, oy, map_w, map_h, lat_top, lat_bottom

    def project(self, lat, lon):
        cell, ox, oy, map_w, map_h, lat_top, lat_bottom = self._layout()
        lat = max(lat_bottom, min(lat_top, lat))
        return ox + (lon + 180) / 360 * map_w, oy + (lat_top - lat) / (lat_top - lat_bottom) * map_h

    def _dot_paths(self):
        cols, rows, lat_top, lat_bottom, cells = land_mask()
        cell, ox, oy, *_ = self._layout()
        now = datetime.now(timezone.utc)
        key = (self.get_width(), self.get_height(), now.strftime("%Y%m%d%H%M"))
        if self._dots_cache and self._dots_cache[0] == key:
            return self._dots_cache[1]
        subsolar = util.subsolar_point(now)
        lat_step = (lat_top - lat_bottom) / rows
        builders = [Gsk.PathBuilder.new() for _ in range(3)]
        radius = cell * 0.32
        for r, c in cells:
            lat = lat_top - (r + 0.5) * lat_step
            lon = -180 + (c + 0.5) * 360 / cols
            altitude = util.sun_altitude(lat, lon, subsolar)
            level = 0 if altitude > 0 else 1 if altitude > -12 else 2
            builders[level].add_circle(point(ox + (c + 0.5) * cell, oy + (r + 0.5) * cell), radius)
        paths = [b.to_path() for b in builders]
        self._dots_cache = (key, paths)
        return paths

    def do_snapshot(self, snapshot):
        if self.get_width() < 40 or self.get_height() < 40:
            return
        ink = self.get_color()
        dark = dark_mode()
        orange = util.palette_color(dark, "orange")
        paper = util.palette_color(dark, "paper")
        for path, alpha in zip(self._dot_paths(), (0.36, 0.2, 0.1)):
            snapshot.append_fill(path, Gsk.FillRule.WINDING, with_alpha(ink, alpha))

        _cell, _ox, oy, _mw, map_h, *_ = self._layout()

        # Time-zone line through the focus city
        if self.focus:
            x, y = self.project(self.focus["lat"], self.focus["lon"])
            line = Gsk.PathBuilder.new()
            line.move_to(x, oy - 4)
            line.line_to(x, oy + map_h + 4)
            snapshot.append_stroke(line.to_path(), round_stroke(1.2, [2, 4]), with_alpha(ink, 0.55))
            self._draw_text(snapshot, "Time zone", x, oy - self.LABEL_SPACE + 2, ink, 0.7, center=True)
            self._draw_text(snapshot, self.focus_label, x, oy + map_h + 6, ink, 0.85, center=True, bold=True)

        for city in self.cities:
            if self.focus is city:
                continue
            x, y = self.project(city["lat"], city["lon"])
            snapshot.append_fill(util.circle_path(x, y, 5.5), Gsk.FillRule.WINDING, with_alpha(orange, 0.25))
            snapshot.append_fill(util.circle_path(x, y, 3), Gsk.FillRule.WINDING, orange)

        if self.home and self.home is not self.focus:
            x, y = self.project(self.home["lat"], self.home["lon"])
            snapshot.append_fill(util.circle_path(x, y, 4), Gsk.FillRule.WINDING, ink)

        if self.focus:
            x, y = self.project(self.focus["lat"], self.focus["lon"])
            snapshot.append_fill(util.circle_path(x, y, 9), Gsk.FillRule.WINDING, paper)
            snapshot.append_stroke(util.circle_path(x, y, 9), round_stroke(1.5), ink)
            snapshot.append_fill(util.circle_path(x, y, 3.5), Gsk.FillRule.WINDING, orange)
            self._draw_text(snapshot, self.focus["name"], x + 14, y - 9, ink, 0.9, bold=True)

        if self.highlight is not None:
            self._draw_highlight(snapshot, ink, orange)

    def _draw_highlight(self, snapshot, ink, orange):
        city = self.highlight
        animated = bool(self._highlight_tick)
        elapsed = (GLib.get_monotonic_time() - self._highlight_start) / 1e6 if animated else 10.0
        x, y = self.project(city["lat"], city["lon"])

        # Flight path from home, drawn out over 0.75 s (ease-out), with a dot at its tip while drawing.
        if self.home is not None and places_key(self.home) != places_key(city):
            hx, hy = self.project(self.home["lat"], self.home["lon"])
            distance = math.hypot(x - hx, y - hy)
            cx, cy = (hx + x) / 2, min(hy, y) - distance * 0.28
            progress = 1 - (1 - min(1.0, elapsed / 0.75)) ** 3
            builder = Gsk.PathBuilder.new()
            steps = 48
            head = (hx, hy)
            for i in range(steps + 1):
                t = progress * i / steps
                px = (1 - t) ** 2 * hx + 2 * (1 - t) * t * cx + t ** 2 * x
                py = (1 - t) ** 2 * hy + 2 * (1 - t) * t * cy + t ** 2 * y
                (builder.move_to if i == 0 else builder.line_to)(px, py)
                head = (px, py)
            snapshot.append_stroke(builder.to_path(), round_stroke(2.4), orange)
            if progress < 1:
                snapshot.append_fill(util.circle_path(*head, 3.2), Gsk.FillRule.WINDING, orange)

        # Pulse rings on the city
        for offset in (0.0, 0.5):
            phase = ((elapsed / 1.4) + offset) % 1 if animated else 0.4
            ring = util.circle_path(x, y, 5 + 13 * phase)
            snapshot.append_stroke(ring, round_stroke(1.8), with_alpha(orange, 0.85 * (1 - phase)))
        snapshot.append_fill(util.circle_path(x, y, 4.5), Gsk.FillRule.WINDING, orange)

        label_alpha = max(0.0, min(1.0, (elapsed - 0.35) / 0.25))
        if label_alpha > 0:
            from .places import now_in
            text = f"{city['name']} {util.format_hm_text(now_in(city))}"
            right_side = x < self.get_width() * 0.75
            self._draw_text(snapshot, text, x + 12 if right_side else x - 12, y - 22, ink, 0.95 * label_alpha,
                            bold=True, anchor_end=not right_side)

    def _draw_text(self, snapshot, text, x, y, color, alpha, center=False, bold=False, anchor_end=False):
        layout = self.create_pango_layout(text)
        desc = layout.get_context().get_font_description().copy()
        desc.set_size(int(8.5 * Pango.SCALE))
        desc.set_weight(Pango.Weight.SEMIBOLD if bold else Pango.Weight.NORMAL)
        layout.set_font_description(desc)
        _ink, logical = layout.get_pixel_extents()
        if center:
            x -= logical.width / 2
        elif anchor_end:
            x -= logical.width
        x = max(0, min(self.get_width() - logical.width, x))
        snapshot.save()
        snapshot.translate(point(x, y))
        snapshot.append_layout(layout, with_alpha(color, alpha))
        snapshot.restore()

    def _city_at(self, x, y, radius=12):
        best, best_dist = None, radius
        for city in self.cities + ([self.home] if self.home else []):
            cx, cy = self.project(city["lat"], city["lon"])
            dist = math.hypot(cx - x, cy - y)
            if dist <= best_dist:
                best, best_dist = city, dist
        return best

    def _on_query_tooltip(self, _widget, x, y, _keyboard, tooltip):
        city = self._city_at(x, y)
        if city is None:
            return False
        from .places import now_in
        tooltip.set_text(f"{city['name']} · {util.format_hm_text(now_in(city))}")
        return True

    def _on_click(self, _gesture, _n_press, x, y):
        city = self._city_at(x, y)
        if city is not None and self.on_city_clicked:
            self.on_city_clicked(city)


# ---------------------------------------------------------------------------

class SunArc(Gtk.Widget):
    """Dashed day arc from sunrise to sunset with the sun's current position."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("sun-arc")
        self.fraction = None  # 0..1 through the day, None when unknown

    def set_fraction(self, fraction):
        self.fraction = fraction
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return (120, 200, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (44, 60, -1, -1)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        ink = self.get_color()
        cx, base = width / 2, height - 4
        rx, ry = width / 2 - 10, height - 12
        horizon = Gsk.PathBuilder.new()
        horizon.move_to(4, base)
        horizon.line_to(width - 4, base)
        snapshot.append_stroke(horizon.to_path(), round_stroke(1), with_alpha(ink, 0.25))
        snapshot.append_stroke(util.arc_path(cx, base, rx, ry, math.pi, math.tau), round_stroke(1.4, [3, 5]),
                               with_alpha(ink, 0.45))
        if self.fraction is None:
            return
        frac = max(0.0, min(1.0, self.fraction))
        angle = math.pi + math.pi * frac
        if frac > 0:
            snapshot.append_stroke(util.arc_path(cx, base, rx, ry, math.pi, angle), round_stroke(2.4),
                                   with_alpha(ink, 0.9))
        x, y = cx + rx * math.cos(angle), base + ry * math.sin(angle)
        daytime = 0 < self.fraction < 1
        snapshot.append_fill(util.circle_path(x, y, 7), Gsk.FillRule.WINDING,
                             with_alpha(ink, 0.9 if daytime else 0.25))
        if daytime:
            snapshot.append_fill(util.circle_path(x, y, 3), Gsk.FillRule.WINDING,
                                 util.palette_color(dark_mode(), "yellow"))


# ---------------------------------------------------------------------------

class ProgressRing(Gtk.Widget):
    def __init__(self, size=190):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.add_css_class("progress-ring")
        self.size = size
        self.fraction = 1.0

    def set_fraction(self, fraction):
        self.fraction = max(0.0, min(1.0, fraction))
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        ink = self.get_color()
        thickness = 9
        r = min(width, height) / 2 - thickness
        cx, cy = width / 2, height / 2
        snapshot.append_stroke(util.circle_path(cx, cy, r), round_stroke(thickness), with_alpha(ink, 0.08))
        if self.fraction > 0.001:
            start = -math.pi / 2
            snapshot.append_stroke(util.arc_path(cx, cy, r, r, start, start + math.tau * self.fraction, 128),
                                   round_stroke(thickness), util.palette_color(dark_mode(), "orange"))


# ---------------------------------------------------------------------------

class TimePicker(Gtk.Box):
    """Big vertical spin buttons for hours, minutes and optionally seconds."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, with_seconds=False, hour_max=23):
        super().__init__(spacing=6, halign=Gtk.Align.CENTER, css_classes=["time-picker"])
        self.hours = self._spin(hour_max)
        self.minutes = self._spin(59)
        self.seconds = self._spin(59) if with_seconds else None
        parts = [self.hours, self.minutes] + ([self.seconds] if self.seconds else [])
        for i, spin in enumerate(parts):
            if i:
                self.append(Gtk.Label(label=":", css_classes=["time-picker-colon"]))
            self.append(spin)

    def _spin(self, maximum):
        spin = Gtk.SpinButton.new_with_range(0, maximum, 1)
        spin.set_orientation(Gtk.Orientation.VERTICAL)
        spin.set_wrap(True)
        spin.set_numeric(True)
        spin.add_css_class("time-spin")
        spin.connect("output", self._on_output)
        spin.connect("value-changed", lambda *_: self.emit("changed"))
        return spin

    @staticmethod
    def _on_output(spin):
        spin.set_text(f"{int(spin.get_value()):02d}")
        return True

    def get_values(self):
        values = [int(self.hours.get_value()), int(self.minutes.get_value())]
        if self.seconds:
            values.append(int(self.seconds.get_value()))
        return values

    def set_values(self, hours, minutes, seconds=0):
        self.hours.set_value(hours)
        self.minutes.set_value(minutes)
        if self.seconds:
            self.seconds.set_value(seconds)


# ---------------------------------------------------------------------------

CLOUD_PATH = "M7 18h10.5a3.8 3.8 0 0 0 .4-7.6A5.6 5.6 0 0 0 7.2 9.6 4.2 4.2 0 0 0 7 18z"
MOON_PATH = "M15 4.2a7.8 7.8 0 1 0 4.8 13.6A6.3 6.3 0 0 1 15 4.2z"
BOLT_PATH = "M13.2 14.5l-2.6 3.6h3.1l-2.4 3.9"

# Theme icon name -> glyph kind
GLYPH_KINDS = {
    "weather-clear": "sun", "weather-clear-night": "moon",
    "weather-few-clouds": "partly", "weather-few-clouds-night": "partly-night",
    "weather-overcast": "cloud", "weather-fog": "fog",
    "weather-showers-scattered": "rain", "weather-showers": "rain",
    "weather-snow": "snow", "weather-storm": "storm",
}


def animations_enabled():
    settings = Gtk.Settings.get_default()
    return settings is None or settings.get_property("gtk-enable-animations")


class WeatherGlyph(Gtk.Widget):
    """Line-art weather icon that animates while `play()` is active (sun turns, rain falls, …)."""

    _paths = {}

    def __init__(self, size=18):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.size = size
        self.kind = "sun"
        self._tick = 0
        self._start = 0

    @classmethod
    def path(cls, name):
        if name not in cls._paths:
            cls._paths[name] = Gsk.Path.parse({"cloud": CLOUD_PATH, "moon": MOON_PATH, "bolt": BOLT_PATH}[name])
        return cls._paths[name]

    def set_icon(self, icon_name):
        kind = GLYPH_KINDS.get(icon_name.removesuffix("-symbolic"), "cloud")
        if kind != self.kind:
            self.kind = kind
            self.queue_draw()

    def play(self):
        if not self._tick and animations_enabled():
            self._start = GLib.get_monotonic_time()
            self._tick = self.add_tick_callback(lambda *_: self.queue_draw() or True)

    def stop(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        t = (GLib.get_monotonic_time() - self._start) / 1e6 if self._tick else 0.0
        ink = self.get_color()
        stroke = round_stroke(1.9)
        scale = min(self.get_width(), self.get_height()) / 24
        snapshot.save()
        snapshot.scale(scale, scale)
        kind = self.kind

        def sun(cx, cy, r, ray_in, ray_out, rays=8):
            snapshot.append_stroke(util.circle_path(cx, cy, r), stroke, ink)
            builder = Gsk.PathBuilder.new()
            turn = t * math.tau / 7
            for i in range(rays):
                a = turn + i * math.tau / rays
                builder.move_to(cx + ray_in * math.cos(a), cy + ray_in * math.sin(a))
                builder.line_to(cx + ray_out * math.cos(a), cy + ray_out * math.sin(a))
            snapshot.append_stroke(builder.to_path(), stroke, ink)

        def moon(cx=12, cy=12, rock=10, scale_by=1.0):
            snapshot.save()
            snapshot.translate(point(cx, cy))
            snapshot.rotate(rock * math.sin(math.tau * t / 3))
            snapshot.scale(scale_by, scale_by)
            snapshot.translate(point(-12, -12))
            snapshot.append_stroke(self.path("moon"), stroke, ink)
            snapshot.restore()

        drift = 1.4 * math.sin(math.tau * t / 4.8)

        def cloud(dy=0.0):
            snapshot.save()
            snapshot.translate(point(drift, dy))
            snapshot.append_stroke(self.path("cloud"), stroke, ink)
            snapshot.restore()

        def behind_cloud(draw, dy=0.0):
            # Clip whatever peeks out from behind the cloud to the area outside the cloud shape.
            clip = Gsk.PathBuilder.new()
            clip.add_rect(Graphene.Rect().init(-8 - drift, -8 - dy, 40, 40))
            clip.add_path(self.path("cloud"))
            snapshot.save()
            snapshot.translate(point(drift, dy))
            snapshot.push_fill(clip.to_path(), Gsk.FillRule.EVEN_ODD)
            snapshot.translate(point(-drift, -dy))
            draw()
            snapshot.pop()
            snapshot.restore()

        if kind == "sun":
            sun(12, 12, 4.2, 7.0, 9.4)
        elif kind == "moon":
            moon()
        elif kind == "partly":
            behind_cloud(lambda: sun(16.2, 7.6, 2.8, 4.6, 6.2), dy=1.5)
            cloud(1.5)
        elif kind == "partly-night":
            behind_cloud(lambda: moon(15.5, 8, rock=8, scale_by=0.55), dy=1.5)
            cloud(1.5)
        elif kind == "cloud":
            cloud()
        elif kind in ("rain", "snow", "storm"):
            cloud(-3.5)
            if kind == "storm":
                flash = 1.0 if not self._tick or (t % 1.6) < 0.12 or 0.2 < (t % 1.6) < 0.3 else 0.45
                snapshot.append_stroke(self.path("bolt"), stroke, with_alpha(ink, flash))
            else:
                period = 0.9 if kind == "rain" else 1.7
                for i, x in enumerate((9.0, 12.5, 16.0)):
                    phase = ((t / period) + i / 3) % 1 if self._tick else 0.35
                    alpha = math.sin(math.pi * phase) if self._tick else 1.0
                    y = 16.5 + 4.5 * phase
                    if kind == "rain":
                        drop = Gsk.PathBuilder.new()
                        drop.move_to(x, y)
                        drop.line_to(x - 0.8, y + 2.4)
                        snapshot.append_stroke(drop.to_path(), stroke, with_alpha(ink, alpha))
                    else:
                        sway = 0.8 * math.sin(math.tau * (t + i) / 1.3) if self._tick else 0
                        snapshot.append_fill(util.circle_path(x + sway, y + 1, 1.1), Gsk.FillRule.WINDING,
                                             with_alpha(ink, alpha))
        elif kind == "fog":
            builder = Gsk.PathBuilder.new()
            for i, (y, x0, x1) in enumerate(((8.5, 5, 19), (12.5, 3.5, 17.5), (16.5, 6.5, 20.5))):
                dx = 1.5 * math.sin(math.tau * t / 3 + i * 2.1) if self._tick else 0
                builder.move_to(x0 + dx, y)
                builder.line_to(x1 + dx, y)
            snapshot.append_stroke(builder.to_path(), stroke, ink)
        snapshot.restore()
