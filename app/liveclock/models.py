"""App state: persistent store, the shared ticker, alarms, timers and the stopwatch."""

import json
import os
import time
import uuid
from datetime import datetime, timedelta

from gi.repository import GLib, GObject

from . import places

RING_GRACE_SECONDS = 5 * 60


class Store:
    """Everything that survives a restart, kept in one JSON file."""

    def __init__(self):
        self.path = os.path.join(GLib.get_user_config_dir(), "live-clock", "state.json")
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            pass
        if "cities" not in self.data:
            imported = places.import_gnome_clocks()
            known = {places.city_key(c) for c in imported}
            self.data["cities"] = imported + [c for c in places.DEFAULT_CITIES if places.city_key(c) not in known]
        self.data.setdefault("alarms", [])
        self.data.setdefault("timers", [])
        self.data.setdefault("home", None)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        GLib.file_set_contents(self.path, json.dumps(self.data, indent=2).encode("utf-8"))


class Ticker(GObject.Object):
    """Emits 'second' just after every wall-clock second."""

    __gsignals__ = {"second": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self._schedule()

    def _schedule(self):
        delay_ms = 1000 - int(time.time() * 1000) % 1000 + 3
        GLib.timeout_add(delay_ms, self._on_timeout)

    def _on_timeout(self):
        self.emit("second")
        self._schedule()
        return GLib.SOURCE_REMOVE


class Settings(GObject.Object):
    """User preferences, stored with the rest of the state. Emits 'changed' with the key name."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    DEFAULTS = {"hero_background": "sky", "home_weather": True, "city_weather": True}

    def __init__(self, store):
        super().__init__()
        self.store = store
        store.data.setdefault("settings", {})

    def get(self, key):
        return self.store.data["settings"].get(key, self.DEFAULTS[key])

    def set(self, key, value):
        if self.get(key) != value:
            self.store.data["settings"][key] = value
            self.store.save()
            self.emit("changed", key)


class Cities(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, store):
        super().__init__()
        self.store = store
        if not store.data.get("home"):
            store.data["home"] = places.default_home()
            store.save()  # first run: keep the detected home and imported cities

    @property
    def home(self):
        return self.store.data["home"]

    @property
    def items(self):
        return self.store.data["cities"]

    def set_home(self, city):
        self.store.data["home"] = dict(city)
        self.store.save()
        self.emit("changed")

    def add(self, city):
        if all(places.city_key(c) != places.city_key(city) for c in self.items):
            self.items.append(dict(city))
            self.store.save()
            self.emit("changed")

    def remove(self, city):
        self.store.data["cities"] = [c for c in self.items if places.city_key(c) != places.city_key(city)]
        self.store.save()
        self.emit("changed")


def next_occurrence(hour, minute, days, after):
    """Next local datetime at hour:minute on one of `days` (0 = Monday; empty = any day)."""
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    if days:
        for _ in range(7):
            if candidate.weekday() in days:
                break
            candidate += timedelta(days=1)
    return candidate


class Alarms(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ring": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, store):
        super().__init__()
        self.store = store
        now = datetime.now()
        for alarm in self.items:
            if alarm["enabled"] and not alarm.get("next_ring"):
                self._reschedule(alarm, now)

    @property
    def items(self):
        return self.store.data["alarms"]

    def sorted(self):
        return sorted(self.items, key=lambda a: (a["hour"], a["minute"]))

    def get(self, alarm_id):
        return next((a for a in self.items if a["id"] == alarm_id), None)

    def _reschedule(self, alarm, now):
        alarm["snoozed_until"] = None
        if alarm["enabled"]:
            alarm["next_ring"] = next_occurrence(alarm["hour"], alarm["minute"], alarm["days"], now).timestamp()
        else:
            alarm["next_ring"] = None

    def _commit(self):
        self.store.save()
        self.emit("changed")

    def add(self, hour, minute, days, name, snooze_minutes):
        alarm = {"id": uuid.uuid4().hex, "hour": hour, "minute": minute, "days": sorted(days),
                 "name": name, "snooze_minutes": snooze_minutes, "enabled": True}
        self._reschedule(alarm, datetime.now())
        self.items.append(alarm)
        self._commit()
        return alarm

    def update(self, alarm_id, **fields):
        alarm = self.get(alarm_id)
        if alarm:
            alarm.update(fields)
            if "days" in fields:
                alarm["days"] = sorted(alarm["days"])
            self._reschedule(alarm, datetime.now())
            self._commit()
        return alarm

    def set_enabled(self, alarm_id, enabled):
        alarm = self.get(alarm_id)
        if alarm and alarm["enabled"] != enabled:
            alarm["enabled"] = enabled
            self._reschedule(alarm, datetime.now())
            self._commit()

    def remove(self, alarm_id):
        self.store.data["alarms"] = [a for a in self.items if a["id"] != alarm_id]
        self._commit()

    def snooze(self, alarm_id):
        alarm = self.get(alarm_id)
        if alarm:
            alarm["enabled"] = True
            alarm["snoozed_until"] = time.time() + 60 * alarm.get("snooze_minutes", 10)
            self._commit()

    def due_time(self, alarm):
        return alarm.get("snoozed_until") or alarm.get("next_ring")

    def any_enabled(self):
        return any(a["enabled"] for a in self.items)

    def next_alarm(self):
        upcoming = [(self.due_time(a), a) for a in self.items if a["enabled"] and self.due_time(a)]
        return min(upcoming, key=lambda item: item[0], default=(None, None))

    def check(self):
        now = time.time()
        changed = False
        for alarm in list(self.items):
            due = self.due_time(alarm)
            if not alarm["enabled"] or not due or now < due:
                continue
            missed = now - due > RING_GRACE_SECONDS
            if not alarm["days"] and not alarm.get("snoozed_until"):
                alarm["enabled"] = False
            self._reschedule(alarm, datetime.now())
            changed = True
            if not missed:
                self.emit("ring", alarm["id"])
        if changed:
            self._commit()


class Timers(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, store):
        super().__init__()
        self.store = store
        self._poll_id = 0
        # Timers that ran out long ago while the app was closed should not ring now.
        for timer in self.items:
            if timer["state"] == "running" and time.time() - timer["end"] > RING_GRACE_SECONDS:
                timer["state"] = "done"
                timer["remaining"] = 0
        self._ensure_polling()

    @property
    def items(self):
        return self.store.data["timers"]

    def get(self, timer_id):
        return next((t for t in self.items if t["id"] == timer_id), None)

    def remaining(self, timer):
        if timer["state"] == "running":
            return max(0.0, timer["end"] - time.time())
        if timer["state"] == "done":
            return 0.0
        return timer["remaining"]

    def any_running(self):
        return any(t["state"] == "running" for t in self.items)

    def _commit(self):
        self.store.save()
        self._ensure_polling()
        self.emit("changed")

    def add(self, duration, name=""):
        timer = {"id": uuid.uuid4().hex, "name": name, "duration": duration, "remaining": duration,
                 "state": "running", "end": time.time() + duration}
        self.items.append(timer)
        self._commit()
        return timer

    def pause(self, timer_id):
        timer = self.get(timer_id)
        if timer and timer["state"] == "running":
            timer["remaining"] = self.remaining(timer)
            timer["state"] = "paused"
            self._commit()

    def resume(self, timer_id):
        timer = self.get(timer_id)
        if timer and timer["state"] in ("paused", "ready"):
            timer["end"] = time.time() + timer["remaining"]
            timer["state"] = "running"
            self._commit()

    def reset(self, timer_id):
        timer = self.get(timer_id)
        if timer:
            timer["remaining"] = timer["duration"]
            timer["state"] = "ready"
            self._commit()

    def restart(self, timer_id):
        self.reset(timer_id)
        self.resume(timer_id)

    def remove(self, timer_id):
        self.store.data["timers"] = [t for t in self.items if t["id"] != timer_id]
        self._commit()

    def _ensure_polling(self):
        if self.any_running() and not self._poll_id:
            self._poll_id = GLib.timeout_add(200, self._poll)

    def _poll(self):
        finished = []
        for timer in self.items:
            if timer["state"] == "running" and time.time() >= timer["end"]:
                timer["state"] = "done"
                timer["remaining"] = 0
                finished.append(timer["id"])
        if finished:
            self.store.save()
            self.emit("changed")
            for timer_id in finished:
                self.emit("finished", timer_id)
        if not self.any_running():
            self._poll_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE


class Stopwatch(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.state = "stopped"  # stopped | running | paused
        self._started = 0.0
        self._accumulated = 0.0
        self.laps = []  # (lap duration, total at lap)

    def elapsed(self):
        if self.state == "running":
            return self._accumulated + time.monotonic() - self._started
        return self._accumulated

    def start(self):
        if self.state != "running":
            self._started = time.monotonic()
            self.state = "running"
            self.emit("changed")

    def pause(self):
        if self.state == "running":
            self._accumulated = self.elapsed()
            self.state = "paused"
            self.emit("changed")

    def reset(self):
        self.state = "stopped"
        self._accumulated = 0.0
        self.laps = []
        self.emit("changed")

    def lap(self):
        if self.state == "running":
            total = self.elapsed()
            previous = self.laps[-1][1] if self.laps else 0.0
            self.laps.append((total - previous, total))
            self.emit("changed")
