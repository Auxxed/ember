"""Adwaita application entry."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk

from ..paths import APP_ID
from .bridge import Bridge
from .window import EmberWindow


def load_css() -> None:
    provider = Gtk.CssProvider()
    path = Path(__file__).with_name("style.css")
    provider.load_from_path(str(path))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class EmberApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.bridge: Bridge | None = None
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

    def _on_startup(self, *_args) -> None:
        Adw.init()
        load_css()

    def _on_activate(self, *_args) -> None:
        win = self.props.active_window
        if win is None:
            self.bridge = Bridge()
            win = EmberWindow(self, self.bridge)
        win.present()

    def _on_shutdown(self, *_args) -> None:
        if self.bridge:
            self.bridge.close()


def run() -> int:
    import sys

    return EmberApp().run([sys.argv[0]])
