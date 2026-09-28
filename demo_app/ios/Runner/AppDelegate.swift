import AVFoundation
import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // iOS 27 requires the scene lifecycle (Info.plist: FlutterSceneDelegate). With it, plugins and
  // channels are registered once Flutter's engine exists, not at launch.
  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let messenger = engineBridge.applicationRegistrar.messenger()
    FlutterMethodChannel(name: "jevtest/native", binaryMessenger: messenger).setMethodCallHandler { call, result in
      guard call.method == "open", let root = AppDelegate.topViewController() else {
        result(FlutterMethodNotImplemented)
        return
      }
      let screen = UINavigationController(rootViewController: NativeViewController())
      screen.modalPresentationStyle = .fullScreen
      root.present(screen, animated: true)
      result(nil)
    }
  }
}

extension AppDelegate {
  static func topViewController() -> UIViewController? {
    let window = UIApplication.shared.connectedScenes
      .compactMap { ($0 as? UIWindowScene)?.keyWindow }
      .first
    var top = window?.rootViewController
    while let presented = top?.presentedViewController { top = presented }
    return top
  }
}

/// A plain UIKit screen, so jevtest is exercised on native widgets, not only Flutter.
final class NativeViewController: UIViewController {
  private let nickname = UITextField()
  private let saved = UILabel()
  private let themeState = UILabel()
  private let camera = UILabel()

  override func viewDidLoad() {
    super.viewDidLoad()
    title = "Native screen"
    view.backgroundColor = .systemBackground
    navigationItem.leftBarButtonItem = UIBarButtonItem(title: "Close", style: .plain, target: self, action: #selector(close))

    nickname.placeholder = "Nickname"
    nickname.text = "Guest"
    nickname.borderStyle = .roundedRect
    nickname.returnKeyType = .done
    nickname.addTarget(self, action: #selector(doneEditing), for: .editingDidEndOnExit)  // Done closes the keyboard
    saved.text = "Nothing saved"
    themeState.text = "Theme is light"
    camera.text = "Camera: not asked"
    let theme = UISwitch()
    theme.accessibilityLabel = "Dark theme"
    theme.addTarget(self, action: #selector(themeChanged(_:)), for: .valueChanged)
    let themeLabel = UILabel()
    themeLabel.text = "Dark theme"
    themeLabel.isAccessibilityElement = false  // the switch carries the label
    let themeRow = UIStackView(arrangedSubviews: [themeLabel, theme])  // a switch keeps its own size

    let stack = UIStackView(arrangedSubviews: [
      nickname, button("Save nickname", #selector(save)), saved,
      themeRow, themeState, button("Delete account", #selector(deleteAccount)),
      button("Ask for camera", #selector(askCamera)), camera,
    ])
    stack.axis = .vertical
    stack.spacing = 16
    stack.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(stack)
    NSLayoutConstraint.activate([
      stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
      stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
      stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
    ])
  }

  private func button(_ title: String, _ action: Selector) -> UIButton {
    let b = UIButton(type: .system)
    b.setTitle(title, for: .normal)
    b.addTarget(self, action: action, for: .touchUpInside)
    return b
  }

  @objc private func close() { dismiss(animated: true) }
  @objc private func doneEditing() {}
  @objc private func save() { saved.text = "Saved: \(nickname.text ?? "")" }
  @objc private func themeChanged(_ sender: UISwitch) { themeState.text = sender.isOn ? "Theme is dark" : "Theme is light" }

  @objc private func deleteAccount() {
    let alert = UIAlertController(title: "Delete account?", message: "This cannot be undone.", preferredStyle: .alert)
    alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
    alert.addAction(UIAlertAction(title: "Delete", style: .destructive) { _ in self.saved.text = "Account deleted" })
    present(alert, animated: true)
  }

  @objc private func askCamera() {
    AVCaptureDevice.requestAccess(for: .video) { granted in
      DispatchQueue.main.async { self.camera.text = granted ? "Camera: allowed" : "Camera: denied" }
    }
  }
}
