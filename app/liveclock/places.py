"""Cities: the local home city, the GWeather search index and GNOME Clocks import."""

import math
import os
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import gi

gi.require_version("GWeather", "4.0")
from gi.repository import Gio, GLib, GWeather  # noqa: E402

ZONE_TAB = "/usr/share/zoneinfo/zone1970.tab"

DEFAULT_CITIES = [
    {"name": "London", "country": "United Kingdom", "tz": "Europe/London", "lat": 51.507, "lon": -0.128},
    {"name": "Dubai", "country": "United Arab Emirates", "tz": "Asia/Dubai", "lat": 25.205, "lon": 55.271},
    {"name": "Tokyo", "country": "Japan", "tz": "Asia/Tokyo", "lat": 35.690, "lon": 139.692},
]

CODE_OVERRIDES = {
    "New York": "NYC", "Los Angeles": "LAX", "San Francisco": "SFO", "Hong Kong": "HKG",
    "Rio de Janeiro": "RIO", "Mexico City": "MEX", "Kuala Lumpur": "KUL", "Buenos Aires": "BUE",
    "Cape Town": "CPT", "Abu Dhabi": "AUH", "Tel Aviv": "TLV", "São Paulo": "SAO", "Las Vegas": "LAS",
    "New Delhi": "DEL", "Salt Lake City": "SLC", "Ho Chi Minh City": "SGN", "Kyiv": "KYV",
}


def zone_of(city):
    return ZoneInfo(city["tz"])


def now_in(city):
    return datetime.now(zone_of(city))


def city_key(city):
    return (city["name"], city.get("country", ""), city["tz"])


NAME_PREFIXES = {"new", "san", "santa", "santo", "sao", "saint", "st", "sainte", "los", "las", "el", "la", "le",
                 "port", "porto", "puerto", "fort", "mount", "north", "south", "east", "west", "al"}


def city_code(name):
    """Three-letter code for a city: 'London' -> LON, 'New Bern' -> NBE, 'San Diego' -> SDI."""
    if name in CODE_OVERRIDES:
        return CODE_OVERRIDES[name]
    words = ["".join(ch for ch in word if ch.isalpha()) for word in _fold(name).replace("-", " ").split()]
    words = [w for w in words if w]
    if len(words) >= 2 and words[0] in NAME_PREFIXES:
        return (words[0][0] + words[1][:2]).upper()
    return "".join(words)[:3].upper() or "?"


def unique_codes(names):
    """Codes for several cities, adjusted so no two chips show the same letters."""
    codes, used = [], set()
    for name in names:
        code = city_code(name)
        if code in used:
            letters = "".join(ch for ch in _fold(name) if ch.isalpha()).upper()
            candidates = (letters[0] + a + b for i, a in enumerate(letters[1:], 1) for b in letters[i + 1:])
            code = next((c for c in candidates if c not in used), code)
        used.add(code)
        codes.append(code)
    return codes


def _fold(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _parse_iso6709(coord):
    """'+2232+08822' -> (22.533, 88.367)."""
    split = max(coord.rfind("+"), coord.rfind("-"))
    lat_s, lon_s = coord[:split], coord[split:]

    def convert(text, degree_digits):
        sign = -1 if text[0] == "-" else 1
        digits = text[1:]
        deg = int(digits[:degree_digits])
        mins = int(digits[degree_digits:degree_digits + 2] or 0)
        secs = int(digits[degree_digits + 2:degree_digits + 4] or 0)
        return sign * (deg + mins / 60 + secs / 3600)

    return convert(lat_s, 2), convert(lon_s, 3)


def system_timezone():
    identifier = GLib.TimeZone.new_local().get_identifier()
    if identifier and "/" in identifier and not identifier.startswith(("/", ":")):
        return identifier
    try:
        target = os.path.realpath("/etc/localtime")
        return target.split("/zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def default_home():
    tz = system_timezone()
    name = tz.rsplit("/", 1)[-1].replace("_", " ")
    lat = lon = 0.0
    country = ""
    try:
        with open(ZONE_TAB, encoding="utf-8") as tab:
            for line in tab:
                if line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) >= 3 and fields[2] == tz:
                    lat, lon = _parse_iso6709(fields[1])
                    country_loc = GWeather.Location.get_world().find_by_country_code(fields[0].split(",")[0])
                    country = country_loc.get_name() if country_loc else ""
                    break
    except OSError:
        pass
    return {"name": name, "country": country, "tz": tz, "lat": lat, "lon": lon}


def import_gnome_clocks():
    """World clocks the user already set up in GNOME Clocks."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.clocks", True) is None:
        return []
    world = GWeather.Location.get_world()
    cities = []
    for entry in Gio.Settings.new("org.gnome.clocks").get_value("world-clocks").unpack():
        try:
            _version, (name, code, _has_coords, coords, _parent_coords) = entry["location"]
            station = world.find_by_station_code(code) if code else None
            tz = station.get_timezone_str() if station else None
            if not tz:
                continue
            lat, lon = (math.degrees(v) for v in coords[0]) if coords else station.get_coords()
            cities.append({"name": name, "country": station.get_country_name() or "", "tz": tz,
                           "lat": lat, "lon": lon})
        except (KeyError, TypeError, ValueError):
            continue
    return cities


class CityIndex:
    """Every city GWeather knows about, searchable by name or country."""

    def __init__(self):
        self._cities = None

    def _load(self):
        cities, seen = [], set()

        def walk(location):
            child = location.next_child(None)
            while child is not None:
                if child.get_level() == GWeather.LocationLevel.CITY and child.has_coords():
                    tz = child.get_timezone_str()
                    if tz:
                        lat, lon = child.get_coords()
                        city = {"name": child.get_name(), "country": child.get_country_name() or "",
                                "tz": tz, "lat": lat, "lon": lon}
                        if city_key(city) not in seen:
                            seen.add(city_key(city))
                            city["_name"] = _fold(city["name"])
                            city["_country"] = _fold(city["country"])
                            cities.append(city)
                walk(child)
                child = location.next_child(child)

        walk(GWeather.Location.get_world())
        cities.sort(key=lambda c: c["_name"])
        self._cities = cities

    def search(self, query, limit=50):
        if self._cities is None:
            self._load()
        query = _fold(query.strip())
        if not query:
            return []
        ranked = []
        for city in self._cities:
            name, country = city["_name"], city["_country"]
            if name.startswith(query):
                rank = 0
            elif any(word.startswith(query) for word in name.split()):
                rank = 1
            elif country.startswith(query):
                rank = 2
            elif query in name:
                rank = 3
            else:
                continue
            ranked.append((rank, city))
        ranked.sort(key=lambda item: item[0])
        return [{k: v for k, v in city.items() if not k.startswith("_")} for _, city in ranked[:limit]]
