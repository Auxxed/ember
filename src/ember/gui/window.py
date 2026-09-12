"""Main Ember window."""

from __future__ import annotations

from typing import Any, Optional

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..paths import load_config, save_config
from .bridge import Bridge

PRESETS = [
    ("#4ea1ff", "Low"),
    ("#3dd68c", "Med"),
    ("#ff6a1a", "High"),
    ("#f4ece4", "Peak"),
]


def _hex_to_rgba(value: str) -> Gdk.RGBA:
    value = (value or "#ff6a1a").lstrip("#")
    if len(value) != 6:
        value = "ff6a1a"
    rgba = Gdk.RGBA()
    rgba.parse(f"#{value}")
    return rgba


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(rgba.red * 255))),
        max(0, min(255, int(rgba.green * 255))),
        max(0, min(255, int(rgba.blue * 255))),
    )


def _units() -> str:
    return (load_config().get("units") or "F").upper()


class EmberWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, bridge: Bridge):
        super().__init__(application=app, title="Ember")
        self.bridge = bridge
        self.set_default_size(400, 740)
        self.add_css_class("ember-window")
        self._busy = False
        self._syncing = False
        self._debounces: dict[str, int] = {}
        self._devices: list[dict] = []
        self._editor_key: tuple | None = None
        self._build()
        bridge.on_status = self.apply_status
        bridge.on_error = self._toast
        bridge.on_notify = self._notify
        bridge.on_ready = self._on_ready
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(root)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Ember"))
        self.battery_label = Gtk.Label(label="")
        self.battery_label.add_css_class("ember-battery")
        header.pack_end(self.battery_label)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(self._menu())
        header.pack_end(menu_btn)
        root.append(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._setup_page(), "setup")
        self.stack.add_named(self._device_page(), "device")
        root.append(self.stack)

        self._install_actions()

    def _menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        units = Gio.Menu()
        units.append("Fahrenheit", "win.units-f")
        units.append("Celsius", "win.units-c")
        menu.append_submenu("Units", units)
        menu.append("Device info", "win.info")
        menu.append("Usage", "win.usage")
        menu.append("Flash battery on device", "win.show-battery")
        danger = Gio.Menu()
        danger.append("Factory reset…", "win.factory-reset")
        menu.append_section(None, danger)
        menu.append("Quit", "win.quit")
        return menu

    def _install_actions(self) -> None:
        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        add("units-f", lambda *_: self._set_units("F"))
        add("units-c", lambda *_: self._set_units("C"))
        add("info", lambda *_: self._show_info())
        add("usage", lambda *_: self._show_usage())
        add("show-battery", lambda *_: self.bridge.call("show_battery"))
        add("factory-reset", lambda *_: self._confirm_reset())
        add("quit", lambda *_: self.close())

    def _setup_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.add_css_class("ember-body")
        page.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="Peak Pro")
        title.add_css_class("ember-temp")
        title.set_wrap(True)
        page.append(title)

        hint = Gtk.Label(
            label="Wake the device, keep it next to the PC,\nand disconnect the phone app."
        )
        hint.add_css_class("ember-sub")
        hint.set_justify(Gtk.Justification.CENTER)
        page.append(hint)

        self.last_btn = Gtk.Button(label="Connect")
        self.last_btn.add_css_class("ember-heat")
        self.last_btn.connect("clicked", self._connect_saved)
        page.append(self.last_btn)

        scan_row = Gtk.Box(spacing=8)
        self.scan_btn = Gtk.Button(label="Scan")
        self.scan_btn.add_css_class("ember-boost")
        self.scan_btn.set_hexpand(True)
        self.scan_btn.connect("clicked", self._scan)
        scan_row.append(self.scan_btn)
        page.append(scan_row)

        self.device_list = Gtk.ListBox()
        self.device_list.add_css_class("ember-card")
        self.device_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.device_list.connect("row-activated", self._row_activated)
        page.append(self.device_list)

        self.setup_status = Gtk.Label(label="")
        self.setup_status.add_css_class("ember-sub")
        page.append(self.setup_status)
        self._refresh_saved_button()
        return page

    def _device_page(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.add_css_class("ember-body")
        scroller.set_child(page)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.add_css_class("ember-hero")
        hero.set_halign(Gtk.Align.CENTER)
        self.hero = hero
        self.temp_label = Gtk.Label(label="—")
        self.temp_label.add_css_class("ember-temp")
        hero.append(self.temp_label)
        self.state_label = Gtk.Label(label="DISCONNECTED")
        self.state_label.add_css_class("ember-state")
        hero.append(self.state_label)
        self.meta_label = Gtk.Label(label="")
        self.meta_label.add_css_class("ember-sub")
        hero.append(self.meta_label)
        page.append(hero)

        actions = Gtk.Box(spacing=8)
        self.heat_btn = Gtk.Button(label="HEAT")
        self.heat_btn.add_css_class("ember-heat")
        self.heat_btn.set_hexpand(True)
        self.heat_btn.connect("clicked", lambda *_: self._heat_toggle())
        self.boost_btn = Gtk.Button(label="BOOST")
        self.boost_btn.add_css_class("ember-boost")
        self.boost_btn.connect("clicked", lambda *_: self.bridge.call("boost_heat"))
        self.stop_btn = Gtk.Button(label="STOP")
        self.stop_btn.add_css_class("ember-stop")
        self.stop_btn.connect("clicked", lambda *_: self.bridge.call("stop_heat"))
        actions.append(self.heat_btn)
        actions.append(self.boost_btn)
        actions.append(self.stop_btn)
        page.append(actions)

        page.append(self._section("PROFILES"))
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        self.profile_btns: list[Gtk.Button] = []
        self.profile_names: list[Gtk.Label] = []
        self.profile_metas: list[Gtk.Label] = []
        for i in range(4):
            btn = self._profile_button(i)
            self.profile_btns.append(btn)
            grid.attach(btn, i % 2, i // 2, 1, 1)
        page.append(grid)

        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.editor.add_css_class("ember-card")
        self.name_entry = Gtk.Entry()
        self.name_entry.add_css_class("ember-entry")
        self.name_entry.set_placeholder_text("Profile name")
        self.name_entry.connect("activate", self._apply_name)
        self.editor.append(self.name_entry)

        self.temp_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 450, 620, 1)
        self.temp_scale.add_css_class("ember-scale")
        self.temp_scale.set_draw_value(True)
        self.temp_scale.connect("value-changed", self._temp_changed)
        self.editor.append(self.temp_scale)

        self.time_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 15, 120, 1)
        self.time_scale.add_css_class("ember-scale")
        self.time_scale.set_draw_value(True)
        self.time_scale.connect("value-changed", self._time_changed)
        self.editor.append(self.time_scale)

        color_row = Gtk.Box(spacing=8)
        color_row.set_halign(Gtk.Align.START)
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        self.color_btn.connect("notify::rgba", self._color_changed)
        color_row.append(self.color_btn)
        for hex_color, _label in PRESETS:
            swatch = Gtk.Button()
            swatch.set_tooltip_text(_label)
            swatch.add_css_class("ember-dot")
            self._paint(swatch, hex_color)
            swatch.connect("clicked", lambda _b, c=hex_color: self._set_color(c))
            color_row.append(swatch)
        self.editor.append(color_row)
        page.append(self.editor)

        page.append(self._section("LIGHTS"))
        lights = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lights.add_css_class("ember-card")
        lantern_row = Gtk.Box(spacing=8)
        lantern_row.append(Gtk.Label(label="Lantern", xalign=0, hexpand=True))
        self.lantern_switch = Gtk.Switch()
        self.lantern_switch.set_valign(Gtk.Align.CENTER)
        self.lantern_switch.connect("state-set", self._lantern_toggled)
        lantern_row.append(self.lantern_switch)
        lights.append(lantern_row)
        self.bright_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
        self.bright_scale.add_css_class("ember-scale")
        self.bright_scale.set_value(80)
        self.bright_scale.connect("value-changed", self._brightness_changed)
        lights.append(self.bright_scale)
        anims = Gtk.Box(spacing=6)
        for name in ("solid", "breathing", "rising", "circling"):
            btn = Gtk.Button(label=name.title())
            btn.add_css_class("ember-boost")
            btn.connect("clicked", lambda _b, n=name: self._anim(n))
            anims.append(btn)
        lights.append(anims)
        page.append(lights)

        page.append(self._section("DEVICE"))
        device = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        device.add_css_class("ember-card")
        stealth_row = Gtk.Box(spacing=8)
        stealth_row.append(Gtk.Label(label="Stealth", xalign=0, hexpand=True))
        self.stealth_switch = Gtk.Switch()
        self.stealth_switch.set_valign(Gtk.Align.CENTER)
        self.stealth_switch.connect("state-set", self._stealth_toggled)
        stealth_row.append(self.stealth_switch)
        device.append(stealth_row)
        power = Gtk.Box(spacing=8)
        sleep_btn = Gtk.Button(label="Sleep")
        sleep_btn.add_css_class("ember-boost")
        sleep_btn.set_hexpand(True)
        sleep_btn.connect("clicked", lambda *_: self.bridge.call("sleep"))
        off_btn = Gtk.Button(label="Power off")
        off_btn.add_css_class("ember-stop")
        off_btn.set_hexpand(True)
        off_btn.connect("clicked", lambda *_: self.bridge.call("power_off"))
        disc_btn = Gtk.Button(label="Disconnect")
        disc_btn.add_css_class("ember-stop")
        disc_btn.set_hexpand(True)
        disc_btn.connect("clicked", lambda *_: self.bridge.call("disconnect"))
        power.append(sleep_btn)
        power.append(off_btn)
        power.append(disc_btn)
        device.append(power)
        page.append(device)
        return scroller

    def _profile_button(self, index: int) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("profile")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_halign(Gtk.Align.START)
        name = Gtk.Label(label=PRESETS[index][1], xalign=0)
        name.add_css_class("profile-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        meta = Gtk.Label(label="—", xalign=0)
        meta.add_css_class("profile-meta")
        box.append(name)
        box.append(meta)
        btn.set_child(box)
        btn.connect("clicked", lambda *_: self._select_profile(index))
        self.profile_names.append(name)
        self.profile_metas.append(meta)
        return btn

    def _section(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("ember-section")
        return label

    def _paint(self, widget: Gtk.Widget, color: str) -> None:
        css = Gtk.CssProvider()
        css.load_from_data(f"* {{ background: {color}; min-width: 18px; min-height: 18px; border-radius: 99px; }}".encode())
        widget.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_saved_button(self) -> None:
        cfg = load_config()
        mac = cfg.get("device_mac") or ""
        name = cfg.get("device_name") or "Peak Pro"
        if mac:
            self.last_btn.set_label(f"Connect {name}")
            self.last_btn.set_sensitive(True)
        else:
            self.last_btn.set_label("Connect")
            self.last_btn.set_sensitive(True)

    def _on_ready(self) -> None:
        status = self.bridge.status
        if status.get("connected"):
            self.apply_status(status)
            return
        cfg = load_config()
        if cfg.get("auto_connect") and (cfg.get("device_mac") or cfg.get("device_name")):
            self.setup_status.set_text("Connecting…")
            self.bridge.call("connect", timeout=90, on_err=self._toast)
        else:
            self.stack.set_visible_child_name("setup")

    def apply_status(self, status: dict[str, Any]) -> None:
        self._syncing = True
        try:
            connected = bool(status.get("connected"))
            self.stack.set_visible_child_name("device" if connected else "setup")
            battery = status.get("battery") or 0
            charge = status.get("charge_state") or ""
            self.battery_label.set_text("" if not connected else f"{battery}%")
            self.battery_label.set_tooltip_text(charge)
            if not connected:
                self._refresh_saved_button()
                return
            units = _units()
            state_id = int(status.get("operating_state_id") or -1)
            live_f = status.get("heater_temp_f")
            live_c = status.get("heater_temp_c")
            current = int(status.get("current_profile") or 0)
            profiles = status.get("profiles") or []
            target_f = target_c = None
            for p in profiles:
                if p.get("index") == current:
                    target_f, target_c = p.get("temp_f"), p.get("temp_c")
            heating = state_id in (7, 8, 9)
            if units == "C":
                shown = live_c if heating and live_c is not None else target_c
                self.temp_label.set_text("—" if shown is None else f"{int(round(float(shown)))}°")
            else:
                shown = live_f if heating and live_f is not None else target_f
                self.temp_label.set_text("—" if shown is None else f"{int(shown)}°")
            for cls in ("ready", "preheat", "cool"):
                self.temp_label.remove_css_class(cls)
                self.state_label.remove_css_class(cls)
                self.hero.remove_css_class(cls)
            if state_id == 7:
                self.temp_label.add_css_class("preheat")
                self.state_label.add_css_class("preheat")
                self.hero.add_css_class("preheat")
            elif state_id == 8:
                self.temp_label.add_css_class("ready")
                self.state_label.add_css_class("ready")
                self.hero.add_css_class("ready")
            elif state_id == 9:
                self.temp_label.add_css_class("cool")
                self.state_label.add_css_class("cool")
                self.hero.add_css_class("cool")
            heating_now = state_id in (7, 8)
            for widget, has in (
                (self.heat_btn, heating_now),
                (self.hero, heating_now),
            ):
                if has:
                    widget.add_css_class("heating")
                else:
                    widget.remove_css_class("heating")
            state = str(status.get("operating_state") or "").upper()
            self.state_label.set_text(state or "IDLE")
            product = (status.get("product") or {}).get("label") or "Peak Pro"
            self.meta_label.set_text(f"{product}  ·  {status.get('chamber') or ''}")
            self.heat_btn.set_label("HEAT" if state_id not in (7, 8) else "HEATING")
            self.boost_btn.set_sensitive(state_id in (7, 8))
            for i, btn in enumerate(self.profile_btns):
                if i == current:
                    btn.add_css_class("selected")
                else:
                    btn.remove_css_class("selected")
                prof = next((p for p in profiles if p.get("index") == i), None)
                if not prof:
                    continue
                self.profile_names[i].set_text(str(prof.get("name") or PRESETS[i][1]))
                if units == "C":
                    meta = f"{prof.get('temp_c')}°C · {prof.get('time')}s"
                else:
                    meta = f"{prof.get('temp_f')}°F · {prof.get('time')}s"
                self.profile_metas[i].set_text(meta)
            if 0 <= current < 4:
                prof = next((p for p in profiles if p.get("index") == current), None)
                key = (
                    current,
                    units,
                    (prof or {}).get("name"),
                    (prof or {}).get("temp_f"),
                    (prof or {}).get("time"),
                    (prof or {}).get("color"),
                )
                dragging = any(k in self._debounces for k in ("temp", "time", "color"))
                if key != self._editor_key and not dragging:
                    self._fill_editor(profiles, current, units)
                    self._editor_key = key
            if self.lantern_switch.get_active() != bool(status.get("lantern")):
                self.lantern_switch.set_active(bool(status.get("lantern")))
            if self.stealth_switch.get_active() != bool(status.get("stealth")):
                self.stealth_switch.set_active(bool(status.get("stealth")))
            brightness = (status.get("brightness") or {}).get("base")
            if brightness is not None:
                self.bright_scale.set_value(int(brightness))
        finally:
            self._syncing = False

    def _fill_editor(self, profiles: list[dict], index: int, units: str) -> None:
        prof = next((p for p in profiles if p.get("index") == index), None)
        if not prof:
            return
        if self.name_entry.get_text() != str(prof.get("name") or ""):
            self.name_entry.set_text(str(prof.get("name") or ""))
        if units == "C":
            self.temp_scale.set_range(230, 325)
            self.temp_scale.set_value(float(prof.get("temp_c") or 260))
        else:
            self.temp_scale.set_range(450, 620)
            self.temp_scale.set_value(float(prof.get("temp_f") or 510))
        self.time_scale.set_value(float(prof.get("time") or 60))
        color = prof.get("color") or PRESETS[index][0]
        self.color_btn.set_rgba(_hex_to_rgba(color))

    def _current_index(self) -> int:
        return int(self.bridge.status.get("current_profile") or 0)

    def _select_profile(self, index: int) -> None:
        self.bridge.call("set_profile", {"index": index})

    def _heat_toggle(self) -> None:
        state_id = int(self.bridge.status.get("operating_state_id") or -1)
        if state_id in (7, 8):
            self.bridge.call("stop_heat")
        else:
            self.bridge.call("start_heat")

    def _scan(self, *_args) -> None:
        self.scan_btn.set_sensitive(False)
        self.setup_status.set_text("Scanning…")
        self.bridge.call(
            "scan",
            {"timeout": 6},
            timeout=45,
            on_ok=self._on_scan,
            on_err=self._scan_failed,
        )

    def _scan_failed(self, message: str) -> None:
        self.scan_btn.set_sensitive(True)
        self._toast(message)

    def _on_scan(self, result: Any) -> None:
        self.scan_btn.set_sensitive(True)
        devices = (result or {}).get("devices") or []
        self._devices = devices
        while True:
            row = self.device_list.get_row_at_index(0)
            if row is None:
                break
            self.device_list.remove(row)
        if not devices:
            self.setup_status.set_text("Nothing found. Wake the Peak Pro and try again.")
            return
        self.setup_status.set_text(f"{len(devices)} device(s)")
        for device in devices:
            label = Gtk.Label(
                label=f"{device.get('name') or 'Peak Pro'}    {device.get('address')}",
                xalign=0,
            )
            self.device_list.append(label)

    def _row_activated(self, _list, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if index < 0 or index >= len(self._devices):
            return
        device = self._devices[index]
        self.setup_status.set_text("Connecting…")
        self.bridge.call(
            "connect",
            {"device_name": device.get("name"), "device_mac": device.get("address")},
            timeout=90,
            on_err=self._toast,
        )

    def _connect_saved(self, *_args) -> None:
        cfg = load_config()
        self.setup_status.set_text("Connecting…")
        self.bridge.call(
            "connect",
            {"device_name": cfg.get("device_name"), "device_mac": cfg.get("device_mac")},
            timeout=90,
            on_err=self._toast,
        )

    def _apply_name(self, *_args) -> None:
        name = self.name_entry.get_text().strip()
        if not name:
            return
        self.bridge.call("set_profile_name", {"index": self._current_index(), "name": name})

    def _temp_changed(self, scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        value = scale.get_value()

        def send() -> bool:
            args: dict[str, Any] = {"index": self._current_index()}
            if _units() == "C":
                args["celsius"] = value
            else:
                args["fahrenheit"] = value
            self.bridge.call("set_profile_temp", args)
            return False

        self._debounce("temp", 450, send)

    def _time_changed(self, scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        value = scale.get_value()

        def send() -> bool:
            self.bridge.call("set_profile_time", {"index": self._current_index(), "seconds": value})
            return False

        self._debounce("time", 450, send)

    def _color_changed(self, *_args) -> None:
        if self._syncing:
            return
        hex_color = _rgba_to_hex(self.color_btn.get_rgba())
        self._debounce(
            "color",
            300,
            lambda: self.bridge.call(
                "set_profile_color",
                {"index": self._current_index(), "hex": hex_color},
            )
            or False,
        )

    def _set_color(self, hex_color: str) -> None:
        self.color_btn.set_rgba(_hex_to_rgba(hex_color))
        self.bridge.call("set_profile_color", {"index": self._current_index(), "hex": hex_color})

    def _anim(self, name: str) -> None:
        hex_color = _rgba_to_hex(self.color_btn.get_rgba())
        self.bridge.call(
            "set_animation",
            {"anim": name, "index": self._current_index(), "colors": [hex_color]},
        )

    def _lantern_toggled(self, switch: Gtk.Switch, state: bool) -> bool:
        if self._syncing:
            return False
        self.bridge.call("start_lantern" if state else "stop_lantern")
        return False

    def _stealth_toggled(self, switch: Gtk.Switch, state: bool) -> bool:
        if self._syncing:
            return False
        self.bridge.call("set_stealth", {"enable": bool(state)})
        return False

    def _brightness_changed(self, scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        level = int(scale.get_value())
        self._debounce("bright", 200, lambda: self.bridge.call("set_brightness", {"level": level}) or False)

    def _debounce(self, name: str, delay: int, fn) -> None:
        handle = self._debounces.get(name)
        if handle:
            GLib.source_remove(handle)
        self._debounces[name] = GLib.timeout_add(delay, fn)

    def _set_units(self, units: str) -> None:
        cfg = load_config()
        cfg["units"] = units
        save_config(cfg)
        self.apply_status(self.bridge.status)

    def _show_info(self) -> None:
        s = self.bridge.status
        if not s.get("connected"):
            self._toast("Not connected")
            return
        product = (s.get("product") or {}).get("label") or "Peak Pro"
        text = (
            f"{s.get('device_name')}\n{product}\n\n"
            f"Serial    {s.get('serial')}\n"
            f"Firmware  {s.get('firmware')}  (boot {s.get('bootloader')})\n"
            f"Chamber   {s.get('chamber')}\n"
            f"Uptime    {s.get('uptime')}\n"
            f"Dabs      {s.get('total_dabs')} total, ~{s.get('dabs_remaining')} left\n"
            f"MAC       {s.get('device_mac')}"
        )
        dialog = Adw.AlertDialog(heading="Device", body=text)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    def _show_usage(self) -> None:
        s = self.bridge.status
        if not s.get("connected"):
            self._toast("Not connected")
            return
        t = s.get("telemetry") or {}
        lines = [
            f"Total       {s.get('total_dabs', 0)} lifetime  ·  ~{s.get('dabs_remaining', 0)} left in chamber",
            f"Average     {s.get('dabs_per_day', 0)}/day (device)",
            "",
            f"Today       {t.get('today', 0)}",
            f"This week   {t.get('this_week', 0)}",
            f"This month  {t.get('this_month', 0)}",
            f"This year   {t.get('this_year', 0)}",
        ]
        daily = t.get("daily") or []
        if daily:
            counts = [d.get("count", 0) for d in daily]
            peak = max(counts) or 1
            blocks = " ▁▂▃▄▅▆▇█"
            spark = "".join(
                blocks[min(len(blocks) - 1, int(c / peak * (len(blocks) - 1)))] for c in counts
            )
            lines.append("")
            lines.append(f"Last {len(daily)} days  {spark}")
        since = t.get("tracking_since")
        lines.append("")
        if since:
            from datetime import datetime

            lines.append(f"Tracked locally since {datetime.fromtimestamp(since):%Y-%m-%d}")
        else:
            lines.append("Take a dab to start local tracking.")
        dialog = Adw.AlertDialog(heading="Usage", body="\n".join(lines))
        dialog.add_response("ok", "OK")
        dialog.present(self)

    def _confirm_reset(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Factory reset?",
            body="This wipes profiles and settings on the Peak Pro.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, response: self.bridge.call("factory_reset") if response == "reset" else None,
        )
        dialog.present(self)

    def _toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))
        self.setup_status.set_text(message)
        self.scan_btn.set_sensitive(True)

    def _notify(self, title: str, body: str) -> None:
        app = self.get_application()
        if app:
            note = Gio.Notification.new(title)
            note.set_body(body)
            app.send_notification("ember-event", note)
        self._toast(body)

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return):
            self._heat_toggle()
            return True
        if keyval in (Gdk.KEY_b, Gdk.KEY_B):
            self.bridge.call("boost_heat")
            return True
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.bridge.call("stop_heat")
            return True
        if keyval in (Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3, Gdk.KEY_4):
            self._select_profile(int(chr(keyval)) - 1)
            return True
        return False
