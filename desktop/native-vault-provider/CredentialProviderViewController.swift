import AppKit
import AuthenticationServices
import CryptoKit
@preconcurrency import LocalAuthentication
import Security

private extension Data { func urlSafeBase64() -> String { base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") } }
private func constantTimeEqual(_ left: String, _ right: String) -> Bool {
    let lhs = Array(left.utf8), rhs = Array(right.utf8)
    var mismatch = lhs.count ^ rhs.count
    let width = max(lhs.count, rhs.count)
    for index in 0..<width { mismatch |= Int((index < lhs.count ? lhs[index] : 0) ^ (index < rhs.count ? rhs[index] : 0)) }
    return mismatch == 0
}

private final class Transaction {
    let verifier: String; let state: String
    init() throws { verifier = try Self.random(); state = try Self.random() }
    private static func random() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw EnrollmentError.message("Secure random generation failed. Close and reopen AI Matrx Vault.") }
        return Data(bytes).urlSafeBase64()
    }
}


@MainActor
final class CredentialProviderViewController: ASCredentialProviderViewController, ASWebAuthenticationPresentationContextProviding {
    private var activeOperation: NativeVaultEnrollmentOperation?
    private var startingConnect = false
    private let connectionAdmission = NativeVaultCurrentConnectionAdmission()
    var sessionAccess = NativeVaultSessionAccess()
    var nativePasswordTransport: NativeVaultPasswordTransporting = NativeVaultPasswordTransport()
    let nativePasswordCoordinator = NativePasswordOperationCoordinator()
    // Test seam for the actual provider callbacks. Production leaves these nil
    // and uses LA, Keychain, ProviderStore, native UI, and extensionContext.
    var nativePasswordKeyOverride: String?
    var nativePasswordAuthorize: ((@escaping (Bool) -> Void) -> Void)?
    var nativePasswordAcquire: ((@escaping (Result<NativeVaultSessionAccess.Grant, Error>) -> Void) -> Void)?
    var nativePasswordCurrentState: (@Sendable () -> NativePasswordCurrentState)?
    var nativePasswordCompletionLock: (((NativePasswordCurrentState) throws -> Void) throws -> Void)?
    var nativePasswordOrganizationChoice: (([NativeOrganization]) -> Int?)?
    var nativePasswordMatchChoice: (([NativePasswordMatch]) -> Int?)?
    var nativePasswordCancelSink: ((NSError) -> Void)?
    var nativePasswordCompleteSink: (((String, String), @escaping () -> Void) -> Void)?
    var nativePasswordSelectedBinding: (item: String, organization: String, service: ASCredentialServiceIdentifier, digest: String)?
    // Narrow test boundary for actual selected-credential callbacks. Production
    // always parses the signed record under the ProviderStore lock.
    var nativeIdentityBindingOverride: ((String) -> NativeVaultIdentityBinding?)?
    let nativeIdentitySynchronizer = NativeVaultIdentitySynchronizer()
    lazy var nativePasskeyCoordinator = NativeVaultPasskeyCoordinator(
        sessionAccess: sessionAccess,
        key: { [weak self] in self?.nativePasswordKeyOverride ?? (Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String) },
        cancel: { [weak self] message in self?.cancelPasskey(message) },
        completeRegistration: { [weak self] credential in self?.extensionContext.completeRegistrationRequest(using: credential, completionHandler: nil) },
        completeAssertion: { [weak self] credential in self?.extensionContext.completeAssertionRequest(using: credential, completionHandler: nil) },
        isExternalCurrent: { [weak self] in self?.nativeRequest.isCurrent ?? false },
        requestLifetime: { [weak self] in self?.nativeRequest ?? NativeVaultRequestLifetime() }
    )
    fileprivate(set) var nativeRequest = NativeVaultRequestLifetime()
    func replaceNativeRequest() {
        nativeRequest.cancel()
        nativeRequest = NativeVaultRequestLifetime()
        nativePasskeyCoordinator.cancelCurrent()
        nativeIdentitySynchronizer.cancel()
        nativePasswordSelectedBinding = nil
        if let old = nativePasswordCoordinator.active { _ = nativePasswordCoordinator.cancel(old) }
        nativePasswordTransport.cancel()
        sessionAccess.cancel()
        _ = finishOperation()
        finishConnectionOperation()
        // The extension host owns this window; never close it ourselves.
        window = nil
        connectionStatus = nil; connectButton = nil; retryButton = nil
    }
    func ownNativeContext(_ context: LAContext) { nativeRequest.own { context.invalidate() } }
    private func enrollmentRequest(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let lifetime = nativeRequest
        let transport = BoundedTransport { result in guard lifetime.isCurrent else { return }; completion(result) }
        lifetime.own { transport.cancel() }
        guard lifetime.isCurrent else { return }
        transport.start(request)
    }
    private var webSession: ASWebAuthenticationSession?
    private var window: NSWindow?
    private var connectionStatus: NSTextField?
    private var connectButton: NSButton?
    private var retryButton: NSButton?

    override func prepareInterfaceForExtensionConfiguration() { replaceNativeRequest(); showConfiguration() }
    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 480, height: 440))
        preferredContentSize = view.frame.size
    }
    override func prepareCredentialList(for serviceIdentifiers: [ASCredentialServiceIdentifier]) {
        beginPasswordRequest(serviceIdentifiers)
    }
    override func prepareCredentialList(for serviceIdentifiers: [ASCredentialServiceIdentifier], requestParameters: ASPasskeyCredentialRequestParameters) {
        replaceNativeRequest()
        nativePasskeyCoordinator.assertList(requestParameters)
    }
    override func prepareInterface(forPasskeyRegistration registrationRequest: any ASCredentialRequest) {
        replaceNativeRequest()
        nativePasskeyCoordinator.register(registrationRequest)
    }
    override func prepareInterfaceToProvideCredential(for credentialRequest: any ASCredentialRequest) {
        replaceNativeRequest()
        if let request = credentialRequest as? ASPasskeyCredentialRequest { beginSelectedPasskey(request) }
        else if let request = credentialRequest as? ASPasswordCredentialRequest, let identity = request.credentialIdentity as? ASPasswordCredentialIdentity { beginSelectedPassword(identity) }
        else { super.prepareInterfaceToProvideCredential(for: credentialRequest) }
    }

    override func provideCredentialWithoutUserInteraction(for credentialIdentity: ASPasswordCredentialIdentity) {
        replaceNativeRequest()
        // This direct-list provider intentionally has no identity index yet.
        let error = NSError(domain: ASExtensionErrorDomain, code: NativePasswordStage.interactionRequiredCode)
        if let sink = nativePasswordCancelSink { sink(error) } else { extensionContext.cancelRequest(withError: error) }
    }

    override func provideCredentialWithoutUserInteraction(for credentialRequest: any ASCredentialRequest) {
        replaceNativeRequest()
        let error = NSError(domain: ASExtensionErrorDomain, code: NativePasswordStage.interactionRequiredCode)
        if let sink = nativePasswordCancelSink { sink(error) } else { extensionContext.cancelRequest(withError: error) }
    }

    override func performWithoutUserInteractionIfPossible(passkeyRegistration: ASPasskeyCredentialRequest) {
        replaceNativeRequest()
        extensionContext.cancelRequest(withError: NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userInteractionRequired.rawValue))
    }

    private func cancelPasskey(_ message: String) {
        extensionContext.cancelRequest(withError: NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userCanceled.rawValue, userInfo: [NSLocalizedDescriptionKey: message]))
    }

    private func showConfiguration() {
        let content = view
        content.subviews.forEach { $0.removeFromSuperview() }
        let title = NSTextField(labelWithString: "Connect AI Matrx Vault"); title.font = .systemFont(ofSize: 18, weight: .semibold)
        let text = NSTextField(wrappingLabelWithString: "Connect your AI Matrx account, then choose which organization supplies password and passkey suggestions on this Mac."); text.textColor = .secondaryLabelColor
        let status = NSTextField(wrappingLabelWithString: "Checking the current provider connection…"); status.textColor = .secondaryLabelColor
        let connect = NSButton(title: "Connect account", target: self, action: #selector(begin)); let scope = NSButton(title: "Show suggestions from…", target: self, action: #selector(selectSuggestionScope)); let retry = NSButton(title: "Refresh suggestions", target: self, action: #selector(refreshSuggestions)); let disconnect = NSButton(title: "Disconnect", target: self, action: #selector(disconnect)); let cancelButton = NSButton(title: "Cancel", target: self, action: #selector(cancel))
        let stack = NSStackView(views: [title, text, status, connect, scope, retry, disconnect, cancelButton]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12; stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -24),
            text.widthAnchor.constraint(equalTo: stack.widthAnchor),
            status.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
        self.connectionStatus = status; self.connectButton = connect; self.retryButton = retry
        loadCurrentConnection()
    }
    @objc private func begin() {
        do {
            guard activeOperation == nil, !startingConnect else {
                setConnectionStatus("An account connection is already in progress.")
                return
            }
            guard let key = Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String, key.validToken else { throw EnrollmentError.message("This build has no public Vault configuration. Install an updated AI Matrx build.") }
            let transaction = try Transaction()
            let context = LAContext(); var detail: NSError?
            guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &detail) else { throw EnrollmentError.message("Vault protection is unavailable on this Mac.") }
            startingConnect = true; setConnectionBusy(true)
            ownNativeContext(context)
            let lifetime = nativeRequest
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Protect your AI Matrx Vault session") { [weak self] allowed, _ in
            guard lifetime.isCurrent else { return }
                guard allowed else { Task { @MainActor in guard lifetime.isCurrent else { return }; self?.finishStartingConnect() }; return }
                DispatchQueue.global(qos: .userInitiated).async {
                    do {
                        guard lifetime.isCurrent else { return }
                        let store = try ProviderStore()
                        let state = try store.initializeExplicitConnect {
                            guard lifetime.isCurrent else { throw EnrollmentError.message("Vault request was cancelled.") }
                            try NativeVaultPrivateSession().delete(context: context)
                        }
                        DispatchQueue.main.async { guard lifetime.isCurrent else { return }; self?.startAuthorization(transaction, generation: state.generation, key: key) }
                    } catch { DispatchQueue.main.async { guard lifetime.isCurrent else { return }; self?.finishStartingConnect(); self?.showError(error) } }
                }
            }
        } catch { showError(error) }
    }
    private func startAuthorization(_ transaction: Transaction, generation: String, key: String) {
        do {
            defer { finishStartingConnect() }
            guard let operation = NativeVaultEnrollmentLifecycle.begin(verifier: transaction.verifier, state: transaction.state, generation: generation, active: activeOperation) else {
                throw EnrollmentError.message("An account connection is already in progress.")
            }
            activeOperation = operation
            let challenge = NativeVaultEnrollmentLifecycle.pkceChallenge(verifier: transaction.verifier)
            var components = URLComponents(url: authorizeURL, resolvingAgainstBaseURL: false)!; components.queryItems = [URLQueryItem(name: "response_type", value: "code"), URLQueryItem(name: "client_id", value: clientID), URLQueryItem(name: "redirect_uri", value: callback.absoluteString), URLQueryItem(name: "state", value: transaction.state), URLQueryItem(name: "code_challenge", value: challenge), URLQueryItem(name: "code_challenge_method", value: "S256"), URLQueryItem(name: "scope", value: "openid email offline_access")]
            guard let url = components.url, let anchor = view.window else { throw EnrollmentError.message("Vault setup needs an active provider window. Try again.") }
            window = anchor
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callback.scheme!) { [weak self, operationID = operation.id] url, error in
                Task { @MainActor in self?.complete(operationID: operationID, url: url, error: error, key: key) }
            }
            session.presentationContextProvider = self; session.prefersEphemeralWebBrowserSession = true; webSession = session
            guard session.start() else { throw EnrollmentError.message("Could not open account connection. Try again.") }
        } catch { finishOperation(); showError(error) }
    }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // startAuthorization retains this exact extension window before
        // starting the session, so this cannot borrow an unrelated app window.
        return window!
    }
    private func complete(operationID: UUID, url: URL?, error: Error?, key: String) {
        guard let operation = activeOperation, operation.id == operationID else { return }
        webSession = nil
        guard error == nil, let url else { finishOperation(operationID); return }
        do {
            let callback = try NativeVaultEnrollmentLifecycle.callbackCode(url, for: operation, callback: callback)
            exchange(code: callback.code, verifier: callback.verifier, operation: operation, key: key)
        } catch { finishOperation(operationID); showError(error) }
    }
    private func exchange(code: String, verifier: String, operation: NativeVaultEnrollmentOperation, key: String) {
        var request = URLRequest(url: tokenURL); request.httpMethod = "POST"; request.timeoutInterval = 10; request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept"); request.setValue(key, forHTTPHeaderField: "apikey")
        request.httpBody = formBody([
            ("grant_type", "authorization_code"),
            ("client_id", clientID),
            ("redirect_uri", callback.absoluteString),
            ("code", code),
            ("code_verifier", verifier),
        ])
        enrollmentRequest(request) { [weak self] result in
            do { let (data, http) = try result.get(); guard http.statusCode == 200 else { throw connectionResponseError(http.statusCode) }; DispatchQueue.main.async { self?.authenticateAndPersist(try? VaultEnvelopeCodec.token(data), operation: operation, key: key) } } catch { DispatchQueue.main.async { guard self?.activeOperation?.id == operation.id else { return }; self?.finishOperation(operation.id); self?.showError(error) } }
        }
    }
    private func authenticateAndPersist(_ token: Token?, operation: NativeVaultEnrollmentOperation, key: String) {
        guard activeOperation?.id == operation.id else { return }
        guard let token else { finishOperation(operation.id); showError(EnrollmentError.message("Account response was rejected. Try again.")); return }
        let context = LAContext(); var detail: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &detail) else { finishOperation(operation.id); showError(EnrollmentError.message("Vault protection is unavailable on this Mac.")); return }
        ownNativeContext(context)
        let lifetime = nativeRequest
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Protect your AI Matrx Vault session") { [weak self] allowed, _ in
            guard lifetime.isCurrent else { return }
            Task { @MainActor in
                guard lifetime.isCurrent else { return }
                guard allowed else { self?.finishOperation(operation.id); return }
                self?.userinfo(token, operation: operation, key: key, context: context)
            }
        }
    }
    private func userinfo(_ token: Token, operation: NativeVaultEnrollmentOperation, key: String, context: LAContext) {
        var request = URLRequest(url: userinfoURL); request.timeoutInterval = 10; request.setValue("Bearer \(token.access_token)", forHTTPHeaderField: "Authorization"); request.setValue(key, forHTTPHeaderField: "apikey"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        enrollmentRequest(request) { [weak self] result in
            do {
                let (data, http) = try result.get()
                guard http.statusCode == 200 else { throw connectionResponseError(http.statusCode) }
                let identity = try VaultEnvelopeCodec.userinfo(data)
                let store = try ProviderStore()
                let committed = try store.locked { old in
                    try operation.commitGuard.commit {
                        guard old.generation == operation.generation else { throw EnrollmentError.message("A host account change cancelled Vault connection. Start again.") }
                        let next = PublicState(version: 2, generation: UUID().canonical, host_subject: old.host_subject, provider_subject: nil)
                        let expiry = Int64(Date().timeIntervalSince1970 * 1000) + Int64(token.expires_in) * 1000
                        try store.write(next)
                        try NativeVaultPrivateSession().save(PrivateSession(version: 1, phase: "active", subject: identity.sub, generation: next.generation, access_token: token.access_token, refresh_token: token.refresh_token, expires_at_ms: expiry), context: context)
                        try store.write(PublicState(version: 2, generation: next.generation, host_subject: next.host_subject, provider_subject: identity.sub))
                    }
                }
                DispatchQueue.main.async {
                    guard committed, let self, NativeVaultEnrollmentLifecycle.mayCompleteConfiguration(active: self.activeOperation, operationID: operation.id) else { return }
                    self.finishOperation(operation.id, cancel: false)
                    self.nativeRequest.cancel()
                    self.extensionContext.completeExtensionConfigurationRequest()
                }
            } catch { DispatchQueue.main.async { guard self?.activeOperation?.id == operation.id else { return }; self?.finishOperation(operation.id); self?.showError(error) } }
        }
    }
    // Configuration and password filling both use the provider-owned session
    // primitive. It returns only an identity label here, never a token.
    private func loadCurrentConnection() {
        guard activeOperation == nil, !startingConnect, connectionAdmission.admit() else {
            setConnectionStatus("An account connection is already in progress.")
            return
        }
        setConnectionBusy(true)
        guard let key = Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String, key.validToken else {
            setConnectionStatus("This build has no public Vault configuration. Install an updated AI Matrx build.")
            finishConnectionOperation()
            return
        }
        let privateSession = NativeVaultPrivateSession()
        let context: LAContext
        do { context = try privateSession.authenticatedContext(reason: "Check your AI Matrx Vault connection") }
        catch { setConnectionStatus("Vault protection is unavailable on this Mac."); finishConnectionOperation(); return }
        ownNativeContext(context)
        let lifetime = nativeRequest
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Check your AI Matrx Vault connection") { [weak self] allowed, _ in
            guard lifetime.isCurrent else { return }
            guard allowed else { Task { @MainActor in guard lifetime.isCurrent else { return }; self?.setConnectionStatus("Unlock Vault protection to check the connected account."); self?.finishConnectionOperation() }; return }
            Task { @MainActor [weak self] in
                guard let self, lifetime.isCurrent else { return }
                self.sessionAccess.acquire(key: key, context: context, lifetime: lifetime) { result in
                    Task { @MainActor in
                    guard lifetime.isCurrent else { return }
                    switch result {
                    case let .success(grant): self.nativeIdentitySynchronizer.migrateV1(subject: grant.subject, generation: grant.generation) { migration in switch migration { case .success: self.showSuggestionStatus(subject: grant.subject); case .failure: self.setConnectionStatus("Vault suggestions need refresh.") } }
                    case let .failure(error): self.setConnectionFailure(error)
                    }
                    self.finishConnectionOperation()
                    }
                }
            }
        }
    }
    private func finishStartingConnect() {
        startingConnect = false
        setConnectionBusy(false)
    }
    private func finishConnectionOperation() {
        connectionAdmission.finish { setConnectionBusy(false) }
    }
    @discardableResult
    private func finishOperation(_ id: UUID? = nil, cancel: Bool = true) -> NativeVaultOperationCommitGuard.CancellationResult? {
        guard id == nil || activeOperation?.id == id else { return nil }
        let result = cancel ? activeOperation?.commitGuard.cancel() : nil
        webSession?.cancel()
        webSession = nil
        activeOperation = nil
        finishStartingConnect()
        return result
    }
    private func setConnectionBusy(_ busy: Bool) {
        connectButton?.isEnabled = !busy
        retryButton?.isEnabled = !busy
    }
    @objc private func selectSuggestionScope() {
        guard let key = Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String, key.validToken else { setConnectionStatus("This build has no public Vault configuration. Install an updated AI Matrx build."); return }
        let context: LAContext; do { context = try NativeVaultPrivateSession().authenticatedContext(reason: "Choose AI Matrx Vault suggestions") } catch { setConnectionStatus("Vault protection is unavailable on this Mac."); return }
        ownNativeContext(context); let lifetime = nativeRequest
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Choose AI Matrx Vault suggestions") { [weak self] allowed, _ in guard allowed, lifetime.isCurrent else { return }; self?.sessionAccess.acquire(key: key, context: context, lifetime: lifetime) { result in Task { @MainActor in
            guard let self, lifetime.isCurrent, case let .success(grant) = result else { self?.setConnectionStatus("Vault connection needs reconnect."); return }
            // A legacy public-state envelope is migrated before its scope can
            // be selected, so no picker result can be committed to v1 state.
            self.nativeIdentitySynchronizer.migrateV1(subject: grant.subject, generation: grant.generation) { migration in
                guard lifetime.isCurrent else { return }
                guard case .success = migration else { self.setConnectionStatus("Vault suggestions need refresh."); return }
                self.presentSuggestionScopePicker(grant: grant, lifetime: lifetime)
            }
        } } }
    }

    func presentSuggestionScopePicker(grant: NativeVaultSessionAccess.Grant, lifetime: NativeVaultRequestLifetime) {
        let access = sessionAccess
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent("api/auth/organizations")); request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        nativePasswordTransport.send(request) { [weak self] raw in
            access.reconcileResponse(raw, grant: grant, lifetime: lifetime) { response in Task { @MainActor in
            guard let self, lifetime.isCurrent else { return }
            do {
                let (data, http) = try response.get()
                guard http.statusCode == 200 else { throw EnrollmentError.message("Vault organizations are unavailable. Try again.") }
                let values = try NativeOrganizationCodec.organizations(data, subject: grant.subject)
                guard !values.isEmpty else { throw EnrollmentError.message("Your account has no available organization.") }
                let alert = NSAlert(); alert.messageText = "Show suggestions from"; alert.informativeText = "Device suggestions expose websites and usernames to Apple's local suggestion service."
                let picker = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 340, height: 28)); values.forEach { picker.addItem(withTitle: $0.name) }
                alert.accessoryView = picker; alert.addButton(withTitle: "Use organization"); alert.addButton(withTitle: "Cancel")
                guard alert.runModal() == .alertFirstButtonReturn else { return }
                let selected = values[picker.indexOfSelectedItem]
                self.setConnectionStatus("Refreshing suggestions…")
                self.nativeIdentitySynchronizer.refresh(accessToken: grant.accessToken, subject: grant.subject, generation: grant.generation, organization: selected.id, send: self.nativePasswordTransport.send, isCurrent: { lifetime.isCurrent }) { result in
                    switch result {
                    case let .success(state): self.setConnectionStatus("Suggestions updated: \(state.suggestions.count) installed, \(state.suggestions.unsupported_count) unavailable.")
                    case .failure: self.setConnectionStatus("Suggestion scope could not be changed. Try again.")
                    }
                }
            } catch { self.setConnectionStatus("Vault organizations are unavailable. Try again.") }
        } }
        }
    }

    /// A credential-list callback never waits for refresh. It opportunistically
    /// updates the selected scope only when a current v2 scope already exists.
    func refreshSuggestionsForCredentialList(grant: NativeVaultSessionAccess.Grant, lifetime: NativeVaultRequestLifetime) {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let organization = try? ProviderStore(mode: .providerAccess).locked { state -> String? in
                guard lifetime.isCurrent, state.version == 2, state.generation == grant.generation, state.provider_subject == grant.subject else { return nil }
                return state.suggestions.organization_id
            }
            DispatchQueue.main.async {
                guard let self, lifetime.isCurrent, let organization else { return }
                self.nativeIdentitySynchronizer.refresh(accessToken: grant.accessToken, subject: grant.subject, generation: grant.generation, organization: organization, send: self.nativePasswordTransport.send, isCurrent: { lifetime.isCurrent }) { _ in }
            }
        }
    }

    @objc private func refreshSuggestions() {
        guard let key = Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String, key.validToken else { setConnectionStatus("This build has no public Vault configuration. Install an updated AI Matrx build."); return }
        let context: LAContext
        do { context = try NativeVaultPrivateSession().authenticatedContext(reason: "Refresh AI Matrx Vault suggestions") } catch { setConnectionStatus("Vault protection is unavailable on this Mac."); return }
        ownNativeContext(context); let lifetime = nativeRequest
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Refresh AI Matrx Vault suggestions") { [weak self] allowed, _ in
            guard allowed, lifetime.isCurrent else { return }
            self?.sessionAccess.acquire(key: key, context: context, lifetime: lifetime) { result in Task { @MainActor in
                guard let self, lifetime.isCurrent else { return }; guard case let .success(grant) = result else { self.setConnectionStatus("Vault connection needs reconnect."); return }
                let choose: (String?) = (try? ProviderStore(mode: .providerAccess).locked { $0.suggestions.organization_id }) ?? nil
                guard let organization = choose else { self.setConnectionStatus("Choose an organization before refreshing suggestions."); return }
                self.setConnectionStatus("Refreshing suggestions…")
                self.nativeIdentitySynchronizer.refresh(accessToken: grant.accessToken, subject: grant.subject, generation: grant.generation, organization: organization, send: self.nativePasswordTransport.send, isCurrent: { lifetime.isCurrent }) { result in
                    switch result { case let .success(state): self.setConnectionStatus("Suggestions updated: \(state.suggestions.count) installed, \(state.suggestions.unsupported_count) unavailable."); case .failure: self.setConnectionStatus("Suggestions could not be refreshed. Try again.") }
                }
            } }
        }
    }

    @objc private func retryCurrentConnection() {
        guard activeOperation == nil, !startingConnect else {
            setConnectionStatus("An account connection is already in progress.")
            return
        }
        loadCurrentConnection()
    }
    private func setConnectionStatus(_ value: String) {
        let lifetime = nativeRequest
        DispatchQueue.main.async { [weak self] in guard lifetime.isCurrent else { return }; self?.connectionStatus?.stringValue = value }
    }
    private func setConnectionFailure(_ error: Error) {
        let message = (error as? LocalizedError)?.errorDescription ?? "Vault connection is temporarily unavailable. Try again."
        setConnectionStatus(message)
    }
    private func showSuggestionStatus(subject: String) {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let state = try? ProviderStore(mode: .providerAccess).locked { $0 }
            DispatchQueue.main.async { guard let self, state?.provider_subject == subject else { return }
                guard let suggestions = state?.suggestions, state?.version == 2 else { self.setConnectionStatus("Connected account: \(subject). Suggestions need refresh."); return }
                let lastRefresh: String
                if let refreshedAt = suggestions.refreshed_at_ms, refreshedAt > 0 {
                    lastRefresh = DateFormatter.localizedString(from: Date(timeIntervalSince1970: TimeInterval(refreshedAt) / 1000), dateStyle: .short, timeStyle: .short)
                } else { lastRefresh = "not yet refreshed" }
                self.setConnectionStatus("Connected account: \(subject). Suggestions: \(suggestions.status), \(suggestions.count) installed, \(suggestions.unsupported_count) unavailable. Last refreshed: \(lastRefresh).")
            }
        }
    }
    private func showError(_ error: Error) {
        let lifetime = nativeRequest
        DispatchQueue.main.async { guard lifetime.isCurrent else { return }; NSAlert(error: error).runModal() }
    }
    @objc private func disconnect() {
        replaceNativeRequest()
        let privateSession = NativeVaultPrivateSession()
        let context: LAContext
        do { context = try privateSession.authenticatedContext(reason: "Disconnect your AI Matrx Vault session") }
        catch { showError(error); return }
        ownNativeContext(context)
        let lifetime = nativeRequest
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Disconnect your AI Matrx Vault session") { [weak self] allowed, _ in
            guard lifetime.isCurrent else { return }
            guard allowed else { return }
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let store = try ProviderStore()
                    var deletionFailure: Error?
                    try store.locked { old in
                        // Public invalidation fences stale bytes before the exact,
                        // authenticated Keychain deletion; no network logout here.
                        let cleared = PublicState(version: 2, generation: UUID().canonical, host_subject: old.host_subject, provider_subject: nil)
                        try store.write(cleared)
                        do { try privateSession.delete(context: context) }
                        catch { deletionFailure = error }
                        // Do not release the shared generation lock while a
                        // callback may still clear an identity for this provider.
                        try ProviderIdentityIndex.system.clearWhileLocked()
                    }
                    if let deletionFailure { throw deletionFailure }
                } catch { DispatchQueue.main.async { guard lifetime.isCurrent else { return }; self?.showError(error) } }
            }
        }
    }
    private func beginSelectedPasskey(_ request: ASPasskeyCredentialRequest) {
        let lifetime = nativeRequest; let record = (request.credentialIdentity as? ASPasskeyCredentialIdentity)?.recordIdentifier ?? ""
        let bindingOverride = nativeIdentityBindingOverride
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let binding = bindingOverride?(record) ?? ((try? ProviderStore(mode: .providerAccess).locked { NativeVaultIdentityRecord.parse(record, state: $0) }) ?? nil)
            DispatchQueue.main.async { guard let self, lifetime.isCurrent, let binding, binding.kind == .passkey, let passkey = binding.passkey else { self?.cancelPasskey("The selected passkey is no longer available."); return }; self.nativePasskeyCoordinator.assert(request, expectedBinding: (binding.item, passkey)) }
        }
    }

    @objc private func cancel() {
        let result = finishOperation()
        replaceNativeRequest()
        if result == .alreadyCommitted {
            extensionContext.completeExtensionConfigurationRequest()
        } else {
            extensionContext.cancelRequest(withError: NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userCanceled.rawValue))
        }
    }
}
