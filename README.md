# OmaPuffco

[![tests](https://github.com/Auxxed/omapuffco/actions/workflows/tests.yml/badge.svg)](https://github.com/Auxxed/omapuffco/actions/workflows/tests.yml)

Puffco Peak Pro controls for the [Omarchy](https://omarchy.org/) bar: chamber
temperature and battery at a glance, heat and profile controls, LED colors,
and usage stats read straight from the device.

OmaPuffco is unofficial and not affiliated with Puffco. It speaks the
reverse-engineered Lorax Bluetooth protocol, so a Puffco firmware update can
break it. It works with the **Peak Pro only**; Proxy and Pivot are rejected.

![OmaPuffco panel](preview.png)

## Supported devices

- **Puffco Peak Pro**, every colorway. Heat control, profiles, battery, usage,
  the fault log and LED colours all work across Peak Pro firmware versions.
  Firmware before AF stores LED colours in an older format; OmaPuffco writes
  that format the way the Puffco app does, but it has only been tested on
  newer firmware so far.
- Firmware from before Puffco's current Bluetooth protocol can't connect.
  Update it once in the Puffco app.
- More than one Peak nearby (or a friend's): choose it with **Find nearby
  Peaks** in the panel, or run `omapuffco scan` and then
  `omapuffco connect --mac AA:BB:...`.
- Any Bluetooth adapter BlueZ supports (see [Bluetooth adapter](#bluetooth-adapter)).
- The Puffco Proxy and Pivot are not supported.

## Install

```bash
omarchy plugin add https://github.com/Auxxed/omapuffco --enable && ~/.config/omarchy/plugins/auxxed.omapuffco/install.sh
```

`omarchy plugin add` installs the bar widget. `install.sh` sets up what the
widget needs, all inside your home directory with no root access:

- a Python environment in `~/.local/share/omapuffco/venv` with the Bluetooth
  libraries from PyPI: [`bleak`](https://pypi.org/project/bleak/),
  [`cbor2`](https://pypi.org/project/cbor2/) and
  [`dbus-fast`](https://pypi.org/project/dbus-fast/)
- the `omapuffco` command in `~/.local/bin`
- the `omapuffco-daemon` systemd user service, which holds the one Bluetooth
  connection the widget and the command share

If you add the plugin without running `install.sh`, the widget shows
**Finish setup**, which runs it in a terminal.

Requirements: Python 3.10 or newer, BlueZ, and systemd — all present on a
standard Omarchy install.

Then wake the Peak, keep it near the computer, and disconnect the Puffco phone
app (the Peak accepts one connection at a time). Click the OmaPuffco widget and
choose **Connect**.

If something doesn't work, run `omapuffco doctor`. It checks Bluetooth, the
background daemon, the bar widget and your Peak, and prints the command that
fixes anything it finds.

## Update

```bash
omarchy plugin update auxxed.omapuffco && ~/.config/omarchy/plugins/auxxed.omapuffco/install.sh
```

Used this back when it was called Ember? Run the install line above.
`install.sh` moves your settings and dab history across and swaps the old
widget out, keeping its place in the bar.

## Remove

```bash
~/.config/omarchy/plugins/auxxed.omapuffco/uninstall.sh
```

This stops the daemon, removes the `omapuffco` command and the Python environment,
and removes the plugin. Your dab history (`~/.local/share/omapuffco/dabs.json`) and
settings (`~/.config/omapuffco`) are kept; delete those folders too for a clean
removal.

## Using it

- **Bar** — chamber temperature and battery, with ⚡ while plugged in.
  Left-click opens the panel, right-click starts a heat cycle, middle-click
  refreshes.
- **Control** — Heat, Boost and Stop, with a countdown ring while the Peak
  heats up and during the session; the four heat profiles (click one to
  select it, click a value to type it, or nudge it with − and +); vapor level;
  and boost temperature and time. Below 10% battery, unplugged, it warns that
  the Peak may refuse to heat.
- **Lights** — LED on/off, brightness, stealth mode, the selected
  profile's LED color.
- **Usage** — today, this week, this month and lifetime counts, a daily chart,
  streaks, your peak hour, average session length and temperature, and which
  heat profiles you used over the last 30 days (with each one's usual
  temperature). **History** lists every dab with its profile, temperature and
  heat-up time; tap one to add a note, and tap again any time to edit it.
- **Care** — Battery Preservation (charge to 80% only, the same setting as
  the Puffco app's), battery saver (puts the Peak to sleep 30 seconds after a session
  ends or after 10 minutes idle, turning the lantern off first), a Q-tip reminder after each dab, and a
  chamber-clean reminder (every 10–100 dabs); under Goals, an optional daily
  limit and a weekly recap on Sunday evenings.
- **Device** — rename the Peak; model, chamber, battery (with time until full
  while charging), battery capacity and health, firmware, serial and uptime;
  the fault log of heater, battery and pairing problems (saved per Peak, so it
  opens instantly after the first read); disconnect
  so your phone or another computer can connect; sleep or power off. **Tips** has the factory heat presets (490 / 510 / 530 / 545°F)
  and a short care list.

### Notifications and reconnecting

OmaPuffco sends a desktop notification when the Peak reaches temperature, once
when the battery drops to 15% (again only after it recovers or charges), and
when the chamber is due a clean. After each session that reached temperature
it reminds you to Q-tip the chamber while it's still warm; switch that off under
Care or with `omapuffco qtip off`. If you set a daily limit, it notifies once
the day you reach it, and every Sunday evening it sends a weekly recap (how
many sessions, your most-used profile, and how that compares with the week
before); both live under Care → Goals. Turn the ready and low-battery alerts off with
`"notify_ready": false` or `"notify_low_battery": false` in
`~/.config/omapuffco/config.json`.

After a restart or reboot the daemon reconnects to the last Peak on its own.
Pressing Disconnect (or `omapuffco disconnect`) stops that until you connect
again.

Battery capacity is what the Peak's fuel gauge has learned the pack holds,
shown against the stock Peak Pro battery's rated 1700 mAh.

To go easy on the Peak's battery, OmaPuffco only checks it every 20 seconds
while nothing is heating and the panel is closed; it speeds up the moment you
open the panel or start a heat cycle.

### Where the usage numbers come from

The Peak keeps its own log of heat cycles. OmaPuffco reads it when it connects and
after each session, and counts every cycle that reached temperature;
`omapuffco sync` does the same on demand. Until the phone app sets the Peak's clock
after a restart, the log's timestamps count from boot. OmaPuffco places those using
the Peak's current clock, and skips cycles from before a later restart rather
than guessing their date. For any period the device log no longer covers, the
cycles OmaPuffco saw while connected fill in.

Usage is kept per Peak, by serial number. Your Peak brings its stats to any
computer running OmaPuffco, rebuilt from its own log, and a friend's Peak shows
its own stats instead of mixing into yours. The Peak's log holds roughly its
last 1,000 events (several weeks of use); older day-by-day history stays on
the computer that recorded it. The lifetime total always comes from the Peak.

## Command line

The widget runs these under the hood; they also work for scripting or outside
Omarchy.

```bash
omapuffco scan
omapuffco connect                  # or: omapuffco connect --mac AA:BB:...
omapuffco disconnect               # free the Peak for your phone or another PC
omapuffco status
omapuffco heat start|stop|boost
omapuffco profile 0 --temp-f 510 --time 75 --color '#ff6a1a'
omapuffco lantern on
omapuffco brightness 160
omapuffco color '#ff6a1a' --index 0  # a profile's LED color
omapuffco stealth on
omapuffco preserve on              # stop charging at 80% (off: charge to 100%)
omapuffco saver on                 # sleep after sessions and 10 min idle
omapuffco clean --every 30         # remind after N dabs (10–100)
omapuffco clean done               # reset the cleaning countdown
omapuffco qtip on|off              # Q-tip reminder after each dab
omapuffco sessions --limit 20      # recent dabs with their notes
omapuffco note d1473 "great flavor" # add or edit a dab's note (no text clears it)
omapuffco limit 5                  # notify after 5 dabs in a day (0 = off)
omapuffco recap                    # this week so far; `recap on|off` for Sundays
omapuffco stats                    # today / week / month / year / lifetime
omapuffco sync                     # pull usage history from the Peak's log
omapuffco faults                   # faults the Peak recorded
omapuffco doctor                   # check Bluetooth, the daemon and your Peak
omapuffco waybar
omapuffco sleep
omapuffco off
```

### Waybar

```jsonc
"custom/omapuffco": {
  "exec": "omapuffco waybar",
  "return-type": "json",
  "interval": 2,
  "on-click": "omapuffco status",
  "on-click-right": "omapuffco heat start"
}
```

## Bluetooth adapter

OmaPuffco uses the first powered adapter BlueZ reports. To pin a specific one when
you have several, set it in `~/.config/omapuffco/config.json` (`bluetoothctl list`
shows what you have):

```json
{ "adapter": "hci1" }
```

## Development

```bash
git clone https://github.com/Auxxed/omapuffco.git
cd omapuffco
./install.sh                   # links the checkout in as a development plugin
~/.local/share/omapuffco/venv/bin/pip install pytest
~/.local/share/omapuffco/venv/bin/python -m pytest
```

Keep virtual environments outside the checkout: `omarchy plugin` refuses
symlinks inside a plugin folder, and a venv is full of them.

The tests cover the CBOR and color codec, audit-log decoding, dab-history date
maths, config and profile limits, the daemon liveness probe, and the plugin
manifest. The Bluetooth layer needs real hardware, so it's exercised by hand.

Protocol work builds on [Fr0st3h/PuffcoBLE](https://github.com/Fr0st3h/PuffcoBLE)
and the [OldGrowthCrypto Linux/BlueZ fork](https://github.com/OldGrowthCrypto/Puffco);
audit-log decoding follows [puff.social](https://github.com/puff-social/web).

## License

[MIT](LICENSE)
