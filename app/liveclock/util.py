"""Time formatting, colours, signal bookkeeping and sun position maths."""

import math
from datetime import datetime, timedelta, timezone

from gi.repository import Gdk, Gio, Graphene, Gsk

MINUS = "−"


# ---------------------------------------------------------------------------
# Colours and drawing helpers

PALETTE = {
    False: {"orange": "#f4874b", "yellow": "#efe94a", "ink": "#1d1a17", "paper": "#fbf6f1"},
    True: {"orange": "#f08442", "yellow": "#e9df45", "ink": "#f6efe8", "paper": "#1d1a17"},
}


def rgba(spec, alpha=None):
    color = Gdk.RGBA()
    color.parse(spec)
    if alpha is not None:
        color.alpha = alpha
    return color


def with_alpha(color, alpha):
    faded = color.copy()
    faded.alpha *= alpha
    return faded


def palette_color(dark, name, alpha=None):
    return rgba(PALETTE[bool(dark)][name], alpha)


def point(x, y):
    return Graphene.Point().init(x, y)


def round_stroke(width, dash=None):
    stroke = Gsk.Stroke.new(width)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    if dash:
        stroke.set_dash(dash)
    return stroke


def circle_path(cx, cy, radius):
    builder = Gsk.PathBuilder.new()
    builder.add_circle(point(cx, cy), radius)
    return builder.to_path()


def arc_path(cx, cy, rx, ry, start, end, steps=64):
    """Polyline arc; angles in radians, 0 = 3 o'clock, increasing clockwise (screen coords)."""
    builder = Gsk.PathBuilder.new()
    count = max(2, int(steps * abs(end - start) / math.tau) + 2)
    for i in range(count):
        angle = start + (end - start) * i / (count - 1)
        x, y = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
        if i == 0:
            builder.move_to(x, y)
        else:
            builder.line_to(x, y)
    return builder.to_path()


def clear_box(box):
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class SignalBin:
    """Remembers signal connections so a view can drop them all when it goes away."""

    def __init__(self):
        self._handlers = []

    def connect(self, obj, signal, callback, *args):
        self._handlers.append((obj, obj.connect(signal, callback, *args)))

    def disconnect_all(self):
        for obj, handler_id in self._handlers:
            if obj.handler_is_connected(handler_id):
                obj.disconnect(handler_id)
        self._handlers.clear()


# ---------------------------------------------------------------------------
# Time formatting

_interface_settings = None


def interface_settings():
    global _interface_settings
    if _interface_settings is None:
        _interface_settings = Gio.Settings.new("org.gnome.desktop.interface")
    return _interface_settings


def uses_12h():
    return interface_settings().get_string("clock-format") == "12h"


def format_hm(dt, twelve=None):
    """Returns (time text, meridiem) e.g. ('08:24', '') or ('8:24', 'AM')."""
    if twelve is None:
        twelve = uses_12h()
    if twelve:
        return f"{dt.hour % 12 or 12}:{dt.minute:02d}", "AM" if dt.hour < 12 else "PM"
    return f"{dt.hour:02d}:{dt.minute:02d}", ""


def format_hm_text(dt, twelve=None):
    text, meridiem = format_hm(dt, twelve)
    return f"{text} {meridiem}" if meridiem else text


def format_offset(offset):
    """timedelta UTC offset -> 'UTC +5:30', 'UTC −4', 'UTC +0'."""
    minutes = int(round(offset.total_seconds() / 60))
    sign = "+" if minutes >= 0 else MINUS
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC {sign}{hours}" + (f":{mins:02d}" if mins else "")


def format_span(seconds, short=False):
    """Seconds -> '7 h 12 min', '45 min', '1 min'."""
    minutes = int(round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{max(mins, 1 if not short else 0)} min"


def format_signed_span(seconds):
    """Offset from local time, e.g. '−9 h 30 min', '+3 h 30 min', 'Same time'."""
    if abs(seconds) < 60:
        return "Same time"
    return f"{'+' if seconds > 0 else MINUS}{format_span(abs(seconds))}"


def availability(dt):
    """(kind, label, hint) for whether it's a good time to reach someone at local time `dt`."""
    hour = dt.hour + dt.minute / 60
    if hour >= 22 or hour < 7:
        return "sleep", "Sleeping", "Probably asleep"
    if dt.weekday() >= 5:
        return "free", "Weekend", "Weekend, may be free"
    if 9 <= hour < 18:
        return "work", "Working hours", "Good time for work calls"
    return "free", "Morning" if hour < 9 else "Evening", "Outside working hours"


def describe_difference(seconds):
    """Offset of another zone relative to local time, e.g. '9 h 30 min behind'."""
    if abs(seconds) < 60:
        return "Same time as here"
    return f"{format_span(abs(seconds))} {'ahead' if seconds > 0 else 'behind'}"


def format_countdown(seconds):
    """Whole seconds -> 'H:MM:SS' or 'MM:SS'."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_stopwatch(seconds):
    """Float seconds -> ('MM:SS' or 'H:MM:SS', '.cc')."""
    centis = int(seconds * 100)
    total, cc = divmod(centis, 100)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    main = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
    return main, f".{cc:02d}"


def day_relation(other, here):
    delta = (other.date() - here.date()).days
    return {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta, other.strftime("%a"))


# ---------------------------------------------------------------------------
# Sun position (NOAA solar calculator equations)

def _julian_day(dt_utc):
    return dt_utc.timestamp() / 86400.0 + 2440587.5


def solar_position(dt_utc):
    """Returns (declination in degrees, equation of time in minutes)."""
    t = (_julian_day(dt_utc) - 2451545.0) / 36525.0
    l0 = math.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360)
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * m) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    apparent_long = math.radians(math.degrees(l0) + c - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(eps) * math.sin(apparent_long))
    y = math.tan(eps / 2) ** 2
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0) - 1.25 * e * e * math.sin(2 * m))
    return math.degrees(declination), eq_time


def subsolar_point(dt_utc):
    declination, eq_time = solar_position(dt_utc)
    utc_minutes = dt_utc.hour * 60 + dt_utc.minute + dt_utc.second / 60
    longitude = (720 - utc_minutes - eq_time) / 4
    return declination, (longitude + 180) % 360 - 180


def sun_altitude(lat, lon, subsolar):
    s_lat, s_lon = map(math.radians, subsolar)
    lat, lon = math.radians(lat), math.radians(lon)
    return math.degrees(math.asin(
        math.sin(lat) * math.sin(s_lat) + math.cos(lat) * math.cos(s_lat) * math.cos(lon - s_lon)))


def sun_times(lat, lon, day, tz):
    """Sunrise and sunset on local `day` in `tz`. Returns (sunrise, sunset, state)."""
    noon_utc = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc) - timedelta(hours=lon / 15)
    declination, eq_time = solar_position(noon_utc)
    lat_r, dec_r = math.radians(lat), math.radians(declination)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(dec_r))
              - math.tan(lat_r) * math.tan(dec_r))
    if cos_ha > 1:
        return None, None, "polar-night"
    if cos_ha < -1:
        return None, None, "polar-day"
    hour_angle = math.degrees(math.acos(cos_ha))
    solar_noon = 720 - 4 * lon - eq_time
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    sunrise = base + timedelta(minutes=solar_noon - 4 * hour_angle)
    sunset = base + timedelta(minutes=solar_noon + 4 * hour_angle)
    return sunrise.astimezone(tz), sunset.astimezone(tz), "normal"


def moon_phase(dt_utc):
    """Fraction through the synodic month: 0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter."""
    days = _julian_day(dt_utc) - 2451550.1
    return (days / 29.530588853) % 1.0


def moon_phase_name(phase):
    names = [(0.03, "New moon"), (0.22, "Waxing crescent"), (0.28, "First quarter"), (0.47, "Waxing gibbous"),
             (0.53, "Full moon"), (0.72, "Waning gibbous"), (0.78, "Last quarter"), (0.97, "Waning crescent")]
    return next((name for limit, name in names if phase < limit), "New moon")


def moon_illumination(phase):
    return (1 - math.cos(math.tau * phase)) / 2


def is_daylight(lat, lon, dt_utc=None):
    dt_utc = dt_utc or datetime.now(timezone.utc)
    return sun_altitude(lat, lon, subsolar_point(dt_utc)) > -0.833
