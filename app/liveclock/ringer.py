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
    "sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "sounds/Yaru/stereo/alarm-clock-elapsed.oga",
    "sounds/freedesktop/stereo/complete.oga",
]


def find_sound():
    # Searches the XDG data dirs rather than /usr/share so a snap finds the sound it bundles.
    for name in SOUND_CANDIDATES:
        for data_dir in GLib.get_system_data_dirs():
            path = os.path.join(data_dir, name)
            if os.path.exists(path):
                return path
    return None


class Ringer:
    def __init__(self):
        self._player = None
        sound = find_sound()
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
