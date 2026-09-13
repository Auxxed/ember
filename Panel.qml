import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

Panel {
  id: root
  moduleName: "auxxed.omapuffco"
  ipcTarget: "auxxed.omapuffco"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var statusData: ({})
  property bool refreshPending: false

  // Palette/typography lifted off the bar so every child stops repeating the
  // `bar ? bar.x : fallback` ternary, matching how first-party panels do it.
  readonly property color foreground: root.barForeground
  readonly property color dim: Qt.darker(foreground, 1.4)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // The bar tracks the widget mounted in its slot, not this nested panel, so
  // panel-to-panel Tab handoff has to hand it the host widget's identity.
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function refresh() {
    if (statusProc.running) {
      refreshPending = true
      return
    }
    refreshPending = false
    statusProc.running = true
  }

  // Fire a control command detached through a login shell — the same path
  // bar.run() takes internally — then re-poll shortly after, since the daemon
  // usually reflects a heat/profile change well before the poll interval
  // would catch it. Going straight to Util avoids depending on `bar`, which
  // is null for a beat right after the panel is created.
  function run(cmd) {
    Util.execDetached(cmd)
    kickTimer.restart()
  }

  // Same, for commands carrying user-entered text: argv never passes through
  // a shell that could re-tokenize it, so a profile named `$(reboot)` is a
  // profile name and nothing else.
  function runArgv(argv) {
    Util.execArgv(argv)
    kickTimer.restart()
  }

  onOpenedChanged: {
    if (opened) {
      refresh()
    } else {
      cancelEdit()
      pendingTemps = ({})
      pendingTimes = ({})
      pendingNames = ({})
      pendingColors = ({})
      pendingVapors = ({})
      pendingBoostTemps = ({})
      pendingBoostTimes = ({})
      pendingStealth = undefined
      pendingLantern = undefined
      pendingLanternTimeout = -1
      pendingBrightness = -1
      pendingDeviceName = ""
      confirmPowerOff = false
      page = "control"
    }
  }

  // ---------------------------------------------------------------- state
  readonly property bool connected: statusData.connected === true
  readonly property var profiles: statusData.profiles || []
  readonly property bool hasProfiles: connected && profiles.length > 0

  readonly property int currentProfile: {
    var n = Number(statusData.current_profile)
    return isFinite(n) ? n : -1
  }
  readonly property var activeProfile: {
    for (var i = 0; i < profiles.length; i++) {
      if (Number(profiles[i].index) === root.currentProfile) return profiles[i]
    }
    return null
  }

  // OperatingState ids from omapuffco's constants.py: 7 preheat, 8 at-temp, 9 fade.
  readonly property int stateId: {
    var n = Number(statusData.operating_state_id)
    return isFinite(n) ? n : -1
  }
  readonly property bool preheating: connected && stateId === 7
  readonly property bool atTemp: connected && stateId === 8
  readonly property bool cooling: connected && stateId === 9
  readonly property bool heating: preheating || atTemp

  // `omapuffco` stores the user's unit preference in its own config; the bar label
  // already honors it, so the panel has to as well or the two disagree.
  property string units: "F"
  readonly property bool celsius: units === "C"

  function fToC(f) { return (Number(f) - 32) * 5 / 9 }
  function cToF(c) { return Number(c) * 9 / 5 + 32 }

  // `c` is optional: profile tiles work off Fahrenheit alone (the unit the
  // device stores and the clamp range is expressed in) and convert here.
  function formatTemp(f, c) {
    if (celsius) {
      var vc = Number(c)
      if (!isFinite(vc)) {
        var vf = Number(f)
        if (!isFinite(vf)) return ""
        vc = fToC(vf)
      }
      return Math.round(vc) + "°C"
    }
    var v = Number(f)
    if (!isFinite(v)) return ""
    return Math.round(v) + "°F"
  }

  readonly property string tempLabel: {
    var t = formatTemp(statusData.heater_temp_f, statusData.heater_temp_c)
    return t !== "" ? t : "—"
  }
  readonly property string targetLabel: activeProfile
    ? formatTemp(activeProfile.temp_f, activeProfile.temp_c)
    : ""
  readonly property string deviceName: String(statusData.device_name || "Peak Pro")
  readonly property string batteryLabel: {
    if (!connected) return ""
    var n = Number(statusData.battery)
    var pct = isFinite(n) ? Math.round(n) + "%" : ""
    return pluggedIn ? "\uf0e7 " + pct : pct
  }
  readonly property bool pluggedIn: {
    var src = String(statusData.charge_source || "")
    return connected && src !== "" && src !== "Unplugged"
  }
  readonly property string batteryDetail: {
    if (!connected) return ""
    var n = Number(statusData.battery)
    var bits = []
    if (isFinite(n)) bits.push(Math.round(n) + "%")
    var state = String(statusData.charge_state || "")
    if (state !== "" && state !== "Unplugged") bits.push(state)
    return bits.join(" · ")
  }
  readonly property string metaLabel: {
    if (!connected) return needsSetup ? "Setup needed" : (connecting ? "Connecting…" : "Disconnected")
    var s = String(statusData.operating_state || "Connected")
    if (heating && targetLabel !== "") s += " · " + targetLabel
    else if (chamberLabel !== "") s += " · " + chamberLabel
    return s
  }

  readonly property string chamberLabel: {
    if (!connected) return ""
    var s = String(statusData.chamber || "")
    if (s === "" || s === "No chamber") return ""
    return s
  }
  readonly property string remainingLabel: {
    if (!connected) return ""
    var n = Number(statusData.dabs_remaining)
    if (!isFinite(n) || n < 0) return ""
    return "~" + Math.round(n)
  }

  readonly property var telemetry: statusData.telemetry || ({})
  readonly property bool showStats: {
    if (connected) return true
    if (telemetry.tracking_since) return true
    return Number(telemetry.today) > 0
      || Number(telemetry.this_week) > 0
      || Number(telemetry.this_month) > 0
  }

  function countLabel(value) {
    var n = Number(value)
    return String(isFinite(n) ? Math.round(n) : 0)
  }

  readonly property var dailySeries: telemetry.daily || []
  readonly property var hourSeries: telemetry.hours || []
  readonly property var weekdaySeries: telemetry.weekdays || []
  readonly property var colorSeries: telemetry.colors || []
  readonly property int chartPeak: {
    var peak = 1
    for (var i = 0; i < dailySeries.length; i++) {
      var c = Number(dailySeries[i].count)
      if (isFinite(c) && c > peak) peak = c
    }
    return peak
  }
  readonly property int hourPeak: {
    var peak = 1
    for (var i = 0; i < hourSeries.length; i++) {
      var c = Number(hourSeries[i])
      if (isFinite(c) && c > peak) peak = c
    }
    return peak
  }

  function formatHour(hour) {
    if (hour === null || hour === undefined || hour === "") return "—"
    var h = Number(hour)
    if (!isFinite(h)) return "—"
    h = Math.round(h)
    var twelve = h % 12
    if (twelve === 0) twelve = 12
    return twelve + (h < 12 ? "AM" : "PM")
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || seconds === "") return "—"
    var s = Number(seconds)
    if (!isFinite(s)) return "—"
    s = Math.max(0, Math.round(s))
    var m = Math.floor(s / 60)
    var r = s % 60
    return m + ":" + (r < 10 ? "0" : "") + r
  }

  function formatAvgTemp(temp) {
    if (temp === null || temp === undefined || temp === "") return "—"
    var n = Number(temp)
    if (!isFinite(n)) return "—"
    return formatTemp(n, undefined)
  }

  property bool connecting: false
  // Set when `omapuffco` isn't installed or its daemon isn't running, e.g. right
  // after `omarchy plugin add` without install.sh.
  property bool needsSetup: false
  readonly property string installScript: Qt.resolvedUrl("install.sh").toString().replace(/^file:\/\//, "")
  property bool confirmPowerOff: false
  property var pendingStealth: undefined
  property var pendingLantern: undefined
  property int pendingBrightness: -1
  property string page: "control"
  property string activeMood: ""
  property string activeStyle: ""
  property string pendingDeviceName: ""
  property int pendingLanternTimeout: -1

  readonly property bool onControl: page === "control"
  readonly property bool onLights: page === "lights"
  readonly property bool onUsage: page === "usage"
  readonly property bool onDevice: page === "device"

  readonly property var pageOptions: [
    { "value": "control", "label": "Control" },
    { "value": "lights", "label": "Lights" },
    { "value": "usage", "label": "Usage" },
    { "value": "device", "label": "Device" }
  ]

  readonly property var exclusiveMoods: [
    { "id": "puffcon", "label": "Puffcon", "colors": ["#ff4fa3", "#3b9eff"] },
    { "id": "july4", "label": "4th of July", "colors": ["#ff4d4d", "#ffffff", "#3b9eff"] },
    { "id": "candle", "label": "Candle", "colors": ["#ffb07a", "#e8955a"] },
    { "id": "hologram", "label": "Hologram", "colors": ["#7c3aed", "#3b9eff", "#22d3ee"] },
    { "id": "lupus", "label": "Lupus", "colors": ["#6d28d9", "#c4b5fd"] },
    { "id": "disco", "label": "Disco", "colors": ["#ff4d4d", "#f6d32d", "#3dd68c", "#3b9eff", "#a855f7", "#ff4fa3"] }
  ]

  readonly property var lightStyles: [
    { "value": "fill", "label": "Fill" },
    { "value": "fade", "label": "Fade" },
    { "value": "disco", "label": "Disco" },
    { "value": "split", "label": "Split" },
    { "value": "spin", "label": "Spin" }
  ]

  function applyMood(id) {
    activeMood = id
    activeStyle = ""
    pendingLantern = true
    var args = ["omapuffco", "mood", id]
    if (currentProfile >= 0) {
      args.push("--index")
      args.push(String(currentProfile))
    }
    runArgv(args)
  }

  function applyStyle(name) {
    activeStyle = name
    activeMood = ""
    pendingLantern = true
    var hex = activeProfile ? profileSwatch(activeProfile.color) : ""
    if (hex === "") hex = colorPalette[0]
    var args = ["omapuffco", "anim", name, "--color", hex]
    if (currentProfile >= 0) {
      args.push("--index")
      args.push(String(currentProfile))
    }
    runArgv(args)
  }

  function applyLightColor(hex) {
    if (currentProfile < 0) return
    activeMood = ""
    pendingLantern = true
    var updated = {}
    for (var key in pendingColors) updated[key] = pendingColors[key]
    updated[currentProfile] = hex
    pendingColors = updated
    // `omapuffco anim solid` paints the live lantern without reselecting the
    // heat profile (which flashes factory green over the preview).
    runArgv(["omapuffco", "anim", "solid", "--color", hex, "--index", String(currentProfile)])
    clearPendingTimer.restart()
  }

  readonly property var vaporLevels: [
    { "value": "smooth", "label": "Smooth" },
    { "value": "bold", "label": "Bold" },
    { "value": "intense", "label": "Intense" },
    { "value": "extreme", "label": "Extreme" }
  ]

  function profileVapor(index, fallback) {
    var pending = pendingVapors[index]
    if (pending !== undefined) return pending
    return String(fallback || "")
  }

  function applyVapor(name) {
    if (currentProfile < 0) return
    var updated = {}
    for (var key in pendingVapors) updated[key] = pendingVapors[key]
    updated[currentProfile] = name
    pendingVapors = updated
    runArgv(["omapuffco", "profile", String(currentProfile), "--vapor", name])
    clearPendingTimer.restart()
  }

  readonly property real minBoostTempF: 0
  readonly property real maxBoostTempF: 36
  readonly property int boostTempStepF: 2
  readonly property real minBoostTimeS: 0
  readonly property real maxBoostTimeS: 60
  readonly property int boostTimeStepS: 5
  property var pendingBoostTemps: ({})
  property var pendingBoostTimes: ({})

  function profileBoostTempF(index, fallback) {
    var pending = pendingBoostTemps[index]
    if (pending !== undefined) return pending
    var n = Number(fallback)
    return isFinite(n) ? n : 0
  }

  function profileBoostTime(index, fallback) {
    var pending = pendingBoostTimes[index]
    if (pending !== undefined) return pending
    var n = Number(fallback)
    return isFinite(n) ? n : 0
  }

  function clampBoostTempF(value) {
    return Math.max(minBoostTempF, Math.min(maxBoostTempF, Math.round(value)))
  }

  function clampBoostTime(value) {
    return Math.max(minBoostTimeS, Math.min(maxBoostTimeS, Math.round(value)))
  }

  function formatBoostTemp(f) {
    var n = Number(f)
    if (!isFinite(n)) n = 0
    if (celsius) return "+" + Math.round(n * 5 / 9) + "°C"
    return "+" + Math.round(n) + "°F"
  }

  function canStepBoostTemp(index, fallback, delta) {
    if (index < 0) return false
    var current = profileBoostTempF(index, fallback)
    return clampBoostTempF(current + delta) !== Math.round(current)
  }

  function canStepBoostTime(index, fallback, delta) {
    if (index < 0) return false
    var current = profileBoostTime(index, fallback)
    return clampBoostTime(current + delta) !== Math.round(current)
  }

  function stepBoostTemp(index, fallback, delta) {
    if (!canStepBoostTemp(index, fallback, delta)) return
    var updated = {}
    for (var key in pendingBoostTemps) updated[key] = pendingBoostTemps[key]
    updated[index] = clampBoostTempF(profileBoostTempF(index, fallback) + delta)
    pendingBoostTemps = updated
    commitWriteTimer.restart()
  }

  function stepBoostTime(index, fallback, delta) {
    if (!canStepBoostTime(index, fallback, delta)) return
    var updated = {}
    for (var key in pendingBoostTimes) updated[key] = pendingBoostTimes[key]
    updated[index] = clampBoostTime(profileBoostTime(index, fallback) + delta)
    pendingBoostTimes = updated
    commitWriteTimer.restart()
  }

  function commitBoost() {
    var seen = {}
    for (var key in pendingBoostTemps) seen[key] = true
    for (key in pendingBoostTimes) seen[key] = true
    for (key in seen) {
      var index = Math.round(Number(key))
      if (!isFinite(index) || index < 0) continue
      var args = ["omapuffco", "profile", String(index)]
      if (pendingBoostTemps[key] !== undefined)
        args.push("--boost-temp", String(Math.round(pendingBoostTemps[key])))
      if (pendingBoostTimes[key] !== undefined)
        args.push("--boost-time", String(Math.round(pendingBoostTimes[key])))
      runArgv(args)
    }
  }

  readonly property var lanternTimeouts: [1800, 3600, 7200, 14400]

  readonly property int lanternTimeoutSec: {
    if (pendingLanternTimeout >= 0) return pendingLanternTimeout
    var n = Number(statusData.lantern_timeout)
    return isFinite(n) && n > 0 ? Math.round(n) : 7200
  }

  function formatLanternTimeout(seconds) {
    var s = Number(seconds)
    if (!isFinite(s) || s <= 0) return ""
    if (s >= 3600) {
      var hours = s / 3600
      return (hours === Math.round(hours) ? Math.round(hours) : hours.toFixed(1)) + "h"
    }
    return Math.round(s / 60) + "m"
  }

  function setLanternTimeout(seconds) {
    pendingLanternTimeout = seconds
    runArgv(["omapuffco", "lantern", "--timeout", String(seconds)])
    clearPendingTimer.restart()
  }

  readonly property string shownDeviceName: {
    if (pendingDeviceName !== "") return pendingDeviceName
    return deviceName
  }

  function commitDeviceName(raw) {
    var name = String(raw || "").replace(/^\s+|\s+$/g, "")
    cancelEdit()
    if (name === "") return
    pendingDeviceName = name
    runArgv(["omapuffco", "name", name])
    clearPendingTimer.restart()
  }

  readonly property bool stealthOn: pendingStealth !== undefined
    ? pendingStealth === true
    : statusData.stealth === true
  readonly property bool lanternOn: pendingLantern !== undefined
    ? pendingLantern === true
    : statusData.lantern === true
  readonly property int brightnessLevel: {
    if (pendingBrightness >= 0) return pendingBrightness
    var b = statusData.brightness || ({})
    var n = Number(b.base)
    return isFinite(n) ? Math.round(n) : 80
  }

  function finishSetup() {
    Util.execArgv(["xdg-terminal-exec", "bash", "-c",
      "\"$1\"; echo; read -rp 'Press Enter to close'", "omapuffco-setup", root.installScript])
  }

  function connectDevice() {
    if (connecting) return
    connecting = true
    connectGiveUp.restart()
    run("omapuffco connect")
  }

  function toggleStealth() {
    var next = !stealthOn
    pendingStealth = next
    run("omapuffco stealth " + (next ? "on" : "off"))
  }

  function toggleLantern() {
    var next = !lanternOn
    pendingLantern = next
    run("omapuffco lantern " + (next ? "on" : "off"))
  }

  function setBrightness(value) {
    pendingBrightness = Math.max(0, Math.min(255, Math.round(Number(value))))
    commitBrightnessTimer.restart()
  }

  function commitBrightness() {
    if (pendingBrightness < 0) return
    runArgv(["omapuffco", "brightness", String(pendingBrightness)])
    clearPendingTimer.restart()
  }

  // The heat state drives the hero glyph's color, and mirrors the bar widget's
  // own active tint so the two surfaces never disagree at a glance.
  readonly property color heatColor: heating ? urgent : (cooling ? Color.accent : dim)

  // Profiles carry the LED color the device glows for them; it's how the app's
  // own editor identifies them, so the tiles show the same swatch. Anything
  // that isn't a plain 6-digit hex is dropped rather than handed to QML.
  function profileSwatch(raw) {
    var s = String(raw || "")
    return /^#[0-9A-Fa-f]{6}$/.test(s) ? s : ""
  }

  readonly property var colorPalette: [
    "#3b9eff", "#3dd68c", "#ff4d4d", "#ffffff",
    "#ff6a1a", "#a855f7", "#f6d32d", "#99ffff"
  ]

  function profileColor(index, fallback) {
    var pending = pendingColors[index]
    if (pending !== undefined) return pending
    return profileSwatch(fallback)
  }

  // The device's name field is a fixed-size buffer that a shorter write
  // doesn't clear, so a renamed profile can read back as "New\0ldTail".
  function cleanName(raw) {
    var s = String(raw || "")
    var cut = s.indexOf("\u0000")
    if (cut >= 0) s = s.slice(0, cut)
    return s.replace(/^\s+|\s+$/g, "")
  }

  // ------------------------------------------------- profile temperature
  //
  // The daemon is the one chokepoint that clamps to the Peak Pro's rated
  // range; mirroring it here is what lets the steppers go inert at the ends
  // and typed values land in range instead of being clamped out of sight.
  readonly property real minTempF: 400
  readonly property real maxTempF: 620
  readonly property int tempStepF: 5
  readonly property real minTimeS: 5
  readonly property real maxTimeS: 180
  readonly property int timeStepS: 5

  // Optimistic per-index overrides, so a burst of taps steps by 5 each time
  // instead of each tap recomputing from the same not-yet-refreshed status,
  // and the writes coalesce into one BLE round trip.
  property var pendingTemps: ({})
  property var pendingTimes: ({})
  property var pendingNames: ({})
  property var pendingColors: ({})
  property var pendingVapors: ({})

  function profileTempF(index, fallback) {
    var pending = pendingTemps[index]
    if (pending !== undefined) return pending
    var n = Number(fallback)
    return isFinite(n) ? n : NaN
  }

  function profileTime(index, fallback) {
    var pending = pendingTimes[index]
    if (pending !== undefined) return pending
    var n = Number(fallback)
    return isFinite(n) ? n : NaN
  }

  function profileName(index, fallback) {
    var pending = pendingNames[index]
    if (pending !== undefined) return pending
    return cleanName(fallback)
  }

  function clampTempF(value) {
    return Math.max(minTempF, Math.min(maxTempF, Math.round(value)))
  }

  function clampTime(value) {
    return Math.max(minTimeS, Math.min(maxTimeS, Math.round(value)))
  }

  function setPendingTemp(index, tempF) {
    var updated = {}
    for (var key in pendingTemps) updated[key] = pendingTemps[key]
    updated[index] = tempF
    pendingTemps = updated
    commitWriteTimer.restart()
  }

  function setPendingTime(index, seconds) {
    var updated = {}
    for (var key in pendingTimes) updated[key] = pendingTimes[key]
    updated[index] = seconds
    pendingTimes = updated
    commitWriteTimer.restart()
  }

  function canStepTemp(index, fallback, delta) {
    var current = profileTempF(index, fallback)
    if (index < 0 || !isFinite(current)) return false
    return clampTempF(current + delta) !== Math.round(current)
  }

  function canStepTime(index, fallback, delta) {
    var current = profileTime(index, fallback)
    if (index < 0 || !isFinite(current)) return false
    return clampTime(current + delta) !== Math.round(current)
  }

  function stepTemp(index, fallback, delta) {
    if (!canStepTemp(index, fallback, delta)) return
    setPendingTemp(index, clampTempF(profileTempF(index, fallback) + delta))
  }

  function stepTime(index, fallback, delta) {
    if (!canStepTime(index, fallback, delta)) return
    setPendingTime(index, clampTime(profileTime(index, fallback) + delta))
  }

  function commitTemps() {
    for (var key in pendingTemps) {
      var index = Math.round(Number(key))
      if (!isFinite(index) || index < 0) continue
      runArgv(["omapuffco", "profile", String(index), "--temp-f", String(Math.round(pendingTemps[key]))])
    }
  }

  function commitTimes() {
    for (var key in pendingTimes) {
      var index = Math.round(Number(key))
      if (!isFinite(index) || index < 0) continue
      runArgv(["omapuffco", "profile", String(index), "--time", String(Math.round(pendingTimes[key]))])
    }
  }

  function commitWrites() {
    commitTemps()
    commitTimes()
    commitBoost()
    clearPendingTimer.restart()
  }

  // ------------------------------------------------------ inline editing
  property int editIndex: -1
  property string editField: ""
  readonly property bool editing: editField !== ""

  function startEdit(index, field) {
    if (index < 0 && field !== "device") return
    editIndex = index
    editField = field
  }

  function cancelEdit() {
    if (!editing) return
    editIndex = -1
    editField = ""
    // Hand keys back to the panel so Escape closes it again.
    Qt.callLater(function() { if (root.opened && keyCatcher) keyCatcher.forceActiveFocus() })
  }

  // Losing focus cancels — but only the edit that field actually owned. A
  // blur fired while tearing the field down (because a *different* field just
  // took over) must not cancel the edit that replaced it.
  function cancelEditFor(index, field) {
    if (editIndex === index && editField === field) cancelEdit()
  }

  function commitName(index, raw) {
    var name = String(raw || "").replace(/^\s+|\s+$/g, "")
    cancelEdit()
    if (index < 0 || name === "") return
    var updated = {}
    for (var key in pendingNames) updated[key] = pendingNames[key]
    updated[index] = name
    pendingNames = updated
    runArgv(["omapuffco", "profile", String(index), "--name", name])
    clearPendingTimer.restart()
  }

  function commitTemp(index, raw) {
    // Read the number out of whatever was typed ("550", "550°F", " 550 ") and
    // refuse anything that isn't one rather than shipping it to the daemon.
    var match = String(raw || "").match(/-?\d+(?:\.\d+)?/)
    cancelEdit()
    if (index < 0 || !match) return
    var typed = Number(match[0])
    if (!isFinite(typed)) return
    setPendingTemp(index, clampTempF(celsius ? cToF(typed) : typed))
  }

  function commitTime(index, raw) {
    var match = String(raw || "").match(/-?\d+(?:\.\d+)?/)
    cancelEdit()
    if (index < 0 || !match) return
    var typed = Number(match[0])
    if (!isFinite(typed)) return
    setPendingTime(index, clampTime(typed))
  }

  Process {
    id: statusProc
    command: ["bash", "-lc", "omapuffco --json status"]
    onRunningChanged: {
      if (running) {
        stallTimer.restart()
        return
      }
      stallTimer.stop()
      if (root.refreshPending) root.refresh()
    }
    onExited: function(exitCode) {
      if (exitCode !== 127) return
      root.needsSetup = true
      root.statusData = ({})
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (!/daemon is not running/i.test(text)) return
        root.needsSetup = true
        root.statusData = ({})
      }
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (!text) return
        try {
          root.statusData = JSON.parse(text)
          root.needsSetup = false
          if (root.statusData.connected === true) {
            root.connecting = false
            connectGiveUp.stop()
          }
        } catch (e) {
          // leave last-known state on a parse failure
        }
      }
    }
  }

  // `omapuffco --json status` waits on the daemon RPC; give up past that so a
  // stalled BLE call can't wedge the panel (a running Process can't be
  // re-run) and let the next poll retry.
  Timer {
    id: stallTimer
    interval: 8000
    onTriggered: {
      statusProc.running = false
      root.refreshPending = true
    }
  }

  Timer {
    id: kickTimer
    interval: 700
    onTriggered: root.refresh()
  }

  // Debounce a run of taps into a single write per profile.
  Timer {
    id: commitWriteTimer
    interval: 450
    onTriggered: root.commitWrites()
  }

  Timer {
    id: commitBrightnessTimer
    interval: 450
    onTriggered: root.commitBrightness()
  }

  Timer {
    id: connectGiveUp
    interval: 25000
    onTriggered: root.connecting = false
  }

  // Hand the readouts back to the device once the writes have had time to land
  // and be polled back. Never while a tap is still settling, or a nudge made
  // just before this fires would be dropped before it was sent.
  Timer {
    id: clearPendingTimer
    interval: 2500
    onTriggered: {
      if (commitWriteTimer.running || commitBrightnessTimer.running) return
      root.pendingTemps = ({})
      root.pendingTimes = ({})
      root.pendingNames = ({})
      root.pendingColors = ({})
      root.pendingVapors = ({})
      root.pendingBoostTemps = ({})
      root.pendingBoostTimes = ({})
      root.pendingStealth = undefined
      root.pendingLantern = undefined
      root.pendingLanternTimeout = -1
      root.pendingBrightness = -1
      root.pendingDeviceName = ""
    }
  }

  // Only polls while the panel is open — the bar widget's own poll already
  // covers the compact label the rest of the time.
  Timer {
    interval: 2000
    running: root.opened
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  FileView {
    path: Quickshell.env("HOME") + "/.config/omapuffco/config.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var cfg = JSON.parse(text() || "{}")
        root.units = String(cfg.units || "F").toUpperCase() === "C" ? "C" : "F"
      } catch (e) {
        root.units = "F"
      }
    }
    onLoadFailed: root.units = "F"
  }

  // Layer-shell panel rather than PopupCard: the tiles have text fields, and
  // an xdg-popup only receives keys once the compositor happens to route focus
  // through its parent surface.
  KeyboardPanel {
    id: card
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.barIdentity
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: card.fittedContentWidth(Style.space(360))
    contentHeight: card.fittedContentHeight(content.implicitHeight, Style.space(600))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While a tile field is open every key belongs to it, Escape included.
      blocked: root.editing
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        flickableDirection: Flickable.VerticalFlick

        Column {
          id: content
          width: scroller.width
          spacing: Style.spacing.panelGap

          // ---------- Hero: heat glyph · device + state · chamber temp ------
          PanelHero {
            title: root.deviceName
            detail: root.batteryLabel
            meta: root.metaLabel
            foreground: root.foreground
            fontFamily: root.fontFamily

            iconComponent: Text {
              textFormat: Text.PlainText
              text: "\uf06d"
              color: root.heatColor
              font.family: root.fontFamily
              font.pixelSize: Style.font.display

              Behavior on color { ColorAnimation { duration: 220 } }

              // Breathes only while the chamber is actually climbing.
              SequentialAnimation on opacity {
                running: root.preheating && root.opened
                loops: Animation.Infinite
                alwaysRunToEnd: true
                NumberAnimation { from: 1.0; to: 0.45; duration: 900; easing.type: Easing.InOutSine }
                NumberAnimation { from: 0.45; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
              }
            }

            trailingControl: Text {
              textFormat: Text.PlainText
              text: root.tempLabel
              color: root.connected ? root.foreground : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              font.bold: true

              Behavior on color { ColorAnimation { duration: 200 } }
            }
          }

          Segmented {
            width: parent.width
            visible: root.connected
            options: root.pageOptions
            value: root.page
            onPicked: function(value) { root.page = value }
          }

          // ---------- Disconnected ----------
          Column {
            width: parent.width
            visible: !root.connected
            spacing: Style.spacing.controlGap

            ActionButton {
              width: parent.width
              label: root.needsSetup ? "Finish setup" : (root.connecting ? "Connecting…" : "Connect")
              glyph: root.needsSetup ? "\uf0ad" : "\uf293"
              tint: Color.accent
              emphasized: true
              onActivated: root.needsSetup ? root.finishSetup() : root.connectDevice()
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
              text: root.needsSetup
                ? "OmaPuffco's background service isn't set up yet. Setup opens a terminal and installs it for your user; no root access is needed."
                : "Wake the Peak and keep it close to this computer. Disconnect the phone app first; the device accepts one connection at a time."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // ================================================== Control
          Column {
            id: controlPage
            width: parent.width
            visible: root.onControl && root.connected
            spacing: Style.spacing.panelGap

            Row {
              id: actionRow
              width: parent.width
              spacing: Style.spacing.controlGap

              readonly property real cellWidth: (width - spacing * 2) / 3

              ActionButton {
                width: actionRow.cellWidth
                label: "Heat"
                glyph: "\uf04b"
                tint: Color.accent
                emphasized: !root.heating
                onActivated: root.run("omapuffco heat start")
              }

              ActionButton {
                width: actionRow.cellWidth
                label: "Boost"
                glyph: "\uf0e7"
                onActivated: root.run("omapuffco heat boost")
              }

              ActionButton {
                width: actionRow.cellWidth
                label: "Stop"
                glyph: "\uf04d"
                tint: root.urgent
                emphasized: root.heating || root.cooling
                onActivated: root.run("omapuffco heat stop")
              }
            }

            Section {
              visible: root.hasProfiles
              title: "HEAT PROFILES"

              Grid {
                id: profileGrid
                width: parent.width
                columns: 2
                rowSpacing: Style.spacing.controlGap
                columnSpacing: Style.spacing.controlGap

                readonly property real cellWidth: (width - columnSpacing) / 2

                Repeater {
                  model: root.profiles

                  BorderSurface {
                    id: tile
                    required property var modelData

                    readonly property int profileIndex: {
                      var n = Number(modelData.index)
                      return isFinite(n) ? Math.round(n) : -1
                    }
                    readonly property bool active: profileIndex >= 0 && profileIndex === root.currentProfile
                    readonly property string swatch: root.profileColor(profileIndex, modelData.color)
                    readonly property real tempF: root.profileTempF(profileIndex, modelData.temp_f)
                    readonly property real timeS: root.profileTime(profileIndex, modelData.time)
                    readonly property string name: {
                      var n = root.profileName(profileIndex, modelData.name)
                      return n !== "" ? n : ("Profile " + (profileIndex + 1))
                    }

                    readonly property bool editingName: root.editIndex === profileIndex && root.editField === "name"
                    readonly property bool editingTemp: root.editIndex === profileIndex && root.editField === "temp"
                    readonly property bool editingTime: root.editIndex === profileIndex && root.editField === "time"
                    readonly property bool editingThis: editingName || editingTemp || editingTime

                    // Controls inside the tile sit above the tile's own mouse
                    // area, so their hover has to count as the tile's too.
                    readonly property bool hot: tileMouse.containsMouse
                      || renameButton.hovered || lowerStep.hovered || raiseStep.hovered
                      || lowerTime.hovered || raiseTime.hovered
                      || tempTapMouse.containsMouse || timeTapMouse.containsMouse
                      || nameTap.containsMouse

                    width: profileGrid.cellWidth
                    implicitHeight: tileBody.implicitHeight + Style.spacing.controlPaddingY * 2
                    radius: Style.cornerRadius

                    color: tileMouse.pressed ? Style.pressedFillFor(root.foreground, Color.accent)
                      : active ? Style.selectedFillFor(Color.accent, Color.accent)
                      : hot || editingThis ? Style.hoverFillFor(root.foreground, Color.accent)
                      : Style.normalFillFor(root.foreground, Color.accent)

                    borderSpec: active
                      ? Border.flat(Color.accent, Math.max(1, Style.normalBorderWidth))
                      : (hot || editingThis
                         ? Border.controlSpec("hover-cursor", root.foreground, Color.accent)
                         : Border.controlSpec("normal", root.foreground, Color.accent))

                    Behavior on color { ColorAnimation { duration: 120 } }

                    // Declared before the body so every control in it swallows
                    // its own taps instead of also reselecting the profile.
                    MouseArea {
                      id: tileMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: {
                        if (tile.profileIndex < 0) return
                        root.runArgv(["omapuffco", "profile", String(tile.profileIndex)])
                      }
                    }

                    Column {
                      id: tileBody
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.leftMargin: Style.spacing.controlPaddingX
                      anchors.rightMargin: Style.spacing.controlPaddingX
                      spacing: Style.spacing.xxs

                      // ----- Name -----
                      Item {
                        width: parent.width
                        implicitHeight: Math.max(Style.space(19), nameRow.implicitHeight)

                        Row {
                          id: nameRow
                          visible: !tile.editingName
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          spacing: Style.spacing.md

                          Rectangle {
                            id: swatchDot
                            width: tile.swatch !== "" ? Style.space(8) : 0
                            height: Style.space(8)
                            radius: width / 2
                            visible: tile.swatch !== ""
                            anchors.verticalCenter: parent.verticalCenter
                            color: tile.swatch !== "" ? tile.swatch : "transparent"
                            border.width: 1
                            border.color: Util.alpha(root.foreground, 0.25)
                          }

                          Text {
                            id: nameLabel
                            textFormat: Text.PlainText
                            width: nameRow.width
                              - (swatchDot.visible ? swatchDot.width + nameRow.spacing : 0)
                              - (renameButton.width + nameRow.spacing)
                            text: tile.name
                            color: nameTap.containsMouse || tile.active ? Color.accent : root.foreground
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.bodySmall
                            font.bold: tile.active
                            elide: Text.ElideRight
                            anchors.verticalCenter: parent.verticalCenter

                            Behavior on color { ColorAnimation { duration: 120 } }

                            MouseArea {
                              id: nameTap
                              anchors.fill: parent
                              hoverEnabled: true
                              cursorShape: Qt.IBeamCursor
                              onClicked: root.startEdit(tile.profileIndex, "name")
                            }
                          }

                          TileButton {
                            id: renameButton
                            anchors.verticalCenter: parent.verticalCenter
                            glyph: "\uf040"
                            tooltipHot: tile.hot
                            canTap: tile.profileIndex >= 0
                            onActivated: root.startEdit(tile.profileIndex, "name")
                          }
                        }

                        Loader {
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          active: tile.editingName

                          sourceComponent: TileEditor {
                            owningIndex: tile.profileIndex
                            owningField: "name"
                            seed: tile.name
                            maxChars: 20
                            onCommitted: function(value) { root.commitName(tile.profileIndex, value) }
                          }
                        }
                      }

                      // ----- Temperature -----
                      Item {
                        width: parent.width
                        implicitHeight: Math.max(lowerStep.implicitHeight, tempReadout.implicitHeight)

                        TileButton {
                          id: lowerStep
                          visible: !tile.editingTemp
                          anchors.left: parent.left
                          anchors.verticalCenter: parent.verticalCenter
                          glyph: "−"
                          canTap: root.canStepTemp(tile.profileIndex, tile.modelData.temp_f, -root.tempStepF)
                          onActivated: root.stepTemp(tile.profileIndex, tile.modelData.temp_f, -root.tempStepF)
                        }

                        Item {
                          visible: !tile.editingTemp
                          anchors.centerIn: parent
                          width: Math.max(tempReadout.implicitWidth + Style.space(10), Style.space(34))
                          height: parent.height

                          Text {
                            id: tempReadout
                            textFormat: Text.PlainText
                            anchors.centerIn: parent
                            text: {
                              var t = root.formatTemp(tile.tempF, undefined)
                              return t !== "" ? t : "—"
                            }
                            color: tempTapMouse.containsMouse ? Color.accent
                              : (tile.active ? root.foreground : root.dim)
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.bodySmall

                            Behavior on color { ColorAnimation { duration: 120 } }
                          }

                          MouseArea {
                            id: tempTapMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.IBeamCursor
                            onClicked: root.startEdit(tile.profileIndex, "temp")
                          }
                        }

                        TileButton {
                          id: raiseStep
                          visible: !tile.editingTemp
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          glyph: "+"
                          canTap: root.canStepTemp(tile.profileIndex, tile.modelData.temp_f, root.tempStepF)
                          onActivated: root.stepTemp(tile.profileIndex, tile.modelData.temp_f, root.tempStepF)
                        }

                        Loader {
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          active: tile.editingTemp

                          sourceComponent: TileEditor {
                            owningIndex: tile.profileIndex
                            owningField: "temp"
                            digitsOnly: true
                            horizontalAlignment: TextInput.AlignHCenter
                            seed: isFinite(tile.tempF)
                              ? String(Math.round(root.celsius ? root.fToC(tile.tempF) : tile.tempF))
                              : ""
                            onCommitted: function(value) { root.commitTemp(tile.profileIndex, value) }
                          }
                        }
                      }

                      // ----- Heat time -----
                      Item {
                        width: parent.width
                        implicitHeight: Math.max(lowerTime.implicitHeight, timeReadout.implicitHeight)

                        TileButton {
                          id: lowerTime
                          visible: !tile.editingTime
                          anchors.left: parent.left
                          anchors.verticalCenter: parent.verticalCenter
                          glyph: "−"
                          canTap: root.canStepTime(tile.profileIndex, tile.modelData.time, -root.timeStepS)
                          onActivated: root.stepTime(tile.profileIndex, tile.modelData.time, -root.timeStepS)
                        }

                        Item {
                          visible: !tile.editingTime
                          anchors.centerIn: parent
                          width: Math.max(timeReadout.implicitWidth + Style.space(10), Style.space(34))
                          height: parent.height

                          Text {
                            id: timeReadout
                            textFormat: Text.PlainText
                            anchors.centerIn: parent
                            text: isFinite(tile.timeS) ? Math.round(tile.timeS) + "s" : "—"
                            color: timeTapMouse.containsMouse ? Color.accent
                              : (tile.active ? root.foreground : root.dim)
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.bodySmall

                            Behavior on color { ColorAnimation { duration: 120 } }
                          }

                          MouseArea {
                            id: timeTapMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.IBeamCursor
                            onClicked: root.startEdit(tile.profileIndex, "time")
                          }
                        }

                        TileButton {
                          id: raiseTime
                          visible: !tile.editingTime
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          glyph: "+"
                          canTap: root.canStepTime(tile.profileIndex, tile.modelData.time, root.timeStepS)
                          onActivated: root.stepTime(tile.profileIndex, tile.modelData.time, root.timeStepS)
                        }

                        Loader {
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          active: tile.editingTime

                          sourceComponent: TileEditor {
                            owningIndex: tile.profileIndex
                            owningField: "time"
                            digitsOnly: true
                            horizontalAlignment: TextInput.AlignHCenter
                            seed: isFinite(tile.timeS) ? String(Math.round(tile.timeS)) : ""
                            onCommitted: function(value) { root.commitTime(tile.profileIndex, value) }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }

            Section {
              visible: root.hasProfiles && root.currentProfile >= 0
              title: "VAPOR"

              Segmented {
                width: parent.width
                options: root.vaporLevels
                value: root.profileVapor(root.currentProfile,
                  (root.activeProfile && root.activeProfile.vapor) || "")
                onPicked: function(value) { root.applyVapor(value) }
              }
            }

            Section {
              id: boostSection
              visible: root.hasProfiles && root.currentProfile >= 0
              title: "BOOST"

              readonly property var active: root.activeProfile || ({})

              StepperRow {
                width: parent.width
                label: "Extra temperature"
                valueText: root.formatBoostTemp(root.profileBoostTempF(root.currentProfile, boostSection.active.boost_temp_f))
                canLower: root.canStepBoostTemp(root.currentProfile, boostSection.active.boost_temp_f, -root.boostTempStepF)
                canRaise: root.canStepBoostTemp(root.currentProfile, boostSection.active.boost_temp_f, root.boostTempStepF)
                onLower: root.stepBoostTemp(root.currentProfile, boostSection.active.boost_temp_f, -root.boostTempStepF)
                onRaise: root.stepBoostTemp(root.currentProfile, boostSection.active.boost_temp_f, root.boostTempStepF)
              }

              StepperRow {
                width: parent.width
                label: "Extra time"
                valueText: "+" + Math.round(root.profileBoostTime(root.currentProfile, boostSection.active.boost_time)) + "s"
                canLower: root.canStepBoostTime(root.currentProfile, boostSection.active.boost_time, -root.boostTimeStepS)
                canRaise: root.canStepBoostTime(root.currentProfile, boostSection.active.boost_time, root.boostTimeStepS)
                onLower: root.stepBoostTime(root.currentProfile, boostSection.active.boost_time, -root.boostTimeStepS)
                onRaise: root.stepBoostTime(root.currentProfile, boostSection.active.boost_time, root.boostTimeStepS)
              }
            }
          }

          // ================================================== Lights
          Column {
            id: lightsPage
            width: parent.width
            visible: root.onLights && root.connected
            spacing: Style.spacing.panelGap

            Section {
              title: "LANTERN"

              SwitchRow {
                width: parent.width
                label: "Lantern"
                checked: root.lanternOn
                onToggled: root.toggleLantern()
              }

              SwitchRow {
                width: parent.width
                label: "Stealth mode"
                checked: root.stealthOn
                onToggled: root.toggleStealth()
              }

              Item {
                width: parent.width
                implicitHeight: Math.max(autoOffLabel.implicitHeight, autoOffGroup.implicitHeight)

                Text {
                  id: autoOffLabel
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: "Auto-off"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Segmented {
                  id: autoOffGroup
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(200)
                  compact: true
                  options: root.lanternTimeouts.map(function(s) {
                    return { "value": String(s), "label": root.formatLanternTimeout(s) }
                  })
                  value: String(root.lanternTimeoutSec)
                  onPicked: function(value) { root.setLanternTimeout(Number(value)) }
                }
              }

              Column {
                width: parent.width
                spacing: Style.space(4)

                Item {
                  width: parent.width
                  implicitHeight: brightnessLabel.implicitHeight

                  Text {
                    id: brightnessLabel
                    anchors.left: parent.left
                    textFormat: Text.PlainText
                    text: "Brightness"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Text {
                    anchors.right: parent.right
                    textFormat: Text.PlainText
                    text: Math.round(root.brightnessLevel / 255 * 100) + "%"
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }
                }

                PanelSlider {
                  width: parent.width
                  bar: root.bar
                  minimum: 0
                  maximum: 255
                  step: 5
                  integer: true
                  value: root.brightnessLevel
                  onMoved: function(v) { root.setBrightness(v) }
                  onReleased: function(v) { root.setBrightness(v) }
                }
              }
            }

            Section {
              title: root.activeProfile
                ? "COLOR · " + String(root.cleanName(root.activeProfile.name) || ("Profile " + (root.currentProfile + 1))).toUpperCase()
                : "COLOR"

              Grid {
                id: colorGrid
                width: parent.width
                columns: 8
                rowSpacing: Style.space(6)
                columnSpacing: Style.space(6)
                readonly property real cell: (width - columnSpacing * 7) / 8

                Repeater {
                  model: root.colorPalette

                  Rectangle {
                    required property var modelData
                    readonly property bool picked: root.activeProfile
                      && String(root.profileSwatch(root.activeProfile.color)).toLowerCase() === String(modelData).toLowerCase()
                    width: colorGrid.cell
                    height: colorGrid.cell
                    radius: width / 2
                    color: String(modelData)
                    border.width: picked ? 2 : 1
                    border.color: picked ? Color.accent : Util.alpha(root.foreground, 0.35)

                    MouseArea {
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.applyLightColor(String(modelData))
                    }
                  }
                }
              }

              Segmented {
                width: parent.width
                options: root.lightStyles
                value: root.activeStyle
                onPicked: function(value) { root.applyStyle(value) }
              }
            }

            Section {
              title: "PRESETS"

              Grid {
                id: moodGrid
                width: parent.width
                columns: 2
                rowSpacing: Style.spacing.controlGap
                columnSpacing: Style.spacing.controlGap
                readonly property real cellWidth: (width - columnSpacing) / 2

                Repeater {
                  model: root.exclusiveMoods

                  BorderSurface {
                    id: moodCard
                    required property var modelData
                    readonly property var mood: modelData
                    readonly property bool picked: root.activeMood === mood.id
                    width: moodGrid.cellWidth
                    implicitHeight: Math.max(Style.spacing.controlHeight, moodRow.implicitHeight + Style.spacing.controlPaddingY * 2)
                    radius: Style.cornerRadius
                    color: picked ? Style.selectedFillFor(Color.accent, Color.accent)
                      : moodMouse.containsMouse ? Style.hoverFillFor(root.foreground, Color.accent)
                      : Style.normalFillFor(root.foreground, Color.accent)
                    borderSpec: Border.controlSpec(picked ? "selected" : (moodMouse.containsMouse ? "hover-cursor" : "normal"), root.foreground, Color.accent)

                    Row {
                      id: moodRow
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.leftMargin: Style.spacing.controlPaddingX
                      anchors.rightMargin: Style.spacing.controlPaddingX
                      spacing: Style.spacing.md

                      Row {
                        id: moodSwatches
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: -Style.space(3)

                        Repeater {
                          model: moodCard.mood.colors.slice(0, 3)
                          Rectangle {
                            required property var modelData
                            width: Style.space(10)
                            height: Style.space(10)
                            radius: width / 2
                            color: String(modelData)
                            border.width: 1
                            border.color: Util.alpha(root.foreground, 0.25)
                          }
                        }
                      }

                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: moodRow.width - moodSwatches.width - moodRow.spacing
                        textFormat: Text.PlainText
                        text: moodCard.mood.label
                        color: moodCard.picked ? Color.accent : root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.bodySmall
                        font.bold: moodCard.picked
                        elide: Text.ElideRight
                      }
                    }

                    MouseArea {
                      id: moodMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.applyMood(moodCard.mood.id)
                    }
                  }
                }
              }
            }
          }

          // ================================================== Usage
          Column {
            id: usagePage
            width: parent.width
            visible: root.onUsage && root.connected
            spacing: Style.spacing.panelGap

            Row {
              id: summaryRow
              width: parent.width
              spacing: Style.spacing.controlGap
              readonly property real cellWidth: (width - spacing * 3) / 4

              SummaryCell { width: summaryRow.cellWidth; title: "Today"; value: root.countLabel(root.telemetry.today) }
              SummaryCell { width: summaryRow.cellWidth; title: "Week"; value: root.countLabel(root.telemetry.this_week) }
              SummaryCell { width: summaryRow.cellWidth; title: "Month"; value: root.countLabel(root.telemetry.this_month) }
              SummaryCell { width: summaryRow.cellWidth; title: "Lifetime"; value: root.countLabel(root.statusData.total_dabs) }
            }

            Section {
              title: "DAILY"
              trailing: "Avg " + (Number(root.telemetry.avg_per_day) || 0) + "/day"

              Item {
                width: parent.width
                height: Style.space(68)

                Row {
                  id: chartRow
                  anchors.fill: parent
                  spacing: Math.max(1, Style.space(2))

                  Repeater {
                    model: root.dailySeries

                    Item {
                      required property var modelData
                      required property int index
                      width: {
                        var n = Math.max(1, root.dailySeries.length)
                        return (chartRow.width - chartRow.spacing * (n - 1)) / n
                      }
                      height: chartRow.height

                      readonly property int count: Number(modelData.count) || 0
                      readonly property bool isToday: index === root.dailySeries.length - 1

                      Rectangle {
                        width: parent.width
                        height: Math.max(Style.space(2), parent.height * (parent.count / root.chartPeak))
                        anchors.bottom: parent.bottom
                        radius: Math.min(2, Style.cornerRadius)
                        color: parent.isToday ? Color.accent
                          : parent.count > 0 ? Util.alpha(Color.accent, 0.6)
                          : Util.alpha(root.foreground, 0.12)
                      }
                    }
                  }
                }
              }

              Item {
                width: parent.width
                implicitHeight: firstDay.implicitHeight

                Text {
                  id: firstDay
                  anchors.left: parent.left
                  textFormat: Text.PlainText
                  text: root.dailySeries.length ? String(root.dailySeries[0].day || "") : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Text {
                  anchors.right: parent.right
                  textFormat: Text.PlainText
                  text: root.dailySeries.length ? String(root.dailySeries[root.dailySeries.length - 1].day || "") : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }

            Section {
              title: "HABITS"

              Grid {
                width: parent.width
                columns: 2
                rowSpacing: Style.spacing.controlGap
                columnSpacing: Style.spacing.controlGap

                readonly property real cellWidth: (width - columnSpacing) / 2

                StatCard {
                  width: parent.cellWidth
                  title: "Streak"
                  value: (Number(root.telemetry.streak) || 0) + "d"
                  meta: (Number(root.telemetry.streak_best) || 0) > 0
                    ? "Best " + Math.round(Number(root.telemetry.streak_best)) + "d"
                    : "No streak yet"

                  Row {
                    spacing: Style.space(4)
                    Repeater {
                      model: root.weekdaySeries
                      Rectangle {
                        required property var modelData
                        width: Style.space(8)
                        height: Style.space(8)
                        radius: width / 2
                        color: Number(modelData.count) > 0 ? Color.accent : "transparent"
                        border.width: modelData.today ? 1 : (Number(modelData.count) > 0 ? 0 : 1)
                        border.color: modelData.today ? Color.accent : Util.alpha(root.foreground, 0.3)
                      }
                    }
                  }
                }

                StatCard {
                  width: parent.cellWidth
                  title: "Peak hour"
                  value: root.formatHour(root.telemetry.top_hour)
                  meta: Number(root.telemetry.top_hour_share) > 0
                    ? Math.round(Number(root.telemetry.top_hour_share) * 100) + "% of sessions"
                    : "Not enough data"

                  Item {
                    width: parent.width
                    height: Style.space(22)

                    Row {
                      id: hourChart
                      anchors.fill: parent
                      spacing: 1

                      Repeater {
                        model: 24
                        Item {
                          required property int index
                          width: (hourChart.width - 23) / 24
                          height: hourChart.height

                          Rectangle {
                            width: parent.width
                            height: {
                              var c = Number(root.hourSeries[parent.index]) || 0
                              return Math.max(Style.space(2), parent.height * (c / root.hourPeak))
                            }
                            anchors.bottom: parent.bottom
                            radius: 1
                            color: parent.index === Number(root.telemetry.top_hour)
                              ? Color.accent
                              : Util.alpha(root.foreground, (Number(root.hourSeries[parent.index]) || 0) > 0 ? 0.45 : 0.12)
                          }
                        }
                      }
                    }
                  }
                }

                StatCard {
                  width: parent.cellWidth
                  title: "Avg duration"
                  value: root.formatDuration(root.telemetry.avg_time_s)
                  meta: root.telemetry.avg_time_s == null ? "Not enough data" : "Per session"
                }

                StatCard {
                  width: parent.cellWidth
                  title: "Avg temperature"
                  value: root.formatAvgTemp(root.telemetry.avg_temp_f)
                  meta: root.telemetry.avg_temp_f == null ? "Not enough data" : "Per session"
                }
              }
            }

            Section {
              visible: root.colorSeries.length > 0
              title: "TOP COLORS"

              Row {
                width: parent.width
                spacing: Style.space(3)
                Repeater {
                  model: root.colorSeries
                  Rectangle {
                    required property var modelData
                    height: Style.space(8)
                    width: Math.max(Style.space(16), (parent.width - parent.spacing * Math.max(0, root.colorSeries.length - 1)) / Math.max(1, root.colorSeries.length))
                    radius: 2
                    color: String(modelData)
                  }
                }
              }
            }
          }

          // ================================================== Device
          Column {
            id: devicePage
            width: parent.width
            visible: root.onDevice && root.connected
            spacing: Style.spacing.panelGap

            Section {
              title: "NAME"

              Item {
                width: parent.width
                implicitHeight: Math.max(Style.space(28), deviceNameRow.implicitHeight)

                Row {
                  id: deviceNameRow
                  visible: !(root.editIndex === -1 && root.editField === "device")
                  width: parent.width
                  spacing: Style.spacing.md
                  anchors.verticalCenter: parent.verticalCenter

                  Text {
                    width: parent.width - deviceRename.width - parent.spacing
                    textFormat: Text.PlainText
                    text: root.shownDeviceName
                    color: deviceNameTap.containsMouse ? Color.accent : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter

                    MouseArea {
                      id: deviceNameTap
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.IBeamCursor
                      onClicked: root.startEdit(-1, "device")
                    }
                  }

                  TileButton {
                    id: deviceRename
                    anchors.verticalCenter: parent.verticalCenter
                    glyph: "\uf040"
                    canTap: true
                    onActivated: root.startEdit(-1, "device")
                  }
                }

                Loader {
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  active: root.editIndex === -1 && root.editField === "device"

                  sourceComponent: TileEditor {
                    owningIndex: -1
                    owningField: "device"
                    seed: root.shownDeviceName
                    maxChars: 32
                    onCommitted: function(value) { root.commitDeviceName(value) }
                  }
                }
              }
            }

            Section {
              title: "DETAILS"

              InfoRow { width: parent.width; label: "Model"; value: (root.statusData.product && root.statusData.product.label) || "" }
              InfoRow { width: parent.width; label: "Chamber"; value: root.chamberLabel }
              InfoRow { width: parent.width; label: "Battery"; value: root.batteryDetail }
              InfoRow { width: parent.width; label: "Dabs left on charge"; value: root.remainingLabel }
              InfoRow {
                width: parent.width
                label: "Firmware"
                value: {
                  var fw = String(root.statusData.firmware || "")
                  var boot = String(root.statusData.bootloader || "")
                  return fw !== "" && boot !== "" ? fw + " (bootloader " + boot + ")" : fw
                }
              }
              InfoRow { width: parent.width; label: "Serial"; value: String(root.statusData.serial || "") }
              InfoRow { width: parent.width; label: "First used"; value: String(root.statusData.birthday_label || "") }
              InfoRow { width: parent.width; label: "Uptime"; value: String(root.statusData.uptime || "") }
            }

            Section {
              title: "SHOW ON DEVICE"

              Row {
                width: parent.width
                spacing: Style.spacing.controlGap

                ActionButton {
                  width: (parent.width - parent.spacing) / 2
                  label: "Battery level"
                  onActivated: root.run("omapuffco battery")
                }
                ActionButton {
                  width: (parent.width - parent.spacing) / 2
                  label: "Firmware version"
                  onActivated: root.run("omapuffco version")
                }
              }
            }

            Section {
              title: "POWER"

              Row {
                width: parent.width
                spacing: Style.spacing.controlGap

                ActionButton {
                  width: (parent.width - parent.spacing) / 2
                  label: "Sleep"
                  glyph: "\uf186"
                  onActivated: root.run("omapuffco sleep")
                }
                ActionButton {
                  width: (parent.width - parent.spacing) / 2
                  label: "Power off"
                  glyph: "\uf011"
                  tint: root.urgent
                  onActivated: root.confirmPowerOff = true
                }
              }
            }
          }
        }
      }

      ConfirmDialog {
        anchors.fill: parent
        z: 10
        opened: root.confirmPowerOff
        message: "Power off " + root.deviceName + "?"
        confirmText: "Power off"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onCanceled: root.confirmPowerOff = false
        onConfirmed: {
          root.confirmPowerOff = false
          root.run("omapuffco off")
        }
      }
    }
  }

  // Titled block: every group of controls gets the same small-caps header
  // and spacing, which is most of what makes the pages read as one system.
  component Section: Column {
    id: section

    property string title: ""
    property string trailing: ""
    default property alias body: sectionBody.data

    width: parent ? parent.width : 0
    spacing: Style.space(8)

    Item {
      width: parent.width
      implicitHeight: sectionHeader.implicitHeight

      PanelSectionHeader {
        id: sectionHeader
        anchors.left: parent.left
        text: section.title
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Text {
        visible: section.trailing !== ""
        anchors.right: parent.right
        anchors.bottom: sectionHeader.bottom
        textFormat: Text.PlainText
        text: section.trailing
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    Column {
      id: sectionBody
      width: parent.width
      spacing: Style.space(8)
    }
  }

  // Equal-width, mutually exclusive choice row built from the shell's own
  // Button, so tabs and option pickers share the first-party chip styling.
  component Segmented: Row {
    id: seg

    property var options: []
    property string value: ""
    property bool compact: false

    signal picked(string value)

    spacing: compact ? Style.space(4) : Style.spacing.controlGap
    readonly property real cellWidth: options.length > 0
      ? (width - spacing * (options.length - 1)) / options.length
      : 0

    Repeater {
      model: seg.options

      Button {
        required property var modelData
        width: seg.cellWidth
        text: String(modelData.label)
        selected: String(modelData.value) === seg.value
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        fontSize: seg.compact ? Style.font.caption : Style.font.bodySmall
        onClicked: seg.picked(String(modelData.value))
      }
    }
  }

  component SwitchRow: Item {
    id: switchRow

    property string label: ""
    property bool checked: false

    signal toggled()

    implicitHeight: Math.max(switchLabel.implicitHeight, switchControl.implicitHeight)

    Text {
      id: switchLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: switchRow.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    ToggleSwitch {
      id: switchControl
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      checked: switchRow.checked
      cursorRing: false
      foreground: root.foreground
      onToggled: switchRow.toggled()
    }
  }

  component StepperRow: Item {
    id: stepper

    property string label: ""
    property string valueText: ""
    property bool canLower: true
    property bool canRaise: true

    signal lower()
    signal raise()

    implicitHeight: Math.max(Style.space(24), stepperLabel.implicitHeight)

    Text {
      id: stepperLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: stepper.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Row {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.spacing.xs

      TileButton {
        anchors.verticalCenter: parent.verticalCenter
        glyph: "−"
        canTap: stepper.canLower
        onActivated: stepper.lower()
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(52)
        horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText
        text: stepper.valueText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      TileButton {
        anchors.verticalCenter: parent.verticalCenter
        glyph: "+"
        canTap: stepper.canRaise
        onActivated: stepper.raise()
      }
    }
  }

  component InfoRow: Item {
    id: info

    property string label: ""
    property string value: ""

    visible: value !== ""
    implicitHeight: Math.max(infoLabel.implicitHeight, infoValue.implicitHeight)

    Text {
      id: infoLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: info.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      id: infoValue
      anchors.left: infoLabel.right
      anchors.right: parent.right
      anchors.leftMargin: Style.spacing.md
      anchors.verticalCenter: parent.verticalCenter
      horizontalAlignment: Text.AlignRight
      textFormat: Text.PlainText
      text: info.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }

  component SummaryCell: BorderSurface {
    id: cell

    property string title: ""
    property string value: ""

    radius: Style.cornerRadius
    color: Style.normalFillFor(root.foreground, Color.accent)
    borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
    implicitHeight: cellCol.implicitHeight + Style.spacing.controlPaddingY * 2

    Column {
      id: cellCol
      anchors.centerIn: parent
      spacing: Style.space(2)

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        textFormat: Text.PlainText
        text: cell.value
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
      }
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        textFormat: Text.PlainText
        text: cell.title
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // Compact metric tile: title, big value, caption, optional extra
  // (weekday dots, hour histogram).
  component StatCard: BorderSurface {
    id: metric

    property string title: ""
    property string value: ""
    property string meta: ""
    default property alias extra: extraSlot.data

    radius: Style.cornerRadius
    color: Style.normalFillFor(root.foreground, Color.accent)
    borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
    implicitHeight: metricCol.implicitHeight + Style.spacing.controlPaddingY * 2

    Column {
      id: metricCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.rightMargin: Style.spacing.controlPaddingX
      anchors.topMargin: Style.spacing.controlPaddingY
      spacing: Style.space(4)

      Text {
        textFormat: Text.PlainText
        text: metric.title
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
      Text {
        textFormat: Text.PlainText
        text: metric.value
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
      }
      Text {
        visible: metric.meta !== ""
        textFormat: Text.PlainText
        text: metric.meta
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
        width: parent.width
      }
      Column {
        id: extraSlot
        width: parent.width
        spacing: Style.space(6)
      }
    }
  }

  // Inline field for a profile tile. Commits on Enter, abandons on Escape or
  // on losing focus — the identity guard means a blur fired while this field
  // is torn down can't cancel whichever edit replaced it.
  component TileEditor: TextField {
    id: editor

    property int owningIndex: -1
    property string owningField: ""
    property string seed: ""
    property bool digitsOnly: false
    property int maxChars: 0
    // Ignore the blur that fires while the field is still taking focus, or
    // the editor vanishes the instant it appears.
    property bool armed: false

    signal committed(string value)

    foreground: root.foreground
    accent: Color.accent
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    horizontalPadding: Style.spacing.xs
    verticalPadding: 0
    inputMethodHints: digitsOnly ? Qt.ImhDigitsOnly : Qt.ImhNone
    maximumLength: maxChars > 0 ? maxChars : 32767
    placeholderText: digitsOnly ? "" : "Name"

    Component.onCompleted: {
      text = seed
      Qt.callLater(function() {
        editor.selectAll()
        editor.forceActiveFocus()
        editor.armed = true
      })
    }

    onAccepted: editor.committed(editor.text)
    Keys.onEscapePressed: function(event) {
      root.cancelEditFor(editor.owningIndex, editor.owningField)
      event.accepted = true
    }
    onActiveFocusChanged: {
      if (!armed || activeFocus) return
      var index = editor.owningIndex
      var field = editor.owningField
      Qt.callLater(function() { root.cancelEditFor(index, field) })
    }
  }

  // Borderless nudge/rename target that only resolves into a control under
  // the cursor, so the profile grid reads as four tiles, not a dozen buttons.
  component TileButton: Item {
    id: tap

    property string glyph: ""
    property bool canTap: true
    // When set, the button only paints once the row it belongs to is hovered.
    property var tooltipHot: undefined

    signal activated()

    readonly property bool hovered: tapMouse.containsMouse
    readonly property bool hot: canTap && hovered
    readonly property bool revealed: tooltipHot === undefined || tooltipHot === true

    implicitWidth: Style.space(18)
    implicitHeight: Style.space(18)
    opacity: (canTap ? 1 : 0.35) * (revealed ? 1 : 0)

    Behavior on opacity { NumberAnimation { duration: 120 } }

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: tap.hot ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"

      Behavior on color { ColorAnimation { duration: 100 } }
    }

    Text {
      textFormat: Text.PlainText
      anchors.centerIn: parent
      text: tap.glyph
      color: tap.hot ? Color.accent : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    MouseArea {
      id: tapMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: tap.canTap ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: if (tap.canTap) tap.activated()
    }
  }

  // Outlined chip: neutral at rest, tinted on hover/press, softly filled while
  // it's the action the current state calls for. `tint` makes Stop read as
  // destructive (urgent) and Heat as primary (accent).
  component ActionButton: BorderSurface {
    id: chip

    property string label: ""
    property string glyph: ""
    property color tint: root.foreground
    property bool emphasized: false

    signal activated()

    readonly property bool hot: chipMouse.containsMouse
    readonly property bool lit: hot || emphasized

    implicitHeight: Math.max(Style.spacing.controlHeight,
                             chipRow.implicitHeight + Style.spacing.controlPaddingY * 2)
    radius: Style.cornerRadius

    color: chipMouse.pressed ? Style.pressedFillFor(tint, tint)
      : hot ? Style.hoverFillFor(tint, tint)
      : emphasized ? Style.selectedFillFor(tint, tint)
      : Style.normalFillFor(tint, tint)

    borderSpec: hot
      ? Border.controlSpec("hover-cursor", tint, tint)
      : Border.controlSpec("normal", tint, tint)

    Behavior on color { ColorAnimation { duration: 120 } }

    Row {
      id: chipRow
      anchors.centerIn: parent
      spacing: Style.spacing.md

      Text {
        textFormat: Text.PlainText
        visible: chip.glyph !== ""
        text: chip.glyph
        color: chip.lit ? chip.tint : root.foreground
        font.family: root.fontFamily
        font.pixelSize: chip.label === "" ? Style.font.icon : Style.font.iconSmall
        anchors.verticalCenter: parent.verticalCenter

        Behavior on color { ColorAnimation { duration: 120 } }
      }

      Text {
        textFormat: Text.PlainText
        visible: chip.label !== ""
        text: chip.label
        color: chip.lit ? chip.tint : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: chip.emphasized
        anchors.verticalCenter: parent.verticalCenter

        Behavior on color { ColorAnimation { duration: 120 } }
      }
    }

    MouseArea {
      id: chipMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: chip.activated()
    }
  }
}
