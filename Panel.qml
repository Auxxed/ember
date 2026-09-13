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
      pendingSaver = undefined
      pendingQtip = undefined
      pendingCleanEvery = -1
      pendingBrightness = -1
      pendingDeviceName = ""
      confirmPowerOff = false
      page = "control"
      deviceTab = "info"
      usageTab = "stats"
      noteKey = ""
      pendingDailyLimit = -1
      pendingRecap = undefined
      pendingPreserve = undefined
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
    var eta = formatEta(statusData.charge_eta_s)
    if (eta !== "") bits.push("full in " + eta)
    return bits.join(" · ")
  }

  function formatEta(seconds) {
    var n = Number(seconds)
    if (seconds === null || seconds === undefined || !isFinite(n) || n <= 0) return ""
    var mins = Math.max(1, Math.round(n / 60))
    if (mins < 60) return mins + " min"
    var h = Math.floor(mins / 60)
    var m = mins % 60
    return h + " h" + (m ? " " + m + " min" : "")
  }

  // Capacity the Peak's fuel gauge has learned, against the battery's rated size.
  readonly property int batteryRated: Number(statusData.battery_rated_mah) || 1700
  readonly property string batteryHealthLabel: {
    var raw = statusData.battery_capacity_mah
    var mah = Number(raw)
    if (!connected || !raw || !isFinite(mah)) return ""
    // Use the daemon's figure so the panel and `omapuffco status` agree.
    var daemonPct = Number(statusData.battery_health_pct)
    if (statusData.battery_health_pct !== null && isFinite(daemonPct)) return daemonPct + "%"
    return Math.min(100, Math.floor(mah / batteryRated * 100 + 0.5)) + "%"
  }
  readonly property string batteryCapacityLabel: {
    var raw = statusData.battery_capacity_mah
    var mah = Number(raw)
    if (!connected || !raw || !isFinite(mah)) return ""
    return Math.round(mah) + " of " + batteryRated + " mAh"
  }

  property var pendingPreserve: undefined
  readonly property bool preserveSupported: statusData.max_charge !== null && statusData.max_charge !== undefined
  readonly property bool preserveOn: pendingPreserve !== undefined
    ? pendingPreserve === true
    : Number(statusData.max_charge) < 100

  function togglePreserve() {
    var next = !preserveOn
    pendingPreserve = next
    run("omapuffco preserve " + (next ? "on" : "off"))
  }

  // The Peak refuses to heat near 5%; warn a little before that.
  readonly property bool lowHeatBattery: connected && !pluggedIn && Number(statusData.battery) <= 10
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
  readonly property var profileUsage: telemetry.profiles || []

  function profileByIndex(index) {
    for (var i = 0; i < profiles.length; i++) {
      if (Number(profiles[i].index) === Number(index)) return profiles[i]
    }
    return null
  }

  function profileUsageName(index) {
    if (Number(index) < 0) return "Custom temperature"
    var p = profileByIndex(index)
    return p && p.name ? String(p.name) : "Profile " + (Number(index) + 1)
  }

  function profileUsageColor(index) {
    var p = profileByIndex(index)
    return p && typeof p.color === "string" && p.color.charAt(0) === "#" ? p.color : Color.accent
  }
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

  // Heat timer: the Peak reports seconds spent in the current state and the
  // state's planned length. Between polls the panel ticks the elapsed time
  // forward itself, so the ring moves smoothly instead of jumping every 2 s.
  property real timerSampledAt: 0
  property real nowMs: Date.now()
  readonly property real stateTotalS: {
    var n = Number(statusData.state_total_s)
    return statusData.state_total_s !== null && isFinite(n) && n > 0 ? n : NaN
  }
  readonly property real stateElapsedS: {
    var n = Number(statusData.state_elapsed_s)
    if (statusData.state_elapsed_s === null || !isFinite(n)) return NaN
    return n + Math.max(0, nowMs - timerSampledAt) / 1000
  }
  readonly property bool timerActive: (preheating || atTemp)
    && isFinite(stateTotalS) && isFinite(stateElapsedS)
  readonly property real timerProgress: timerActive
    ? Math.max(0, Math.min(1, stateElapsedS / stateTotalS)) : 0
  readonly property int timerSecondsLeft: timerActive
    ? Math.max(0, Math.ceil(stateTotalS - stateElapsedS)) : 0

  // Fault log: read on request, since the first read walks hundreds of entries.
  property var faultLog: null
  property bool faultsLoading: false
  property bool faultError: false
  property int faultShown: 8

  property bool connecting: false
  property string connectError: ""
  property bool connectFailed: false
  property bool scanning: false
  // null until Find nearby Peaks has run.
  property var nearbyPeaks: null
  readonly property string connectMessage: connectError !== ""
    ? connectError
    : (connectFailed ? "Couldn't connect. Wake the Peak, keep it close, and try again." : "")
  // Set when `omapuffco` isn't installed or its daemon isn't running, e.g. right
  // after `omarchy plugin add` without install.sh.
  property bool needsSetup: false
  readonly property string installScript: Qt.resolvedUrl("install.sh").toString().replace(/^file:\/\//, "")
  property bool confirmPowerOff: false
  property var pendingStealth: undefined
  property var pendingLantern: undefined
  property var pendingSaver: undefined
  property var pendingQtip: undefined
  property int pendingCleanEvery: -1
  property int pendingBrightness: -1
  property string page: "control"
  property string pendingDeviceName: ""

  readonly property bool onControl: page === "control"
  readonly property bool onLights: page === "lights"
  readonly property bool onUsage: page === "usage"
  readonly property bool onCare: page === "care"
  readonly property bool onDevice: page === "device"

  readonly property var pageOptions: [
    { "value": "control", "label": "Control" },
    { "value": "lights", "label": "Lights" },
    { "value": "usage", "label": "Usage" },
    // A dot on Care while the chamber is due a clean, so it isn't missed from Control.
    { "value": "care", "label": cleanDue ? "Care \u2022" : "Care" },
    { "value": "device", "label": "Device" }
  ]

  property string deviceTab: "info"
  property string usageTab: "stats"
  readonly property bool onUsageStats: onUsage && usageTab === "stats"
  readonly property bool onUsageHistory: onUsage && usageTab === "history"
  readonly property var usageTabOptions: [
    { "value": "stats", "label": "Stats" },
    { "value": "history", "label": "History" }
  ]

  // History: recent dabs with notes, read on demand.
  property var sessionList: null
  property int sessionTotal: 0
  property bool sessionsLoading: false
  property int sessionLimit: 30
  property string noteKey: ""
  // Notes saved but not yet read back from the daemon, by session key.
  property var pendingNotes: ({})
  readonly property int trackedTotal: Number(telemetry.tracked_total) || 0
  onOnUsageHistoryChanged: if (onUsageHistory) loadSessions()
  onTrackedTotalChanged: if (onUsageHistory) loadSessions()

  property int pendingDailyLimit: -1
  readonly property int dailyLimit: pendingDailyLimit >= 0 ? pendingDailyLimit : (Number(statusData.daily_limit) || 0)
  property var pendingRecap: undefined
  readonly property bool recapOn: pendingRecap !== undefined ? pendingRecap === true : statusData.weekly_recap !== false

  function stepDailyLimit(delta) {
    var next = Math.max(0, Math.min(50, dailyLimit + delta))
    pendingDailyLimit = next
    run("omapuffco limit " + next)
  }

  function toggleRecap() {
    var next = !recapOn
    pendingRecap = next
    run("omapuffco recap " + (next ? "on" : "off"))
  }

  function loadSessions() {
    if (sessionsProc.running) return
    sessionsLoading = true
    sessionsProc.command = ["bash", "-lc", "omapuffco --json sessions --limit \"$1\"", "omapuffco-sessions", String(sessionLimit)]
    sessionsProc.running = true
  }

  function editNote(key) {
    noteKey = String(key)
  }

  function closeNote(key) {
    if (noteKey !== key) return
    noteKey = ""
    Qt.callLater(function() { if (root.opened && keyCatcher) keyCatcher.forceActiveFocus() })
  }

  function noteFor(session) {
    var key = String(session.key || "")
    return pendingNotes[key] !== undefined ? pendingNotes[key] : String(session.note || "")
  }

  function saveNote(key, raw) {
    var text = String(raw || "").replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "")
    var copy = Object.assign({}, pendingNotes)
    copy[key] = text
    pendingNotes = copy
    closeNote(key)
    runArgv(text === "" ? ["omapuffco", "note", key] : ["omapuffco", "note", key, text])
    noteReload.restart()
  }

  function sessionTitle(session) {
    var bits = []
    if (session.profile !== null && session.profile !== undefined) bits.push(profileUsageName(session.profile))
    if (session.temp_f !== null && session.temp_f !== undefined) bits.push(formatTemp(session.temp_f, session.temp_c))
    return bits.length ? bits.join(" \u00b7 ") : "Dab"
  }

  function formatSessionTime(ts) {
    return Qt.formatDateTime(new Date(Number(ts) * 1000), "ddd MMM d, h:mm AP")
  }
  readonly property bool onDeviceInfo: onDevice && deviceTab === "info"
  readonly property bool onDeviceTips: onDevice && deviceTab === "tips"
  readonly property var deviceTabOptions: [
    { "value": "info", "label": "Info" },
    { "value": "tips", "label": "Tips" }
  ]

  // Factory Peak Pro presets (Connect: Blue / Green / Red / White).
  readonly property var stockHeats: [
    { "name": "Low", "color": "Blue", "temp_f": 490, "note": "Flavor" },
    { "name": "Med", "color": "Green", "temp_f": 510, "note": "Balanced" },
    { "name": "High", "color": "Red", "temp_f": 530, "note": "Vapor" },
    { "name": "Peak", "color": "White", "temp_f": 545, "note": "Clouds" }
  ]

  readonly property var peakTips: [
    { "title": "Load small", "body": "Rice-grain on the bowl floor, not the walls." },
    { "title": "Swab while warm", "body": "Dry Q-tip after every hit. Iso only for leftover residue." },
    { "title": "Iso soak", "body": "90%+ iso, 20 minutes, when it tastes off. Never water in the chamber." },
    { "title": "Fill glass off the base", "body": "Water just above the perc slots. Empty it overnight." },
    { "title": "Slow inhale", "body": "Cap snug. Hard pulls cool the bowl and pull reclaim." },
    { "title": "Sleep it", "body": "Lock or sleep between sessions. Phone app and this panel can't share the Peak." }
  ]

  readonly property var peakLights: [
    { "title": "3 white flashes", "body": "No chamber. Reseat it." },
    { "title": "Red-white", "body": "Chamber error. Iso soak, dry, retry." },
    { "title": "Solid red", "body": "Overheating. Let it sit." }
  ]

  function applyLightColor(hex) {
    if (currentProfile < 0) return
    pendingLantern = true
    var updated = {}
    for (var key in pendingColors) updated[key] = pendingColors[key]
    updated[currentProfile] = hex
    pendingColors = updated
    // Also paints the live lantern, without reselecting the heat profile
    // (which flashes factory green over the new colour).
    runArgv(["omapuffco", "color", hex, "--index", String(currentProfile)])
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
  readonly property bool qtipOn: pendingQtip !== undefined
    ? pendingQtip === true
    : statusData.qtip_reminder === true
  readonly property bool saverOn: pendingSaver !== undefined
    ? pendingSaver === true
    : statusData.battery_saver === true
  readonly property int cleanEveryMin: 10
  readonly property int cleanEveryMax: 100
  readonly property int cleanEveryStep: 10
  readonly property int cleanEvery: {
    if (pendingCleanEvery >= 0) return pendingCleanEvery
    var n = Number(statusData.clean_every)
    return isFinite(n) && n > 0 ? Math.round(n) : 30
  }
  readonly property int cleanRemaining: {
    var rem = Number(statusData.clean_remaining)
    if (!isFinite(rem)) rem = cleanEvery
    if (pendingCleanEvery < 0) return Math.max(0, Math.round(rem))
    var every = Number(statusData.clean_every)
    if (!isFinite(every) || every <= 0) every = 30
    var used = Math.max(0, every - rem)
    return Math.max(0, pendingCleanEvery - used)
  }
  readonly property bool cleanDue: cleanRemaining <= 0
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

  function readFaults() {
    if (faultProc.running) return
    faultsLoading = true
    faultError = false
    faultProc.running = true
  }

  function closeFaults() {
    faultLog = null
    faultError = false
    faultShown = 8
  }

  function formatFaultTime(ts) {
    if (ts === null || ts === undefined) return "Before last restart"
    return Qt.formatDateTime(new Date(Number(ts) * 1000), "MMM d, h:mm AP")
  }

  function faultGlyph(code) {
    if (code === 11) return "\uf293"                               // bluetooth
    if (code >= 3 && code <= 8) return "\uf06d"                    // heater
    return "\uf243"                                                // battery
  }

  // `mac` picks one Peak from Find nearby Peaks; without it the daemon uses
  // the last Peak. Runs as a process so a failure can say why.
  function connectDevice(mac) {
    if (connectProc.running) return
    connecting = true
    connectError = ""
    connectFailed = false
    connectGiveUp.restart()
    connectProc.command = mac
      ? ["bash", "-lc", "omapuffco connect --mac \"$1\"", "omapuffco-connect", String(mac)]
      : ["bash", "-lc", "omapuffco connect"]
    connectProc.running = true
  }

  function findPeaks() {
    if (scanProc.running) return
    scanning = true
    connectFailed = false
    scanProc.running = true
  }

  // Frees the Peak's single Bluetooth link for the phone app or another
  // computer; nothing reconnects until Connect is pressed again.
  function disconnectDevice() {
    connecting = false
    connectGiveUp.stop()
    run("omapuffco disconnect")
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

  function toggleQtip() {
    var next = !qtipOn
    pendingQtip = next
    run("omapuffco qtip " + (next ? "on" : "off"))
  }

  function toggleSaver() {
    var next = !saverOn
    pendingSaver = next
    if (next) pendingLantern = false
    run("omapuffco saver " + (next ? "on" : "off"))
  }

  function clampCleanEvery(value) {
    var n = Math.round(Number(value) / cleanEveryStep) * cleanEveryStep
    return Math.max(cleanEveryMin, Math.min(cleanEveryMax, n))
  }

  function canStepCleanEvery(delta) {
    return clampCleanEvery(cleanEvery + delta) !== cleanEvery
  }

  function stepCleanEvery(delta) {
    if (!canStepCleanEvery(delta)) return
    pendingCleanEvery = clampCleanEvery(cleanEvery + delta)
    commitWriteTimer.restart()
  }

  function markCleaned() {
    pendingCleanEvery = -1
    run("omapuffco clean done")
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
    if (pendingCleanEvery >= 0)
      runArgv(["omapuffco", "clean", "--every", String(pendingCleanEvery)])
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
          root.timerSampledAt = Date.now()
          root.nowMs = root.timerSampledAt
          root.needsSetup = false
          if (root.statusData.connected === true) {
            root.connecting = false
            connectGiveUp.stop()
            root.connectError = ""
            root.connectFailed = false
            root.nearbyPeaks = null
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

  Process {
    id: connectProc
    command: ["bash", "-lc", "omapuffco connect"]
    onExited: function(exitCode) {
      root.connecting = false
      connectGiveUp.stop()
      kickTimer.restart()
      if (exitCode !== 0) root.connectFailed = true
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").trim().split("\n")
        var last = lines[lines.length - 1]
        if (last === "") return
        // Two or more Peaks in range: list them instead of guessing.
        if (/Multiple Peak/i.test(last)) {
          root.connectError = "More than one Peak is nearby. Pick yours below."
          root.findPeaks()
          return
        }
        root.connectError = last
      }
    }
  }

  Process {
    id: sessionsProc
    command: ["bash", "-lc", "omapuffco --json sessions --limit 30"]
    onExited: function(exitCode) {
      root.sessionsLoading = false
      if (exitCode !== 0 && root.sessionList === null) root.sessionList = []
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          root.sessionList = result.sessions || []
          root.sessionTotal = Number(result.total) || 0
          root.pendingNotes = ({})
        } catch (e) {
          if (root.sessionList === null) root.sessionList = []
        }
      }
    }
  }

  // Re-read after a note is saved, once the command has written it.
  Timer {
    id: noteReload
    interval: 1500
    onTriggered: root.loadSessions()
  }

  Process {
    id: scanProc
    command: ["bash", "-lc", "omapuffco --json scan --timeout 8"]
    onExited: function(exitCode) {
      root.scanning = false
      if (exitCode !== 0 && root.nearbyPeaks === null) root.nearbyPeaks = []
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          root.nearbyPeaks = JSON.parse(text).devices || []
        } catch (e) {
          root.nearbyPeaks = []
        }
      }
    }
  }

  Timer {
    id: connectGiveUp
    interval: 100000
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
      root.pendingSaver = undefined
      root.pendingCleanEvery = -1
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

  Process {
    id: faultProc
    command: ["bash", "-lc", "omapuffco --json faults"]
    onExited: function(exitCode) {
      root.faultsLoading = false
      if (exitCode !== 0) root.faultError = true
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (!text) return
        try {
          root.faultLog = JSON.parse(text).faults || []
          root.faultShown = 8
        } catch (e) {
          root.faultError = true
        }
      }
    }
  }

  Timer {
    interval: 100
    repeat: true
    running: root.opened && (root.preheating || root.atTemp)
    onTriggered: root.nowMs = Date.now()
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
              visible: root.connectMessage !== "" && !root.connecting && !root.needsSetup
              textFormat: Text.PlainText
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
              text: root.connectMessage
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            ActionButton {
              width: parent.width
              visible: !root.needsSetup
              label: root.scanning ? "Searching\u2026" : "Find nearby Peaks"
              glyph: "\uf002"
              onActivated: root.findPeaks()
            }

            Repeater {
              model: root.scanning || !root.nearbyPeaks ? [] : root.nearbyPeaks

              ActionButton {
                required property var modelData
                width: parent ? parent.width : 0
                label: String(modelData.name || "Peak Pro") + "  \u00b7  " + String(modelData.address || "")
                onActivated: root.connectDevice(modelData.address)
              }
            }

            Text {
              width: parent.width
              visible: root.nearbyPeaks !== null && !root.scanning && root.nearbyPeaks.length === 0
              textFormat: Text.PlainText
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
              text: "No Peaks found. Wake the Peak, keep it close, and disconnect the phone app."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
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

            Text {
              width: parent.width
              visible: root.lowHeatBattery && !root.heating
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              horizontalAlignment: Text.AlignHCenter
              text: "Battery " + Math.round(Number(root.statusData.battery)) + "%: the Peak may refuse to heat. Plug it in first."
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Item {
              width: parent.width
              visible: root.timerActive
              implicitHeight: timerRing.height

              TimerRing {
                id: timerRing
                anchors.left: parent.left
                width: Style.space(84)
                height: width
                progress: root.timerProgress
                fillColor: root.atTemp ? Color.accent : root.urgent

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: root.formatDuration(root.timerSecondsLeft)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                }
              }

              Column {
                anchors.left: timerRing.right
                anchors.leftMargin: Style.space(16)
                anchors.right: parent.right
                anchors.verticalCenter: timerRing.verticalCenter
                spacing: Style.space(4)

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: root.atTemp ? "Session" : "Heating up"
                  color: root.atTemp ? Color.accent : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  text: root.atTemp
                    ? root.formatDuration(root.timerSecondsLeft) + " left before the Peak cools down"
                    : "Ready in about " + root.formatDuration(root.timerSecondsLeft)
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
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
              title: "LEDS"

              SwitchRow {
                width: parent.width
                label: "LED"
                checked: root.lanternOn
                onToggled: root.toggleLantern()
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

              SwitchRow {
                width: parent.width
                label: "Stealth mode"
                checked: root.stealthOn
                onToggled: root.toggleStealth()
              }
            }

            Section {
              title: root.activeProfile
                ? "PROFILE LIGHT · " + String(root.cleanName(root.activeProfile.name) || ("Profile " + (root.currentProfile + 1))).toUpperCase()
                : "PROFILE LIGHT"

              Text {
                width: parent.width
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: "The color this profile glows with while it heats."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

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

            }

          }

          // ================================================== Care
          Column {
            id: carePage
            width: parent.width
            visible: root.onCare && root.connected
            spacing: Style.spacing.panelGap

            Section {
              title: "BATTERY"
              trailing: root.batteryHealthLabel !== "" ? root.batteryHealthLabel + " health" : ""

              SwitchRow {
                width: parent.width
                visible: root.preserveSupported
                label: "Charge to 80% only"
                checked: root.preserveOn
                onToggled: root.togglePreserve()
              }

              SwitchRow {
                width: parent.width
                label: "Sleep after sessions and 10 min idle"
                checked: root.saverOn
                onToggled: root.toggleSaver()
              }

              Text {
                width: parent.width
                visible: root.preserveSupported && root.preserveOn
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: "Stopping at 80% helps the battery last longer."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Section {
              title: "CLEANING"
              trailing: root.cleanDue ? "Due" : root.cleanRemaining + " left"

              SwitchRow {
                width: parent.width
                label: "Q-tip reminder after each dab"
                checked: root.qtipOn
                onToggled: root.toggleQtip()
              }

              StepperRow {
                width: parent.width
                label: "Remind every"
                valueText: root.cleanEvery + " dabs"
                canLower: root.canStepCleanEvery(-root.cleanEveryStep)
                canRaise: root.canStepCleanEvery(root.cleanEveryStep)
                onLower: root.stepCleanEvery(-root.cleanEveryStep)
                onRaise: root.stepCleanEvery(root.cleanEveryStep)
              }

              Text {
                width: parent.width
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: root.cleanDue
                  ? "Swab the chamber, then mark it cleaned."
                  : root.cleanRemaining + " dab" + (root.cleanRemaining === 1 ? "" : "s") + " until the reminder."
                color: root.cleanDue ? root.urgent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              ActionButton {
                width: parent.width
                label: "Mark cleaned"
                emphasized: root.cleanDue
                tint: root.cleanDue ? root.urgent : root.foreground
                onActivated: root.markCleaned()
              }
            }

            Section {
              title: "GOALS"
              trailing: root.dailyLimit > 0
                ? Number(root.telemetry.today || 0) + " of " + root.dailyLimit + " today"
                : ""

              StepperRow {
                width: parent.width
                label: "Daily limit"
                valueText: root.dailyLimit > 0 ? root.dailyLimit + " dabs" : "Off"
                canLower: root.dailyLimit > 0
                canRaise: root.dailyLimit < 50
                onLower: root.stepDailyLimit(-1)
                onRaise: root.stepDailyLimit(1)
              }

              Text {
                width: parent.width
                visible: root.dailyLimit > 0
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: "You'll get one notification the day you reach it."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              SwitchRow {
                width: parent.width
                label: "Weekly recap on Sunday evening"
                checked: root.recapOn
                onToggled: root.toggleRecap()
              }
            }
          }

          // ================================================== Usage
          Segmented {
            width: parent.width
            visible: root.onUsage && root.connected
            compact: true
            options: root.usageTabOptions
            value: root.usageTab
            onPicked: function(value) {
              root.usageTab = value
              scroller.contentY = 0
            }
          }

          Column {
            id: historyPage
            width: parent.width
            visible: root.onUsageHistory && root.connected
            spacing: Style.spacing.controlGap

            Text {
              width: parent.width
              visible: text !== ""
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              text: root.sessionList === null
                ? (root.sessionsLoading ? "Loading your dabs\u2026" : "")
                : root.sessionList.length === 0
                  ? "No dabs yet. Each one shows up here, ready for a note."
                  : "Tap a dab to add or edit its note."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Repeater {
              model: root.sessionList || []

              SessionCard {
                required property var modelData
                width: parent ? parent.width : 0
                session: modelData
              }
            }

            ActionButton {
              width: parent.width
              visible: root.sessionList !== null && root.sessionTotal > root.sessionList.length
              label: root.sessionsLoading ? "Loading\u2026" : "Show more"
              onActivated: {
                root.sessionLimit += 30
                root.loadSessions()
              }
            }
          }

          Column {
            id: usagePage
            width: parent.width
            visible: root.onUsageStats && root.connected
            spacing: Style.spacing.panelGap

            Text {
              width: parent.width
              visible: root.statusData.usage_syncing === true
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              text: "Reading usage history from this Peak. The first read on a new computer takes a minute or two."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

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
              visible: root.profileUsage.length > 0
              title: "PROFILES"
              trailing: "Last " + (Number(root.telemetry.profile_days) || 30) + " days"

              Repeater {
                model: root.profileUsage

                Column {
                  id: usageRow
                  required property var modelData
                  width: parent ? parent.width : 0
                  spacing: Style.space(4)

                  Item {
                    width: parent.width
                    implicitHeight: Math.max(usageName.implicitHeight, usageCount.implicitHeight)

                    Text {
                      id: usageName
                      anchors.left: parent.left
                      anchors.right: usageCount.left
                      anchors.rightMargin: Style.spacing.md
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      elide: Text.ElideRight
                      text: root.profileUsageName(usageRow.modelData.index)
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }

                    Text {
                      id: usageCount
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: {
                        var d = usageRow.modelData
                        var n = Number(d.count)
                        var s = n + (n === 1 ? " session" : " sessions") + " \u00b7 " + Math.round(Number(d.share) * 100) + "%"
                        if (d.temp_f !== null && d.temp_f !== undefined) s += " \u00b7 " + root.formatTemp(d.temp_f, d.temp_c)
                        return s
                      }
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }

                  Rectangle {
                    width: parent.width
                    height: Style.space(6)
                    radius: height / 2
                    color: Style.normalFillFor(root.foreground, Color.accent)

                    Rectangle {
                      width: Math.max(parent.height, parent.width * Math.min(1, Number(usageRow.modelData.share) || 0))
                      height: parent.height
                      radius: height / 2
                      color: root.profileUsageColor(usageRow.modelData.index)
                    }
                  }
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

            Segmented {
              width: parent.width
              compact: true
              options: root.deviceTabOptions
              value: root.deviceTab
              onPicked: function(value) {
                root.deviceTab = value
                scroller.contentY = 0
              }
            }

            Column {
              width: parent.width
              visible: root.onDeviceInfo
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
              InfoRow { width: parent.width; label: "Battery capacity"; value: root.batteryCapacityLabel }
              InfoRow { width: parent.width; label: "Battery health"; value: root.batteryHealthLabel }
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
              title: "FAULT LOG"
              trailing: root.faultLog !== null && !root.faultsLoading
                ? root.faultLog.length + (root.faultLog.length === 1 ? " fault" : " faults")
                : ""

              Text {
                width: parent.width
                visible: text !== ""
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: root.faultsLoading ? "Reading the Peak's fault log. The first read takes a minute or two."
                  : root.faultError ? "Couldn't read the fault log. Check the Peak is connected, then try again."
                  : root.faultLog === null ? "Heater, battery and pairing problems the Peak has recorded."
                  : root.faultLog.length === 0 ? "No faults recorded."
                  : ""
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              Repeater {
                model: root.faultLog && !root.faultsLoading ? root.faultLog.slice(0, root.faultShown) : []

                FaultCard {
                  required property var modelData
                  width: parent ? parent.width : 0
                  fault: modelData
                }
              }

              ActionButton {
                width: parent.width
                visible: !root.faultsLoading && root.faultLog !== null && root.faultLog.length > root.faultShown
                label: "Show " + (root.faultLog ? root.faultLog.length - root.faultShown : 0) + " more"
                onActivated: root.faultShown = root.faultLog.length
              }

              Row {
                width: parent.width
                spacing: Style.spacing.controlGap
                readonly property bool opened: root.faultLog !== null && !root.faultsLoading

                ActionButton {
                  width: parent.opened ? (parent.width - parent.spacing) / 2 : parent.width
                  label: root.faultsLoading ? "Reading…" : (root.faultLog === null ? "Read fault log" : "Refresh")
                  onActivated: root.readFaults()
                }

                ActionButton {
                  visible: parent.opened
                  width: (parent.width - parent.spacing) / 2
                  label: "Close"
                  onActivated: root.closeFaults()
                }
              }
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
              title: "CONNECTION"

              Text {
                width: parent.width
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: "The Peak accepts one connection at a time. Disconnect to use it from your phone or another computer."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              ActionButton {
                width: parent.width
                label: "Disconnect"
                glyph: "\uf127"
                onActivated: root.disconnectDevice()
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

            Column {
              width: parent.width
              visible: root.onDeviceTips
              spacing: Style.spacing.panelGap

              Section {
                title: "STOCK HEAT"
                trailing: "Factory"

                Grid {
                  width: parent.width
                  columns: 2
                  rowSpacing: Style.spacing.controlGap
                  columnSpacing: Style.spacing.controlGap

                  readonly property real cellWidth: (width - columnSpacing) / 2

                  Repeater {
                    model: root.stockHeats

                    SummaryCell {
                      required property var modelData
                      width: parent.cellWidth
                      value: root.formatTemp(modelData.temp_f, undefined)
                      title: modelData.color + " · " + modelData.name
                    }
                  }
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  text: "Green is the everyday setting. Blue keeps flavor; White is clouds."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Section {
                title: "CARE"

                Repeater {
                  model: root.peakTips

                  TipBlock {
                    required property var modelData
                    width: parent ? parent.width : 0
                    title: String(modelData.title)
                    body: String(modelData.body)
                  }
                }
              }

              Section {
                title: "LIGHTS"

                Repeater {
                  model: root.peakLights

                  TipBlock {
                    required property var modelData
                    width: parent ? parent.width : 0
                    title: String(modelData.title)
                    body: String(modelData.body)
                  }
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

  // Circular progress track; children (the countdown) sit in the middle.
  component TimerRing: Item {
    id: ring

    property real progress: 0
    property color fillColor: Color.accent
    property color trackColor: Util.alpha(root.foreground, 0.15)
    property real thickness: Style.space(6)

    onProgressChanged: canvas.requestPaint()
    onFillColorChanged: canvas.requestPaint()
    onTrackColorChanged: canvas.requestPaint()

    Canvas {
      id: canvas
      anchors.fill: parent
      onWidthChanged: requestPaint()
      onHeightChanged: requestPaint()
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        var cx = width / 2
        var cy = height / 2
        var r = Math.min(width, height) / 2 - ring.thickness / 2
        ctx.lineWidth = ring.thickness
        ctx.lineCap = Style.cornerRadius > 0 ? "round" : "butt"
        ctx.strokeStyle = ring.trackColor
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.stroke()
        if (ring.progress <= 0) return
        ctx.strokeStyle = ring.fillColor
        ctx.beginPath()
        ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * ring.progress)
        ctx.stroke()
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

  component TipBlock: Column {
    id: tip

    property string title: ""
    property string body: ""

    spacing: Style.space(2)

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: tip.title
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      text: tip.body
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // One fault: category glyph, what happened, and when.
  component FaultCard: BorderSurface {
    id: faultCard

    property var fault: ({})

    radius: Style.cornerRadius
    color: Style.normalFillFor(Color.urgent, Color.urgent)
    borderSpec: Border.controlSpec("normal", Color.urgent, Color.urgent)
    implicitHeight: cardRow.implicitHeight + Style.spacing.controlPaddingY * 2

    Item {
      id: cardRow
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.rightMargin: Style.spacing.controlPaddingX
      implicitHeight: Math.max(cardGlyph.implicitHeight, cardText.implicitHeight)

      Text {
        id: cardGlyph
        width: Style.space(20)
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText
        text: root.faultGlyph(Number(faultCard.fault.code))
        color: Color.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.iconSmall
      }

      Column {
        id: cardText
        anchors.left: cardGlyph.right
        anchors.right: parent.right
        anchors.leftMargin: Style.spacing.md
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(2)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: String(faultCard.fault.label || "")
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
          wrapMode: Text.WordWrap
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: root.formatFaultTime(faultCard.fault.ts)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }

  // One dab in History: what it was, when, and its note (tap to edit).
  component SessionCard: BorderSurface {
    id: sessionCard

    property var session: ({})
    readonly property string key: String(session.key || "")
    readonly property bool editingNote: root.noteKey === key
    readonly property string note: root.noteFor(session)

    radius: Style.cornerRadius
    color: Style.normalFillFor(root.foreground, Color.accent)
    borderSpec: Border.controlSpec(sessionMouse.containsMouse || editingNote ? "hover-cursor" : "normal", root.foreground, Color.accent)
    implicitHeight: sessionCol.implicitHeight + Style.spacing.controlPaddingY * 2

    MouseArea {
      id: sessionMouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: !sessionCard.editingNote
      cursorShape: Qt.PointingHandCursor
      onClicked: root.editNote(sessionCard.key)
    }

    Column {
      id: sessionCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.rightMargin: Style.spacing.controlPaddingX
      anchors.topMargin: Style.spacing.controlPaddingY
      spacing: Style.space(4)

      Item {
        width: parent.width
        implicitHeight: Math.max(sessionTitle.implicitHeight, sessionTime.implicitHeight)

        Text {
          id: sessionTitle
          anchors.left: parent.left
          anchors.right: sessionTime.left
          anchors.rightMargin: Style.spacing.md
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          elide: Text.ElideRight
          text: root.sessionTitle(sessionCard.session)
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }

        Text {
          id: sessionTime
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: root.formatSessionTime(sessionCard.session.ts)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        width: parent.width
        visible: Number(sessionCard.session.preheat_s) > 0
        textFormat: Text.PlainText
        text: "Heated up in " + Math.round(Number(sessionCard.session.preheat_s)) + " s"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Text {
        width: parent.width
        visible: !sessionCard.editingNote
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        text: sessionCard.note !== "" ? sessionCard.note : "Add a note"
        color: sessionCard.note !== "" ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: sessionCard.note !== "" ? Style.font.bodySmall : Style.font.caption
        font.italic: sessionCard.note === ""
      }

      Loader {
        width: parent.width
        active: sessionCard.editingNote

        sourceComponent: NoteEditor {
          noteKey: sessionCard.key
          seed: sessionCard.note
        }
      }

      Text {
        width: parent.width
        visible: sessionCard.editingNote
        textFormat: Text.PlainText
        text: "Enter or click away to save \u00b7 Esc to cancel"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // Unlike TileEditor, losing focus saves: a half-written note shouldn't vanish.
  component NoteEditor: TextField {
    id: noteEditor

    property string noteKey: ""
    property string seed: ""
    property bool armed: false
    property bool done: false

    function finish(save) {
      if (done) return
      done = true
      if (save) root.saveNote(noteKey, text)
      else root.closeNote(noteKey)
    }

    foreground: root.foreground
    accent: Color.accent
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    horizontalPadding: Style.spacing.xs
    maximumLength: 500
    placeholderText: "How was it? Flavor, clouds, what you'd change"

    Component.onCompleted: {
      text = seed
      Qt.callLater(function() {
        if (noteEditor.cursorPosition !== undefined) noteEditor.cursorPosition = noteEditor.text.length
        noteEditor.forceActiveFocus()
        noteEditor.armed = true
      })
    }

    onAccepted: noteEditor.finish(true)
    Keys.onEscapePressed: function(event) {
      noteEditor.finish(false)
      event.accepted = true
    }
    onActiveFocusChanged: {
      if (!armed || activeFocus) return
      Qt.callLater(function() { noteEditor.finish(true) })
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
