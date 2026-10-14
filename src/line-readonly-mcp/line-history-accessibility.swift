import AppKit
import ApplicationServices
import Foundation

// No search, result clicks, activation, mouse movement, keyboard or composer.
// Operates ONLY on one already-open, focused, exactly named LINE chat window.
// Emits structural metadata only; never reads message-row values.
let args = CommandLine.arguments
let operation = args.count > 1 ? args[1] : ""
let rounds = operation == "load" && args.count == 4 ? Int(args[3]) ?? 0 : 0
func emit(_ data: [String: Any], code: Int32 = 0) -> Never {
    let bytes = try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys])
    FileHandle.standardOutput.write(bytes)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(code)
}
func fail(_ reason: String) -> Never { emit(["ok": false, "error": reason], code: 1) }
guard operation == "inspect" && args.count == 2 || operation == "load" && args.count == 4 else {
    fail("operation_not_allowed")
}
if operation == "load" {
    guard (1...20).contains(rounds), !args[2].isEmpty, args[2].count <= 256 else { fail("invalid_request") }
}
let deadline = Date().addingTimeInterval(10 + Double(rounds) * 2)
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    guard Date() < deadline else { return nil }
    AXUIElementSetMessagingTimeout(element, 0.4)
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func role(_ element: AXUIElement) -> String { attribute(element, kAXRoleAttribute) as? String ?? "" }
func title(_ element: AXUIElement) -> String { attribute(element, kAXTitleAttribute) as? String ?? "" }
func children(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
func rect(_ element: AXUIElement) -> CGRect? {
    guard let p = attribute(element, kAXPositionAttribute), let s = attribute(element, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(p as! AXValue, .cgPoint, &point),
          AXValueGetValue(s as! AXValue, .cgSize, &size) else { return nil }
    return CGRect(origin: point, size: size)
}
guard AXIsProcessTrusted() else { fail("accessibility_permission_required") }
guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "jp.naver.line.mac").first else {
    fail("line_not_running")
}
let root = AXUIElementCreateApplication(app.processIdentifier)
guard let focused = attribute(root, kAXFocusedWindowAttribute), CFGetTypeID(focused) == AXUIElementGetTypeID() else {
    fail("line_window_unavailable")
}
let window = focused as! AXUIElement
if operation == "inspect" {
    let visible = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
    emit(["ok": true, "windowAvailable": true, "windowChildRoles": children(window).map(role),
          "minimized": attribute(window, kAXMinimizedAttribute) as? Bool ?? false,
          "visibleLineWindowCount": visible.filter { $0[kCGWindowOwnerPID as String] as? Int32 == app.processIdentifier }.count])
}
let chatName = args[2]
let windowTitleMatches = title(window) == chatName
let lineFrontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier
guard windowTitleMatches, lineFrontmost else {
    emit(["ok": false, "error": "opened_chat_identity_unverified",
          "windowTitleMatches": windowTitleMatches, "lineFrontmost": lineFrontmost], code: 1)
}
var panes: [AXUIElement] = []
var visited = 0
func findHistory(_ element: AXUIElement, depth: Int) {
    guard depth <= 5, visited < 80, Date() < deadline else { return }
    visited += 1
    let elementRole = role(element)
    if elementRole == "AXSplitGroup" {
        let items = children(element)
        if items.contains(where: { role($0) == "AXTextArea" }) {
            panes += items.filter { role($0) == "AXList" }
            return
        }
    }
    if ["AXList", "AXTextArea", "AXStaticText", "AXRow"].contains(elementRole) { return }
    for child in children(element) { findHistory(child, depth: depth + 1) }
}
findHistory(window, depth: 0)
guard panes.count == 1, let bounds = rect(panes[0]), bounds.width > 100, bounds.height > 100 else {
    fail("history_pane_unverified")
}
for _ in 0..<rounds {
    guard Date() < deadline, let current = attribute(root, kAXFocusedWindowAttribute),
          CFGetTypeID(current) == AXUIElementGetTypeID(), CFEqual(current, window),
          title(window) == chatName, rect(panes[0]) == bounds,
          NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
        fail("history_target_changed")
    }
    // Wheel-only events are delivered directly to the verified LINE process.
    // There are no global pointer moves or mouse button events.
    for _ in 0..<4 {
        let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: 800, wheel2: 0, wheel3: 0)
        event?.location = CGPoint(x: bounds.midX, y: bounds.midY)
        event?.postToPid(app.processIdentifier)
        Thread.sleep(forTimeInterval: 0.1)
    }
    Thread.sleep(forTimeInterval: 1.2)
}
emit(["ok": true, "openedExactResult": true, "alreadyOpenChatRequired": true,
      "historyPaneVerified": true, "completedRounds": rounds,
      "safety": ["typed": false, "pasted": false, "clicked": false, "sent": false]])
