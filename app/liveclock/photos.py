"""Home-city photos from Wikipedia, used only when the "City photo" background is chosen."""

import json
import os
import re
import time
import zlib
from urllib.parse import quote, unquote, urlsplit

import gi

gi.require_version("Soup", "3.0")
from gi.repository import GLib, GObject, Soup  # noqa: E402

from . import VERSION, places  # noqa: E402

SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
SEARCH = "https://en.wikipedia.org/w/rest.php/v1/search/title?q={}&limit=1"
COMMONS = ("https://commons.wikimedia.org/w/api.php?action=query&titles=File:{}"
           "&prop=imageinfo&iiprop=extmetadata&format=json")
WIDTH = 960
RETRY_AFTER = 3600


def _strip_tags(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


class CityPhotos(GObject.Object):
    __gsignals__ = {"ready": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self._dir = os.path.join(GLib.get_user_cache_dir(), "live-clock", "photos")
        self._session = Soup.Session(user_agent=f"PlanetTime/{VERSION} (personal desktop clock)", timeout=30)
        self._pending = set()
        self._failed = {}

    @staticmethod
    def _slug(city):
        return f"{zlib.crc32(json.dumps(places.city_key(city)).encode()):08x}"

    def lookup(self, city):
        """Cached photo for `city` as {'path', 'credit'}, or None."""
        slug = self._slug(city)
        try:
            with open(os.path.join(self._dir, f"{slug}.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return None
        path = os.path.join(self._dir, meta.get("image", ""))
        return {"path": path, "credit": meta.get("credit", "")} if os.path.isfile(path) else None

    def request(self, city):
        slug = self._slug(city)
        if self.lookup(city) or slug in self._pending or time.time() - self._failed.get(slug, 0) < RETRY_AFTER:
            return
        self._pending.add(slug)
        title = city["name"].replace(" ", "_")
        self._get(SUMMARY.format(quote(title, safe="")), lambda body: self._on_summary(city, body, searched=False))

    # -- steps ---------------------------------------------------------------

    def _on_summary(self, city, body, searched):
        data = self._json(body)
        if data and data.get("type") == "standard" and (data.get("originalimage") or data.get("thumbnail")):
            self._download(city, data)
        elif not searched:
            query = quote(f"{city['name']} {city.get('country', '')}".strip(), safe="")
            self._get(SEARCH.format(query), lambda b: self._on_search(city, b))
        else:
            self._fail(city)

    def _on_search(self, city, body):
        pages = (self._json(body) or {}).get("pages") or []
        if not pages:
            self._fail(city)
            return
        key = pages[0]["key"]
        self._get(SUMMARY.format(quote(key, safe="")), lambda b: self._on_summary(city, b, searched=True))

    def _download(self, city, data):
        original = data.get("originalimage") or {}
        thumb = data.get("thumbnail") or {}
        original_url = original.get("source", "").split("?")[0]
        thumb_url = thumb.get("source", "").split("?")[0]
        if original.get("width", 0) > WIDTH and re.search(r"/\d+px-", thumb_url):
            url = re.sub(r"/\d+px-", f"/{WIDTH}px-", thumb_url)
        else:
            url = original_url or thumb_url
        file_name = unquote(urlsplit(original_url or thumb_url).path.rsplit("/", 1)[-1])

        def save(image):
            if image is None and url != thumb_url and thumb_url:
                self._get(thumb_url, save_fallback)
                return
            save_fallback(image)

        def save_fallback(image):
            if not image:
                self._fail(city)
                return
            extension = os.path.splitext(file_name)[1].lower() or ".jpg"
            slug = self._slug(city)
            os.makedirs(self._dir, exist_ok=True)
            GLib.file_set_contents(os.path.join(self._dir, slug + extension), image)
            self._get(COMMONS.format(quote(file_name, safe="")),
                      lambda b: self._on_credit(city, slug + extension, data.get("title", ""), b))

        self._get(url, save)

    def _on_credit(self, city, image_name, title, body):
        credit = "Photo: Wikipedia"
        try:
            page = next(iter(self._json(body)["query"]["pages"].values()))
            meta = page["imageinfo"][0]["extmetadata"]
            artist = _strip_tags(meta.get("Artist", {}).get("value"))[:40]
            license_name = _strip_tags(meta.get("LicenseShortName", {}).get("value"))
            credit = "Photo: " + " · ".join(p for p in (artist, license_name, "Wikimedia Commons") if p)
        except (TypeError, KeyError, IndexError, StopIteration):
            pass
        slug = self._slug(city)
        meta = {"image": image_name, "credit": credit, "title": title, "fetched": time.time()}
        GLib.file_set_contents(os.path.join(self._dir, f"{slug}.json"), json.dumps(meta).encode("utf-8"))
        self._pending.discard(slug)
        self.emit("ready")

    def _fail(self, city):
        slug = self._slug(city)
        self._pending.discard(slug)
        self._failed[slug] = time.time()
        print(f"Planet Time: no photo found for {city['name']}", flush=True)

    # -- network helpers -----------------------------------------------------

    @staticmethod
    def _json(body):
        try:
            return json.loads(body.decode("utf-8")) if body else None
        except ValueError:
            return None

    def _get(self, url, on_done):
        message = Soup.Message.new("GET", url)
        if message is None:
            on_done(None)
            return
        self._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, None, self._on_read, (message, on_done))

    def _on_read(self, session, result, user_data):
        message, on_done = user_data
        try:
            body = session.send_and_read_finish(result)
        except GLib.Error:
            on_done(None)
            return
        on_done(body.get_data() if message.get_status() == Soup.Status.OK else None)
