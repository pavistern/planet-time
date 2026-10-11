"""Loops the alarm sound with GStreamer."""

import os

import gi

from gi.repository import Gio, GLib

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
except (ImportError, ValueError):
    Gst = None

SOUND_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/Yaru/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
]


class Ringer:
    def __init__(self):
        self._player = None
        sound = next((path for path in SOUND_CANDIDATES if os.path.exists(path)), None)
        if Gst is None or sound is None:
            return
        self._player = Gst.ElementFactory.make("playbin", "live-clock-ringer")
        if self._player is None:
            return
        self._player.set_property("uri", Gio.File.new_for_path(sound).get_uri())
        bus = self._player.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        self._playing = False

    def _on_eos(self, _bus, _message):
        if self._playing:
            # Short pause between rings, then go again.
            GLib.timeout_add(600, self._replay)

    def _replay(self):
        if self._playing:
            self._player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self._player.set_state(Gst.State.PLAYING)
        return GLib.SOURCE_REMOVE

    def start(self):
        if self._player is None:
            return
        self._playing = True
        self._player.set_state(Gst.State.NULL)
        self._player.set_state(Gst.State.PLAYING)

    def stop(self):
        if self._player is None:
            return
        self._playing = False
        self._player.set_state(Gst.State.NULL)
