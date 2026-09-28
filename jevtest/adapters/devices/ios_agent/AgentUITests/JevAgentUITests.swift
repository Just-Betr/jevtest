import CoreLocation
import Foundation
import Network
import XCTest

// jevtest iOS agent: a long-running UI test that serves the device's accessibility tree and touch input over
// HTTP on $JEVTEST_PORT. Every request is a POST with a JSON body; every reply is JSON.
//
// Only jevtest may use it: every request must carry the run's secret token ($JEVTEST_TOKEN) in the
// X-Jevtest-Token header, or it is refused. On a simulator ($JEVTEST_LOCAL_ONLY=1) it also listens on the
// loopback interface only; on an iPhone jevtest reaches it through the USB tunnel, so the token is what guards it.

final class JevAgentUITests: XCTestCase {
    private var app: XCUIApplication?
    private var bundleId = ""
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    private static let barHeight: CGFloat = 60  // points: the most a bar above the keyboard is tall
    private var issues: [String] = []
    private var token = Data()

    // A failed XCUITest call (e.g. typing with no focus) would normally fail and end
    // this long-running test, killing the agent. Record it and report it instead.
    override func record(_ issue: XCTIssue) {
        issues.append(issue.compactDescription)
    }

    func testServe() throws {
        continueAfterFailure = true
        let env = ProcessInfo.processInfo.environment
        let port = UInt16(env["JEVTEST_PORT"] ?? "") ?? 8123
        guard let secret = env["JEVTEST_TOKEN"], !secret.isEmpty else {
            print("JEVTEST_AGENT_ERROR JEVTEST_TOKEN is not set: refusing to serve without one")
            return
        }
        token = Data(secret.utf8)
        let parameters = NWParameters.tcp
        if env["JEVTEST_LOCAL_ONLY"] == "1" {
            parameters.requiredInterfaceType = .loopback  // a simulator is reached on 127.0.0.1 only
        }
        let listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)
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
            if let (path, body, given) = Self.parse(buf) {
                guard Self.same(given, self.token) else {
                    Self.send(conn, ["error": "missing or wrong X-Jevtest-Token"], status: "403 Forbidden")
                    return
                }
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

    /// Compares two tokens in constant time, so response timing reveals nothing about the right one.
    private static func same(_ a: Data, _ b: Data) -> Bool {
        guard a.count == b.count, !b.isEmpty else { return false }
        return zip(a, b).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }

    private static func parse(_ buf: Data) -> (String, [String: Any], Data)? {
        guard let headerEnd = buf.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: buf[..<headerEnd.lowerBound], as: UTF8.self)
        let lines = head.components(separatedBy: "\r\n")
        let path = lines.first?.split(separator: " ").dropFirst().first.map(String.init) ?? "/"
        var length = 0
        var given = Data()
        for line in lines {
            let lower = line.lowercased()
            if lower.hasPrefix("content-length:") {
                length = Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
            } else if lower.hasPrefix("x-jevtest-token:") {
                given = Data(line.dropFirst("x-jevtest-token:".count).trimmingCharacters(in: .whitespaces).utf8)
            }
        }
        let bodyData = buf[headerEnd.upperBound...]
        guard bodyData.count >= length else { return nil }
        let json = (try? JSONSerialization.jsonObject(with: Data(bodyData.prefix(length)))) as? [String: Any]
        return (path, json ?? [:], given)
    }

    private static func send(_ conn: NWConnection, _ reply: [String: Any], status: String = "200 OK") {
        let body = (try? JSONSerialization.data(withJSONObject: reply)) ?? Data("{}".utf8)
        var out = Data("HTTP/1.1 \(status)\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
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
            return try tree(app)
        case "/tap":
            point(touched(app), body["x"], body["y"]).tap()
        case "/double_tap":
            point(touched(app), body["x"], body["y"]).doubleTap()
        case "/long_press":
            point(touched(app), body["x"], body["y"]).press(forDuration: (body["seconds"] as? Double) ?? 1.2)
        case "/drag":
            guard let velocity = body["velocity"] as? Double, let hold = body["hold"] as? Double else {
                return ["error": "/drag needs velocity and hold"]
            }
            let target = touched(app)
            point(target, body["x1"], body["y1"]).press(
                forDuration: 0.05, thenDragTo: point(target, body["x2"], body["y2"]),
                withVelocity: XCUIGestureVelocity(CGFloat(velocity)), thenHoldForDuration: hold)
        case "/type":
            app.typeText((body["text"] as? String) ?? "")
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
            // What a person would tap: the Done on the bar just above the keyboard (web views and many apps
            // show one), else the keyboard's own Hide keyboard or Done key, else Return. The client checks
            // that the keyboard really went away.
            if app.keyboards.count > 0 {
                let top = app.keyboards.firstMatch.frame.minY
                let barDone = app.buttons.matching(NSPredicate(format: "label == 'Done'")).allElementsBoundByIndex
                    .first { $0.isHittable && $0.frame.maxY <= top + 1 && $0.frame.maxY > top - Self.barHeight }
                let key = app.keyboards.buttons.matching(NSPredicate(format: "label IN {'Hide keyboard','Done','done'}")).firstMatch
                if let done = barDone { done.tap() } else if key.exists { key.tap() } else { app.typeText("\n") }
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

    /// The screen for the client, from one snapshot pass. The agent never waits: the client reads the
    /// screen when a step checks what it waits for.
    private func tree(_ app: XCUIApplication) throws -> [String: Any] {
        guard app.state == .runningForeground else {
            return ["elements": [], "width": 0, "height": 0, "keyboard": false]
        }
        var out: [[String: Any]] = []
        func hasKeyboard(_ s: XCUIElementSnapshot) -> Bool {
            s.elementType == .keyboard || s.children.contains(where: hasKeyboard)
        }
        func walk(_ s: XCUIElementSnapshot, keyboardWindow: Bool) {
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
        return reply
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
