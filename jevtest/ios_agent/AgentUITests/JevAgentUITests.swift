import Foundation
import Network
import XCTest

// jevtest iOS agent: a long-running UI test that serves the simulator's
// accessibility tree and touch input over HTTP on 127.0.0.1:$JEVTEST_PORT.
// Every request is a POST with a JSON body; every reply is JSON.

final class JevAgentUITests: XCTestCase {
    private var app: XCUIApplication?

    func testServe() throws {
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
        do {
            return try dispatch(path: path, body: body)
        } catch {
            return ["error": "\(error)"]
        }
    }

    private func current(_ body: [String: Any]) -> XCUIApplication {
        if let id = body["bundle_id"] as? String, !id.isEmpty {
            if app == nil || app!.identifier != id { app = XCUIApplication(bundleIdentifier: id) }
        }
        return app ?? XCUIApplication(bundleIdentifier: "com.apple.springboard")
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
        case "/activate":
            app.activate()
            return ["ok": app.wait(for: .runningForeground, timeout: 10)]
        case "/state":
            return ["state": app.state.rawValue]
        case "/tree":
            return try tree(app)
        case "/tap":
            point(app, body["x"], body["y"]).tap()
        case "/double_tap":
            point(app, body["x"], body["y"]).doubleTap()
        case "/long_press":
            point(app, body["x"], body["y"]).press(forDuration: (body["seconds"] as? Double) ?? 1.2)
        case "/drag":
            let start = point(app, body["x1"], body["y1"])
            let end = point(app, body["x2"], body["y2"])
            start.press(forDuration: 0.05, thenDragTo: end, withVelocity: .fast, thenHoldForDuration: 0.05)
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
        case "/home":
            XCUIDevice.shared.press(.home)
        case "/rotate":
            let o = body["orientation"] as? String ?? "portrait"
            let map: [String: UIDeviceOrientation] = [
                "portrait": .portrait, "landscape": .landscapeLeft,
                "landscape_right": .landscapeRight, "portrait_upside_down": .portraitUpsideDown,
            ]
            guard let value = map[o] else { return ["error": "Unknown orientation '\(o)'"] }
            XCUIDevice.shared.orientation = value
        case "/back":
            let back = app.navigationBars.buttons.firstMatch
            if back.exists && back.isHittable {
                back.tap()
            } else {
                // Edge swipe: the system back gesture.
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

    private func tree(_ app: XCUIApplication) throws -> [String: Any] {
        let running = app.state == .runningForeground || app.state == .runningBackground
        guard app.state == .runningForeground else {
            return ["elements": [], "width": 0, "height": 0, "keyboard": false, "running": running]
        }
        let snap = try app.snapshot()
        var out: [[String: Any]] = []
        func walk(_ s: XCUIElementSnapshot) {
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
            s.children.forEach(walk)
        }
        walk(snap)
        return [
            "elements": out,
            "width": app.frame.size.width, "height": app.frame.size.height,
            "keyboard": app.keyboards.count > 0, "running": true,
        ]
    }

    private static func typeName(_ t: XCUIElement.ElementType) -> String {
        switch t {
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
