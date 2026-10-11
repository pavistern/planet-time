"""Living sky: an illustrated sky for the real time of day at a city, in the app's warm palette."""

import math
import random
import zlib
from datetime import datetime, timedelta, timezone

from gi.repository import Gdk, Graphene, Gsk, Gtk

from . import places, util
from .util import point, rgba

# (sun altitude in degrees, sky colour at the top, sky colour at the horizon)
KEYFRAMES = [
    (-18, "#100e17", "#211922"),
    (-10, "#1b1729", "#46293b"),
    (-4, "#3a2f52", "#b8625a"),
    (1, "#6f6489", "#ee915c"),
    (8, "#9db3cb", "#f4bf96"),
    (22, "#adc6da", "#f5e1cd"),
]


def _mix(a, b, t):
    ca, cb = rgba(a), rgba(b)
    out = ca.copy()
    out.red = ca.red + (cb.red - ca.red) * t
    out.green = ca.green + (cb.green - ca.green) * t
    out.blue = ca.blue + (cb.blue - ca.blue) * t
    return out


def sky_colors(altitude):
    if altitude <= KEYFRAMES[0][0]:
        return rgba(KEYFRAMES[0][1]), rgba(KEYFRAMES[0][2])
    for (a0, top0, bot0), (a1, top1, bot1) in zip(KEYFRAMES, KEYFRAMES[1:]):
        if altitude <= a1:
            t = (altitude - a0) / (a1 - a0)
            return _mix(top0, top1, t), _mix(bot0, bot1, t)
    return rgba(KEYFRAMES[-1][1]), rgba(KEYFRAMES[-1][2])


def is_bright(altitude):
    """Whether the sky behind the text is light enough to need dark text."""
    return altitude > 3


def _stop(offset, color):
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = color
    return stop


class SkyScene(Gtk.Widget):
    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.city = None
        self.when = None  # None = now; a UTC datetime to preview other times
        self._skyline_key = None
        self._skyline = None

    def show_city(self, city, when=None):
        self.city, self.when = city, when
        self.queue_draw()

    def now_utc(self):
        return self.when or datetime.now(timezone.utc)

    def altitude(self):
        if not self.city:
            return 30
        return util.sun_altitude(self.city["lat"], self.city["lon"], util.subsolar_point(self.now_utc()))

    # -- drawing -------------------------------------------------------------

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width < 10 or height < 10 or not self.city:
            return
        now = self.now_utc()
        altitude = self.altitude()
        top, horizon = sky_colors(altitude)
        bounds = Graphene.Rect().init(0, 0, width, height)
        snapshot.append_linear_gradient(bounds, point(0, 0), point(0, height * 0.82),
                                        [_stop(0, top), _stop(1, horizon)])

        rng = random.Random(zlib.crc32(self.city["name"].encode()))
        star_alpha = max(0.0, min(1.0, (-altitude - 3) / 9))
        if star_alpha > 0:
            builder_big, builder_small = Gsk.PathBuilder.new(), Gsk.PathBuilder.new()
            for _ in range(70):
                x, y = rng.random() * width, rng.random() * height * 0.68
                (builder_big if rng.random() < 0.25 else builder_small).add_circle(point(x, y), 1.0)
            snapshot.append_fill(builder_small.to_path(), Gsk.FillRule.WINDING, rgba("#fff6e6", 0.45 * star_alpha))
            snapshot.append_fill(builder_big.to_path(), Gsk.FillRule.WINDING, rgba("#fff6e6", 0.9 * star_alpha))

        self._draw_body(snapshot, width, height, now, altitude)
        self._draw_skyline(snapshot, width, height, altitude)

    def _arc_position(self, fraction, width, height):
        horizon_y, top_y = height * 0.80, height * 0.17
        x = width * (0.30 + 0.62 * fraction)
        y = horizon_y - (horizon_y - top_y) * math.sin(math.pi * fraction)
        return x, y

    def _draw_body(self, snapshot, width, height, now, altitude):
        tz = places.zone_of(self.city)
        local = now.astimezone(tz)
        sunrise, sunset, state = util.sun_times(self.city["lat"], self.city["lon"], local.date(), tz)
        if state == "polar-day" or (state == "normal" and sunrise <= local <= sunset):
            fraction = 0.5 if state != "normal" else (local - sunrise) / (sunset - sunrise)
            self._draw_sun(snapshot, *self._arc_position(fraction, width, height), altitude)
            return
        # Night: travel from sunset to the next sunrise.
        if state == "normal":
            if local < sunrise:
                # Before dawn the night began at yesterday's sunset.
                _, start, _ = util.sun_times(self.city["lat"], self.city["lon"], local.date() - timedelta(days=1), tz)
                end = sunrise
            else:
                start = sunset
                end, _, _ = util.sun_times(self.city["lat"], self.city["lon"], local.date() + timedelta(days=1), tz)
            fraction = (local - start) / (end - start) if start and end else 0.5
        else:
            fraction = 0.5
        self._draw_moon(snapshot, *self._arc_position(fraction, width, height), now)

    def _draw_sun(self, snapshot, x, y, altitude):
        low = max(0.0, min(1.0, 1 - altitude / 25))
        core = _mix("#fff3cf", "#ffb46a", low)
        glow = core.copy()
        for radius, alpha in ((54, 0.10), (36, 0.16), (25, 0.28)):
            glow.alpha = alpha
            snapshot.append_fill(util.circle_path(x, y, radius), Gsk.FillRule.WINDING, glow)
        snapshot.append_fill(util.circle_path(x, y, 16), Gsk.FillRule.WINDING, core)

    def _draw_moon(self, snapshot, x, y, now):
        snapshot.append_fill(util.circle_path(x, y, 13 * 2.4), Gsk.FillRule.WINDING, rgba("#f6ecda", 0.06))
        append_moon(snapshot, x, y, 13, util.moon_phase(now), rgba("#f6ecda"))

    def _buildings(self, width, height):
        key = (self.city["name"], width, height)
        if key == self._skyline_key:
            return self._skyline
        rng = random.Random(zlib.crc32(self.city["name"].encode()) ^ 0x5EED)
        layers = []
        for depth, (low, high, w_min, w_max) in enumerate(((0.07, 0.17, 18, 46), (0.04, 0.11, 14, 34))):
            x, blocks = -6, []
            while x < width + 6:
                w = rng.uniform(w_min, w_max)
                h = rng.uniform(low, high) * min(height, 300)  # same building sizes on tall cards
                if depth == 1 and rng.random() < 0.12:
                    h *= 1.6  # the occasional tower
                spire = depth == 1 and rng.random() < 0.18
                windows = []
                if depth == 1:
                    for wy in range(int(height - h + 8), int(height - 6), 9):
                        for wx in range(int(x + 5), int(x + w - 5), 8):
                            if rng.random() < 0.28:
                                windows.append((wx, wy))
                blocks.append((x, w, h, spire, windows))
                x += w + rng.uniform(0, 4 if depth else 2)
            layers.append(blocks)
        self._skyline_key, self._skyline = key, layers
        return layers

    def _draw_skyline(self, snapshot, width, height, altitude):
        night = max(0.0, min(1.0, (-altitude + 2) / 10))
        far_color = _mix("#b48f86", "#2a2028", night)
        far_color.alpha = 0.55
        near_color = _mix("#5a463f", "#16121a", night)
        lit = rgba("#ffcf7a", 0.85 * night)
        for depth, blocks in enumerate(self._buildings(width, height)):
            builder = Gsk.PathBuilder.new()
            windows = Gsk.PathBuilder.new()
            for x, w, h, spire, lights in blocks:
                builder.add_rect(Graphene.Rect().init(x, height - h, w, h + 1))
                if spire:
                    builder.add_rect(Graphene.Rect().init(x + w / 2 - 1, height - h - 14, 2, 14))
                for wx, wy in lights:
                    windows.add_rect(Graphene.Rect().init(wx, wy, 3, 4))
            snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, far_color if depth == 0 else near_color)
            if depth == 1 and night > 0.05:
                snapshot.append_fill(windows.to_path(), Gsk.FillRule.WINDING, lit)


def append_moon(snapshot, x, y, r, phase, color, shade_alpha=0.13):
    """Moon disc at (x, y) with the lit part for `phase` (0 new, 0.5 full) and a faint unlit part."""
    shade = color.copy()
    shade.alpha *= shade_alpha
    snapshot.append_fill(util.circle_path(x, y, r), Gsk.FillRule.WINDING, shade)
    side = 1 if phase < 0.5 else -1
    terminator = r * math.cos(math.tau * phase)
    builder = Gsk.PathBuilder.new()
    steps = 40
    for i in range(steps + 1):
        t = -math.pi / 2 + math.pi * i / steps
        px, py = x + side * r * math.cos(t), y + r * math.sin(t)
        (builder.move_to if i == 0 else builder.line_to)(px, py)
    for i in range(steps + 1):
        t = math.pi / 2 - math.pi * i / steps
        builder.line_to(x + side * terminator * math.cos(t), y + r * math.sin(t))
    builder.close()
    snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, color)


class PhotoView(Gtk.Widget):
    """Draws an image cropped to cover the widget, without requesting the image's size."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.path = None
        self.texture = None

    def set_path(self, path):
        if path != self.path:
            self.path = path
            self.texture = Gdk.Texture.new_from_filename(path) if path else None
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = max(width / tw, height / th)
        dw, dh = tw * scale, th * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init((width - dw) / 2, (height - dh) / 2, dw, dh))
        snapshot.pop()


class MoonGlyph(Gtk.Widget):
    """16 px moon showing the current phase, drawn in the widget's text colour."""

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.phase = 0.0

    def set_phase(self, phase):
        self.phase = phase
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return 16, 16, -1, -1

    def do_snapshot(self, snapshot):
        size = min(self.get_width(), self.get_height())
        append_moon(snapshot, self.get_width() / 2, self.get_height() / 2, size / 2 - 1, self.phase,
                    self.get_color(), shade_alpha=0.25)
