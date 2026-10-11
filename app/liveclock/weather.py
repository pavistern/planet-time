"""Current weather for world-clock cities from Open-Meteo (no account; only city coordinates are sent)."""

import json
import locale
import os
import time

import gi

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, GObject, Soup  # noqa: E402

from . import VERSION, places  # noqa: E402

API = "https://api.open-meteo.com/v1/forecast"
REFRESH_SECONDS = 30 * 60
RETRY_SECONDS = 5 * 60
MAX_AGE_SECONDS = 3 * 3600

# WMO weather interpretation codes -> (description, day icon, night icon)
CONDITIONS = {
    0: ("Clear", "weather-clear", "weather-clear-night"),
    1: ("Mostly clear", "weather-few-clouds", "weather-few-clouds-night"),
    2: ("Partly cloudy", "weather-few-clouds", "weather-few-clouds-night"),
    3: ("Overcast", "weather-overcast", "weather-overcast"),
    45: ("Fog", "weather-fog", "weather-fog"),
    48: ("Freezing fog", "weather-fog", "weather-fog"),
    51: ("Light drizzle", "weather-showers-scattered", "weather-showers-scattered"),
    53: ("Drizzle", "weather-showers-scattered", "weather-showers-scattered"),
    55: ("Heavy drizzle", "weather-showers", "weather-showers"),
    56: ("Freezing drizzle", "weather-showers-scattered", "weather-showers-scattered"),
    57: ("Freezing drizzle", "weather-showers", "weather-showers"),
    61: ("Light rain", "weather-showers-scattered", "weather-showers-scattered"),
    63: ("Rain", "weather-showers", "weather-showers"),
    65: ("Heavy rain", "weather-showers", "weather-showers"),
    66: ("Freezing rain", "weather-showers", "weather-showers"),
    67: ("Freezing rain", "weather-showers", "weather-showers"),
    71: ("Light snow", "weather-snow", "weather-snow"),
    73: ("Snow", "weather-snow", "weather-snow"),
    75: ("Heavy snow", "weather-snow", "weather-snow"),
    77: ("Snow grains", "weather-snow", "weather-snow"),
    80: ("Showers", "weather-showers-scattered", "weather-showers-scattered"),
    81: ("Showers", "weather-showers", "weather-showers"),
    82: ("Heavy showers", "weather-showers", "weather-showers"),
    85: ("Snow showers", "weather-snow", "weather-snow"),
    86: ("Snow showers", "weather-snow", "weather-snow"),
    95: ("Thunderstorm", "weather-storm", "weather-storm"),
    96: ("Thunderstorm", "weather-storm", "weather-storm"),
    99: ("Thunderstorm", "weather-storm", "weather-storm"),
}


def describe(report):
    """(description, symbolic icon name) for a weather report."""
    text, day_icon, night_icon = CONDITIONS.get(report["code"], ("", "weather-overcast", "weather-overcast"))
    return text, f"{day_icon if report.get('is_day', 1) else night_icon}-symbolic"


def use_fahrenheit():
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup("org.gnome.GWeather4", True) is not None:
        unit = Gio.Settings.new("org.gnome.GWeather4").get_string("temperature-unit")
        if unit in ("fahrenheit", "centigrade"):
            return unit == "fahrenheit"
    territory = (locale.getlocale(locale.LC_MEASUREMENT)[0] if hasattr(locale, "LC_MEASUREMENT") else None) \
        or os.environ.get("LC_MEASUREMENT") or os.environ.get("LANG", "")
    return any(code in territory for code in ("_US", "_LR", "_MM"))


class Weather(GObject.Object):
    """Fetches all cities in one request, refreshes every 30 minutes while a window is open."""

    __gsignals__ = {"updated": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, cities, settings):
        super().__init__()
        self.cities = cities
        self.settings = settings
        self.reports = {}  # city key (as JSON string) -> report dict
        self.fahrenheit = use_fahrenheit()
        self._cache_path = os.path.join(GLib.get_user_cache_dir(), "live-clock", "weather.json")
        self._session = Soup.Session(user_agent=f"PlanetTime/{VERSION} (personal desktop clock)", timeout=20)
        self._timer = 0
        self._users = 0
        self._busy = False
        self._load_cache()
        cities.connect("changed", lambda *_: self._schedule(3))
        settings.connect("changed", self._on_setting_changed)

    def _on_setting_changed(self, _settings, key):
        if key in ("home_weather", "city_weather"):
            self.emit("updated")
            self._schedule(2)

    def enabled_for(self, city):
        is_home = places.city_key(city) == places.city_key(self.cities.home)
        return self.settings.get("home_weather" if is_home else "city_weather")

    def locations(self):
        """Everything weather is allowed for: added cities and/or the home city, per the settings."""
        wanted = list(self.cities.items) if self.settings.get("city_weather") else []
        if self.settings.get("home_weather"):
            wanted.append(self.cities.home)
        unique = {}
        for city in wanted:
            unique.setdefault(self.key(city), city)
        return list(unique.values())

    @staticmethod
    def key(city):
        return json.dumps(places.city_key(city))

    def report_for(self, city):
        if not self.enabled_for(city):
            return None
        report = self.reports.get(self.key(city))
        if report and time.time() - report["fetched"] < MAX_AGE_SECONDS:
            return report
        return None

    def format_temp(self, value):
        return f"{round(value)}°"

    # -- lifetime: only fetch while something shows weather -----------------

    def acquire(self):
        self._users += 1
        if self._users == 1:
            stale = any(self.report_for(c) is None for c in self.locations())
            self._schedule(1 if stale else REFRESH_SECONDS - self._age())

    def release(self):
        self._users = max(0, self._users - 1)
        if not self._users and self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _age(self):
        fetched = [r["fetched"] for r in self.reports.values()]
        return time.time() - min(fetched) if fetched else REFRESH_SECONDS

    def _schedule(self, seconds):
        if not self._users:
            return
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(max(1, int(seconds)), self._on_timer)

    def _on_timer(self):
        self._timer = 0
        self._fetch()
        return GLib.SOURCE_REMOVE

    # -- network -------------------------------------------------------------

    def _fetch(self):
        cities = self.locations()
        if not cities or self._busy:
            self._schedule(REFRESH_SECONDS)
            return
        params = {
            "latitude": ",".join(f"{c['lat']:.3f}" for c in cities),
            "longitude": ",".join(f"{c['lon']:.3f}" for c in cities),
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": "1",
        }
        if self.fahrenheit:
            params["temperature_unit"] = "fahrenheit"
        query = "&".join(f"{k}={GLib.Uri.escape_string(v, ',', False)}" for k, v in params.items())
        message = Soup.Message.new("GET", f"{API}?{query}")
        self._busy = True
        self._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, None, self._on_response,
                                          (message, cities))

    def _on_response(self, session, result, user_data):
        message, cities = user_data
        self._busy = False
        try:
            body = session.send_and_read_finish(result)
            if message.get_status() != Soup.Status.OK:
                raise ValueError(f"HTTP {message.get_status()}")
            data = json.loads(body.get_data().decode("utf-8"))
            results = data if isinstance(data, list) else [data]
            now = time.time()
            for city, entry in zip(cities, results):
                current, daily = entry["current"], entry.get("daily", {})
                self.reports[self.key(city)] = {
                    "temp": current["temperature_2m"],
                    "code": int(current["weather_code"]),
                    "is_day": int(current.get("is_day", 1)),
                    "high": (daily.get("temperature_2m_max") or [None])[0],
                    "low": (daily.get("temperature_2m_min") or [None])[0],
                    "fahrenheit": self.fahrenheit,
                    "fetched": now,
                }
            self._save_cache()
            self.emit("updated")
            self._schedule(REFRESH_SECONDS)
        except (GLib.Error, ValueError, KeyError, TypeError, IndexError) as error:
            print(f"Planet Time: weather update failed: {error}", flush=True)
            self._schedule(RETRY_SECONDS)

    # -- cache ---------------------------------------------------------------

    def _load_cache(self):
        try:
            with open(self._cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            self.reports = {k: v for k, v in cached.items() if v.get("fahrenheit") == self.fahrenheit}
        except (OSError, ValueError, AttributeError):
            self.reports = {}

    def _save_cache(self):
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        GLib.file_set_contents(self._cache_path, json.dumps(self.reports).encode("utf-8"))
