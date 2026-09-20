import AppKit
import AuthenticationServices

// Reproduces the observed empty system sheet plus clipped detached panel.
@main
struct NativeVaultConfigurationCorpus {
    @MainActor static func main() {
        _ = NSApplication.shared
        let controller = CredentialProviderViewController()
        let host = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 440), styleMask: [.titled], backing: .buffered, defer: false)
        host.contentViewController = controller
        let before = Set(NSApplication.shared.windows.map(ObjectIdentifier.init))
        controller.prepareInterfaceForExtensionConfiguration()
        controller.view.layoutSubtreeIfNeeded()
        precondition(Set(NSApplication.shared.windows.map(ObjectIdentifier.init)) == before,
                     "Configuration must render inside Apple's host, not create a detached window")
        func descendants(_ view: NSView) -> [NSView] { view.subviews.flatMap { [$0] + descendants($0) } }
        let controls = descendants(controller.view).compactMap { $0 as? NSButton }
        precondition(controls.count >= 5, "Apple's configuration view must contain the setup actions")
        for size in [NSSize(width: 480, height: 440), NSSize(width: 560, height: 480)] {
            host.setContentSize(size)
            controller.view.layoutSubtreeIfNeeded()
            for control in controls {
                let frame = control.convert(control.bounds, to: controller.view)
                precondition(frame.width > 0 && frame.height > 0 && controller.view.bounds.contains(frame),
                             "Setup action is clipped outside Apple's configuration view: \(control.title)")
            }
        }
        controller.replaceNativeRequest()
        precondition(host.contentViewController === controller,
                     "Replacing a request must preserve Apple's host controller")
        print("PASS: embedded configuration controls remain visible across host sizes")
    }
}
