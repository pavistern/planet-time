"""Main window: header with pill navigation, page stack and toasts."""

from gi.repository import Adw, Gio, Gtk

from . import APP_ID, APP_NAME
from .alarms_page import AlarmsPage
from .dashboard import DashboardPage
from .dialogs import RingingDialog
from .stopwatch_page import StopwatchPage
from .timer_page import TimerPage

PAGES = [
    ("dashboard", "Dashboard", "globe-symbolic", DashboardPage),
    ("alarms", "Alarms", "alarm-symbolic", AlarmsPage),
    ("stopwatch", "Stopwatch", "stopwatch-symbolic", StopwatchPage),
    ("timer", "Timer", "timer-symbolic", TimerPage),
]


class NavBar(Gtk.Box):
    """Round icon buttons; the active one grows into a dark pill showing its name."""

    def __init__(self, on_select):
        super().__init__(spacing=8, css_classes=["nav-bar"], valign=Gtk.Align.CENTER)
        self.buttons = {}
        group = None
        for name, title, icon, _page in PAGES:
            btn = Gtk.ToggleButton(css_classes=["nav-item"], tooltip_text=title)
            content = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            content.append(Gtk.Image.new_from_icon_name(icon))
            revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
                                    transition_duration=220)
            revealer.set_child(Gtk.Label(label=title, css_classes=["nav-label"]))
            content.append(revealer)
            btn.set_child(content)
            btn.revealer = revealer
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_toggled, name, on_select)
            self.buttons[name] = btn
            self.append(btn)

    icons_only = False

    def _on_toggled(self, btn, name, on_select):
        btn.revealer.set_reveal_child(btn.get_active() and not self.icons_only)
        if btn.get_active():
            on_select(name)

    def set_icons_only(self, icons_only):
        self.icons_only = icons_only
        for btn in self.buttons.values():
            btn.revealer.set_reveal_child(btn.get_active() and not icons_only)

    def select(self, name):
        if name in self.buttons:
            self.buttons[name].set_active(True)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1240, default_height=820)
        self.set_size_request(380, 560)
        self.app = app
        self.add_css_class("liveclock")
        self.ringing_dialog = None

        self.style_manager = Adw.StyleManager.get_default()
        self._dark_handler = self.style_manager.connect("notify::dark", lambda *_: self._sync_dark())
        self._sync_dark()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180,
                               hhomogeneous=False, vhomogeneous=False)
        self.stack.connect("notify::visible-child", lambda *_: self.set_focus(None))
        self.pages = {}
        for name, title, _icon, page_class in PAGES:
            page = page_class(app, self)
            self.pages[name] = page
            self.stack.add_titled(page, name, title)

        self.nav = NavBar(self.stack.set_visible_child_name)

        header = Adw.HeaderBar(css_classes=["lc-header"])
        header.set_title_widget(self.nav)
        self.brand = Gtk.Box(spacing=10, css_classes=["brand"], valign=Gtk.Align.CENTER)
        self.brand.append(Gtk.Image(icon_name=APP_ID, pixel_size=30))
        self.brand_label = Gtk.Label(label=APP_NAME, css_classes=["brand-label"])
        self.brand.append(self.brand_label)
        header.pack_start(self.brand)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append(f"About {APP_NAME}", "app.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu",
                                       css_classes=["chip-button"], valign=Gtk.Align.CENTER))

        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.toasts)
        self.set_content(view)

        # Only one breakpoint is active at a time; whichever unapplies falls back to "wide".
        for condition, mode in (("max-width: 1060sp", "compact"), ("max-width: 720sp", "narrow")):
            breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(condition))
            breakpoint.connect("apply", lambda _b, m=mode: self._set_layout(m))
            breakpoint.connect("unapply", lambda *_: self._set_layout("wide"))
            self.add_breakpoint(breakpoint)
        self._set_layout("wide")

        self.nav.select("dashboard")
        self.connect("close-request", self._on_close_request)

    def _sync_dark(self):
        if self.style_manager.get_dark():
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")

    def _set_layout(self, mode):
        narrow = mode == "narrow"
        self.pages["dashboard"].set_layout(mode)
        self.pages["timer"].set_layout(mode)
        self.brand.set_visible(not narrow)
        self.brand_label.set_visible(mode == "wide")
        self.nav.set_icons_only(narrow)
        (self.add_css_class if narrow else self.remove_css_class)("narrow")

    def show_page(self, name):
        self.nav.select(name)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def show_ringing(self, info):
        self.close_ringing()
        if info["kind"] == "alarm":
            secondary = lambda: self.app.snooze(info["id"])  # noqa: E731
        else:
            secondary = lambda: self.app.restart_timer(info["id"])  # noqa: E731
        self.ringing_dialog = RingingDialog(info, self.app.stop_ringing, secondary)
        self.ringing_dialog.present(self)

    def close_ringing(self):
        dialog, self.ringing_dialog = self.ringing_dialog, None
        if dialog is not None:
            dialog.close()

    def _on_close_request(self, *_):
        self.close_ringing()
        for page in self.pages.values():
            page.teardown()
        self.style_manager.disconnect(self._dark_handler)
        return False
