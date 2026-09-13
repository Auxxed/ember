import QtQuick
import Quickshell.Io
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "auxxed.quickpuff"

  property string outputText: ""
  property string outputTooltip: ""
  property bool outputActive: false
  property bool outputOffline: true
  property bool refreshPending: false
  // A stall kill also exits non-zero; that is a slow BLE call, not a missing install.
  property bool stalled: false

  function refresh() {
    if (proc.running) {
      refreshPending = true
      return
    }
    refreshPending = false
    proc.running = true
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  // Bar.findPanelWidget requires open/close/opened on the bar-widget root,
  // and the popout coordinator compares against slot.activeItem — so this
  // widget, not the nested panel, is the identity the bar tracks.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  Component.onCompleted: refresh()
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "auxxed.quickpuff"

    function refresh(): void { root.broadcast("refresh") }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  visible: outputText !== ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Goes through a login shell, same as bar.run()/execDetached and the
  // built-in custom-command module, so it finds `quickpuff` on PATH regardless
  // of how omarchy-shell itself was launched.
  Process {
    id: proc
    command: ["bash", "-lc", "quickpuff waybar"]
    // No output means `quickpuff` is missing or its daemon is down; stay visible
    // so the panel's Finish setup is reachable instead of vanishing.
    onExited: function(exitCode) {
      if (exitCode === 0 || root.stalled) {
        root.stalled = false
        return
      }
      root.outputText = "QuickPuff"
      root.outputTooltip = "QuickPuff needs setup — click to finish"
      root.outputActive = false
      root.outputOffline = true
    }
    onRunningChanged: {
      if (running) {
        stallTimer.restart()
        return
      }
      stallTimer.stop()
      if (root.refreshPending) root.refresh()
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (!text) return
        var data
        try {
          data = JSON.parse(text)
        } catch (e) {
          return
        }
        // `quickpuff waybar` pads its idle label with a double space, a waybar
        // convention for separating two fields. The Omarchy bar already gaps
        // its widgets, so that reads as two widgets here — collapse it.
        root.outputText = String(data.text || "").replace(/\s+/g, " ").trim()
        root.outputTooltip = String(data.tooltip || "")
        root.outputActive = data.class === "preheat" || data.class === "ready" || data.class === "clean"
        root.outputOffline = data.class === "disconnected"
      }
    }
  }

  // `quickpuff waybar` waits up to 5s on the daemon RPC; give up past that so a
  // stalled BLE call can't wedge the widget (a running Process can't be
  // re-run) and let the next poll retry.
  Timer {
    id: stallTimer
    interval: 6000
    onTriggered: {
      root.stalled = true
      proc.running = false
      root.refreshPending = true
    }
  }

  Timer {
    interval: 5000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.outputText
    tooltipText: root.outputTooltip
    active: root.outputActive || root.opened
    // Recede while there's no device to report on, the same way the shell's
    // own widgets dim when their subject is idle. Never while the panel is
    // open, so the label stays legible next to its own popup.
    dimmed: root.outputOffline && !root.opened
    // Matches the clock, the bar's other text-bearing widget; the label is
    // read as data, not as a compact tag like the keyboard-layout pill.
    fontSize: Style.font.body

    onPressed: function(b) {
      if (b === Qt.RightButton) {
        if (root.bar) root.bar.run("quickpuff heat start")
      } else if (b === Qt.MiddleButton) {
        root.broadcast("refresh")
      } else {
        root.togglePanel()
      }
    }
  }
}
