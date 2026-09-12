import AppKit
import AuthenticationServices

/// The first provider unit is intentionally a shell.  It owns no credential
/// identities and cannot complete an AutoFill or passkey request.  Keeping the
/// unavailable state inside the extension means it remains truthful when the
/// Tauri host and Python sidecar are not running.
final class CredentialProviderViewController: ASCredentialProviderViewController {
    override func loadView() {
        let view = NSView()
        let title = NSTextField(labelWithString: "AI Matrx Vault is not connected")
        title.font = .systemFont(ofSize: 16, weight: .semibold)

        let explanation = NSTextField(wrappingLabelWithString:
            "Credential setup is not available in this build. Return to the app after native enrollment is released.")
        explanation.textColor = .secondaryLabelColor

        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelRequest))
        let stack = NSStackView(views: [title, explanation, cancel])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            view.widthAnchor.constraint(greaterThanOrEqualToConstant: 360),
        ])
        self.view = view
    }

    override func prepareCredentialList(for serviceIdentifiers: [ASCredentialServiceIdentifier]) {
        // No identity store is populated until the separately reviewed enrollment
        // and protected-material contracts exist.  Showing this view is safer
        // than fabricating a credential or silently failing the system request.
    }

    @objc private func cancelRequest() {
        extensionContext.cancelRequest(withError: NSError(
            domain: ASExtensionErrorDomain,
            code: ASExtensionError.userCanceled.rawValue
        ))
    }
}
