import CoreLocation
import Foundation
import Network
import XCTest

// jevtest iOS agent: a long-running UI test that serves the simulator's
// accessibility tree and touch input over HTTP on 127.0.0.1:$JEVTEST_PORT.
// Every request is a POST with a JSON body; every reply is JSON.

final class JevAgentUITests: XCTestCase {
    private var app: XCUIApplication?
    private var bundleId = ""
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    private var served = ""  // signature of the tree the client last received
    private static let quiet: TimeInterval = 0.15        // same quiet window as the Android agent
    private static let pollInterval: TimeInterval = 0.05 // no change events on iOS: this is the pace
    private var issues: [String] = []

    // A failed XCUITest call (e.g. typing with no focus) would normally fail and end
    // this long-running test, killing the agent. Record it and report it instead.
    override func record(_ issue: XCTIssue) {
        issues.append(issue.compactDescription)
    }

    func testServe() throws {
        continueAfterFailure = true
        let port = UInt16(ProcessInfo.processInfo.environment["JEVTEST_PORT"] ?? "") ?? 8123
        let listener = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port)!)
        listener.newConnectionHandler = { [weak self] conn in
            conn.start(queue: .global())
            self?.receive(conn, buffer: Data())
        }
        listener.start(queue: .global())
        print("JEVTEST_AGENT_READY \(port)")
        while true {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
    }

    // MARK: HTTP

    private func receive(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, done, error in
            guard let self else { return }
            var buf = buffer
            if let data { buf.append(data) }
            if let (path, body) = Self.parse(buf) {
                DispatchQueue.main.async {
                    let reply = self.handle(path: path, body: body)
                    Self.send(conn, reply)
                }
            } else if done || error != nil {
                conn.cancel()
            } else {
                self.receive(conn, buffer: buf)
            }
        }
    }

    private static func parse(_ buf: Data) -> (String, [String: Any])? {
        guard let headerEnd = buf.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: buf[..<headerEnd.lowerBound], as: UTF8.self)
        let lines = head.components(separatedBy: "\r\n")
        let path = lines.first?.split(separator: " ").dropFirst().first.map(String.init) ?? "/"
        var length = 0
        for line in lines where line.lowercased().hasPrefix("content-length:") {
            length = Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
        }
        let bodyData = buf[headerEnd.upperBound...]
        guard bodyData.count >= length else { return nil }
        let json = (try? JSONSerialization.jsonObject(with: Data(bodyData.prefix(length)))) as? [String: Any]
        return (path, json ?? [:])
    }

    private static func send(_ conn: NWConnection, _ reply: [String: Any]) {
        let body = (try? JSONSerialization.data(withJSONObject: reply)) ?? Data("{}".utf8)
        var out = Data("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
        out.append(body)
        conn.send(content: out, completion: .contentProcessed { _ in conn.cancel() })
    }

    // MARK: Commands

    private func handle(path: String, body: [String: Any]) -> [String: Any] {
        issues = []
        do {
            let reply = try dispatch(path: path, body: body)
            if !issues.isEmpty { return ["error": issues.joined(separator: "; ")] }
            return reply
        } catch {
            return ["error": "\(error)"]
        }
    }

    private func current(_ body: [String: Any]) -> XCUIApplication {
        if let id = body["bundle_id"] as? String, !id.isEmpty {
            if app == nil || bundleId != id {
                app = XCUIApplication(bundleIdentifier: id)
                bundleId = id
            }
        }
        return app ?? XCUIApplication(bundleIdentifier: "com.apple.springboard")
    }

    /// Where touches go: SpringBoard while a system alert (a permission prompt) is up, since the alert is
    /// SpringBoard's, not the app's. Touching "through" the app makes XCUITest run its interruption
    /// handling first, which stalls on a real iPhone. Both apps fill the screen, so points are the same.
    private func touched(_ app: XCUIApplication) -> XCUIApplication {
        springboard.alerts.firstMatch.exists ? springboard : app
    }

    private func point(_ app: XCUIApplication, _ x: Any?, _ y: Any?) -> XCUICoordinate {
        let dx = (x as? Double) ?? 0, dy = (y as? Double) ?? 0
        return app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: dx, dy: dy))
    }

    private func dispatch(path: String, body: [String: Any]) throws -> [String: Any] {
        let app = current(body)
        switch path {
        case "/status":
            return ["ok": true]
        case "/wait_foreground":
            // Called right after `simctl launch`. A fresh handle, because after a reinstall the old
            // one points at the removed app; wait(for:) reacts as soon as the app is frontmost.
            let fresh = XCUIApplication(bundleIdentifier: bundleId)
            self.app = fresh
            guard fresh.wait(for: .runningForeground, timeout: (body["timeout"] as? Double) ?? 10) else {
                return ["error": "App '\(bundleId)' did not come to the foreground"]
            }
        case "/activate":
            app.activate()
            return ["ok": app.wait(for: .runningForeground, timeout: (body["timeout"] as? Double) ?? 10)]
        case "/state":
            return ["state": app.state.rawValue]
        case "/tree":
            let (reply, signature) = try tree(app)
            served = signature
            return reply
        case "/tap":
            point(touched(app), body["x"], body["y"]).tap()
        case "/double_tap":
            point(touched(app), body["x"], body["y"]).doubleTap()
        case "/long_press":
            point(touched(app), body["x"], body["y"]).press(forDuration: (body["seconds"] as? Double) ?? 1.2)
        case "/drag":
            let target = touched(app)
            point(target, body["x1"], body["y1"]).press(
                forDuration: 0.05, thenDragTo: point(target, body["x2"], body["y2"]),
                withVelocity: .fast, thenHoldForDuration: 0.05)
        case "/type":
            app.typeText((body["text"] as? String) ?? "")
        case "/change":
            // iOS has no "UI changed" event (WebDriverAgent and Maestro poll too), so poll here,
            // on the device. "Changed" = differs from the tree the client last received, so a
            // change that lands between its /tree and its /change is not missed.
            let deadline = Date().addingTimeInterval((body["timeout"] as? Double) ?? 3)
            while true {
                if try signature(app) != served { return ["changed": true] }
                if Date() >= deadline { break }
                RunLoop.current.run(until: min(deadline, Date().addingTimeInterval(Self.pollInterval)))
            }
            return ["changed": false]
        case "/idle":
            // XCUITest's own idle wait only sees UIKit; Flutter and web views draw their own
            // animations. Idle = the screen has not changed for a quiet window (like Android's
            // UiAutomation.waitForIdle), so a UI that has not started reacting yet is not "idle".
            let deadline = Date().addingTimeInterval((body["timeout"] as? Double) ?? 3)
            var last = try signature(app)
            var stableSince = Date()
            let quiet = (body["quiet"] as? Double) ?? Self.quiet
            while Date() < deadline, Date().timeIntervalSince(stableSince) < quiet {
                RunLoop.current.run(until: min(deadline, Date().addingTimeInterval(Self.pollInterval)))
                let now = try signature(app)
                if now != last {
                    last = now
                    stableSince = Date()
                }
            }
        case "/key":
            let keys: [String: String] = [
                "enter": "\n", "return": "\n", "tab": "\t",
                "delete": XCUIKeyboardKey.delete.rawValue, "backspace": XCUIKeyboardKey.delete.rawValue,
                "escape": XCUIKeyboardKey.escape.rawValue, "space": " ",
            ]
            let name = (body["key"] as? String ?? "").lowercased()
            guard let k = keys[name] else { return ["error": "Unknown key '\(name)'. Known: \(keys.keys.sorted())"] }
            app.typeText(String(repeating: k, count: (body["count"] as? Int) ?? 1))
        // Device-level operations that also work on a real iPhone (the simulator can use simctl).
        case "/terminate":
            app.terminate()
        case "/screenshot":
            return ["png": XCUIScreen.main.screenshot().pngRepresentation.base64EncodedString()]
        case "/location":
            guard let lat = body["lat"] as? Double, let lon = body["lon"] as? Double else {
                return ["error": "/location needs lat and lon"]
            }
            XCUIDevice.shared.location = XCUILocation(location: CLLocation(latitude: lat, longitude: lon))
        case "/open_url":
            guard let url = URL(string: (body["url"] as? String) ?? "") else { return ["error": "Not a URL"] }
            XCUIDevice.shared.system.open(url)
        case "/appearance":
            // With `raw`, set that appearance (to put back what was there); with neither, just report it.
            if let dark = body["dark"] as? Bool {
                XCUIDevice.shared.appearance = dark ? .dark : .light
            } else if let raw = body["raw"] as? Int, let value = XCUIDevice.Appearance(rawValue: raw) {
                XCUIDevice.shared.appearance = value
            }
            return ["raw": XCUIDevice.shared.appearance.rawValue]
        case "/home":
            XCUIDevice.shared.press(.home)
        case "/rotate":
            // With `orientation` or `raw`, rotate; with neither, just report the orientation.
            let map: [String: UIDeviceOrientation] = [
                "portrait": .portrait, "landscape": .landscapeLeft,
                "landscape_right": .landscapeRight, "portrait_upside_down": .portraitUpsideDown,
            ]
            if let o = body["orientation"] as? String {
                guard let value = map[o] else { return ["error": "Unknown orientation '\(o)'"] }
                XCUIDevice.shared.orientation = value
            } else if let raw = body["raw"] as? Int, let value = UIDeviceOrientation(rawValue: raw) {
                XCUIDevice.shared.orientation = value
            }
            return ["raw": XCUIDevice.shared.orientation.rawValue]
        case "/back":
            // What a person taps: a button labelled "Back" (Flutter, React Native, UIKit), then a
            // native navigation bar's back button, and only then the edge-swipe gesture.
            let labelled = app.buttons["Back"]
            let navBack = app.navigationBars.buttons.firstMatch
            if labelled.exists && labelled.isHittable {
                labelled.tap()
            } else if navBack.exists && navBack.isHittable {
                navBack.tap()
            } else {
                let f = app.frame
                point(app, 2.0, Double(f.height / 2)).press(
                    forDuration: 0.05, thenDragTo: point(app, Double(f.width * 0.7), Double(f.height / 2)))
            }
        case "/hide_keyboard":
            if app.keyboards.count > 0 {
                let done = app.keyboards.buttons.matching(NSPredicate(format: "label IN {'Done','done','Return','return','Hide keyboard'}")).firstMatch
                if done.exists { done.tap() } else { app.typeText("\n") }
            }
        default:
            return ["error": "Unknown command \(path)"]
        }
        return ["ok": true]
    }

    /// What is on screen: the app, plus a system alert on top of it (permission prompts belong
    /// to SpringBoard, not the app, so the app's own snapshot never contains them).
    private func snapshots(_ app: XCUIApplication) throws -> [XCUIElementSnapshot] {
        var roots = [try app.snapshot()]
        let alert = springboard.alerts.firstMatch
        if alert.exists, let snap = try? alert.snapshot() {
            roots.append(snap)
        }
        return roots
    }

    /// A line per element: identical for identical screens. Used to detect change and idleness.
    private static func line(_ s: XCUIElementSnapshot) -> String {
        "\(s.elementType.rawValue)|\(s.label)|\(s.value ?? "")|\(s.frame)|\(s.hasFocus)"
    }

    private func signature(_ app: XCUIApplication) throws -> String {
        var parts: [String] = []
        func walk(_ s: XCUIElementSnapshot) {
            parts.append(Self.line(s))
            s.children.forEach(walk)
        }
        try snapshots(app).forEach(walk)
        return parts.joined(separator: "\n")
    }

    /// The screen for the client, and its signature, from one snapshot pass.
    private func tree(_ app: XCUIApplication) throws -> ([String: Any], String) {
        guard app.state == .runningForeground else {
            return (["elements": [], "width": 0, "height": 0, "keyboard": false], "")
        }
        var out: [[String: Any]] = []
        var parts: [String] = []
        func hasKeyboard(_ s: XCUIElementSnapshot) -> Bool {
            s.elementType == .keyboard || s.children.contains(where: hasKeyboard)
        }
        func walk(_ s: XCUIElementSnapshot, keyboardWindow: Bool) {
            parts.append(Self.line(s))
            // The keyboard's window (keys, suggestion strip, emoji and dictation buttons) is not
            // the app's UI: noise for Jev. Whether it is up is reported as "keyboard" below.
            let skip = keyboardWindow || s.elementType == .keyboard || (s.elementType == .window && hasKeyboard(s))
            if !skip {
                let f = s.frame
                var d: [String: Any] = [
                    "type": Self.typeName(s.elementType),
                    "identifier": s.identifier,
                    "label": s.label,
                    "x": f.origin.x, "y": f.origin.y, "w": f.size.width, "h": f.size.height,
                    "enabled": s.isEnabled, "selected": s.isSelected, "focused": s.hasFocus,
                ]
                if let v = s.value { d["value"] = "\(v)" }
                if let p = s.placeholderValue { d["placeholder"] = p }
                out.append(d)
            }
            s.children.forEach { walk($0, keyboardWindow: skip) }
        }
        try snapshots(app).forEach { walk($0, keyboardWindow: false) }
        let reply: [String: Any] = [
            "elements": out,
            "width": app.frame.size.width, "height": app.frame.size.height,
            "keyboard": app.keyboards.count > 0,
            "keyboard_top": app.keyboards.count > 0 ? app.keyboards.firstMatch.frame.minY : 0,
        ]
        return (reply, parts.joined(separator: "\n"))
    }

    private static func typeName(_ t: XCUIElement.ElementType) -> String {
        switch t {
        case .application: return "application"
        case .button: return "button"
        case .textField, .searchField: return "text_field"
        case .secureTextField: return "password_field"
        case .textView: return "text_area"
        case .staticText: return "text"
        case .image, .icon: return "image"
        case .switch, .toggle: return "switch"
        case .checkBox: return "checkbox"
        case .slider: return "slider"
        case .cell: return "cell"
        case .link: return "link"
        case .tab: return "tab"
        case .tabBar: return "tab_bar"
        case .navigationBar: return "navigation_bar"
        case .alert: return "alert"
        case .sheet: return "sheet"
        case .picker, .pickerWheel: return "picker"
        case .popUpButton, .comboBox, .menuButton: return "dropdown"  // e.g. a web <select>
        case .segmentedControl: return "segmented_control"
        case .menuItem: return "menu_item"
        case .scrollView: return "scroll_view"
        case .table, .collectionView: return "list"
        case .webView: return "webview"
        case .keyboard, .key: return "keyboard"
        case .other: return "other"
        default: return "other"
        }
    }
}
