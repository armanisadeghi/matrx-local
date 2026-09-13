import AppKit
import AuthenticationServices
import CryptoKit
import LocalAuthentication
import Security

// Provider-owned OAuth. The host never receives a token or Keychain handle.
private let clientID = "d8a02629-f5f2-4064-ab26-31da07f082fc"
private let callback = URL(string: "matrx-vault-provider://oauth/callback")!
private let authorizeURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/authorize")!
private let tokenURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/token")!
private let userinfoURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/userinfo")!
private let keychainService = "com.aimatrx.desktop.vault-provider.session"
private let keychainAccount = "current-session"
private let keychainGroup = "JH83UH9P4D.com.aimatrx.desktop.vault-provider"


/// URLSession's convenience completion handler has already accumulated the
/// response. This delegate refuses redirects and cancels as soon as the fixed
/// envelope limit is crossed.
private final class BoundedTransport: NSObject, URLSessionDataDelegate {
    private var data = Data(); private let limit = 64 * 1024
    private let completion: (Result<(Data, HTTPURLResponse), Error>) -> Void
    init(_ completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) { self.completion = completion }
    func start(_ request: URLRequest) {
        let config = URLSessionConfiguration.ephemeral; config.timeoutIntervalForRequest = 10; config.timeoutIntervalForResource = 10
        let session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        session.dataTask(with: request).resume()
    }
    func urlSession(_: URLSession, task _: URLSessionTask, willPerformHTTPRedirection _: HTTPURLResponse, newRequest _: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) { guard data.count <= limit - chunk.count else { dataTask.cancel(); return }; data.append(chunk) }
    func urlSession(_: URLSession, task _: URLSessionTask, didCompleteWithError error: Error?) {
        if let error { completion(.failure(error)); return }
        guard let response = dataTaskResponse() else { completion(.failure(EnrollmentError.message("Account connection is unavailable. Try again."))); return }
        completion(.success((data, response)))
    }
    private var response: HTTPURLResponse?
    func urlSession(_: URLSession, dataTask _: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) { self.response = response as? HTTPURLResponse; completionHandler(response.expectedContentLength > Int64(limit) ? .cancel : .allow) }
    private func dataTaskResponse() -> HTTPURLResponse? { response }
}

private extension Data { func urlSafeBase64() -> String { base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") } }
private func constantTimeEqual(_ left: String, _ right: String) -> Bool {
    let lhs = Array(left.utf8), rhs = Array(right.utf8)
    var mismatch = lhs.count ^ rhs.count
    let width = max(lhs.count, rhs.count)
    for index in 0..<width { mismatch |= Int((index < lhs.count ? lhs[index] : 0) ^ (index < rhs.count ? rhs[index] : 0)) }
    return mismatch == 0
}

private extension ProviderStore {
    func deletePrivate(context: LAContext) throws {
        context.interactionNotAllowed = true
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
            kSecAttrAccessGroup as String: keychainGroup,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
            kSecUseAuthenticationContext as String: context,
        ]
        let result = SecItemDelete(query as CFDictionary)
        guard result == errSecSuccess || result == errSecItemNotFound else {
            throw EnrollmentError.message("Could not clear the Vault session. Reconnect the provider.")
        }
    }
    func save(_ value: PrivateSession, context: LAContext) throws {
        let data = try JSONEncoder().encode(value); guard data.count <= 48 * 1024 else { throw EnrollmentError.message("Vault session is too large. Reconnect the provider.") }
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, .userPresence, nil) else { throw EnrollmentError.message("Could not protect the Vault session.") }
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService, kSecAttrAccount as String: keychainAccount, kSecAttrAccessGroup as String: keychainGroup, kSecUseDataProtectionKeychain as String: true, kSecAttrSynchronizable as String: false]
        try deletePrivate(context: context)
        var query = base; query[kSecValueData as String] = data; query[kSecAttrAccessControl as String] = access; query[kSecUseAuthenticationContext as String] = context
        guard SecItemAdd(query as CFDictionary, nil) == errSecSuccess else { throw EnrollmentError.message("Could not save the Vault session. Reconnect the provider.") }
    }
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


final class CredentialProviderViewController: ASCredentialProviderViewController, ASWebAuthenticationPresentationContextProviding {
    private var transaction: Transaction?
    private var enrollmentGeneration: String?
    private var webSession: ASWebAuthenticationSession?
    private var window: NSWindow?

    override func prepareInterfaceForExtensionConfiguration() { showConfiguration() }
    override func loadView() { view = NSView() }
    override func prepareCredentialList(for serviceIdentifiers: [ASCredentialServiceIdentifier]) { }

    private func showConfiguration() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 220), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        let title = NSTextField(labelWithString: "Connect AI Matrx Vault"); title.font = .systemFont(ofSize: 18, weight: .semibold)
        let text = NSTextField(wrappingLabelWithString: "Connect your AI Matrx account to configure this credential provider. This does not enable credential filling yet."); text.textColor = .secondaryLabelColor
        let connect = NSButton(title: "Connect account", target: self, action: #selector(begin)); let retry = NSButton(title: "Retry", target: self, action: #selector(begin)); let disconnect = NSButton(title: "Disconnect", target: self, action: #selector(disconnect)); let cancelButton = NSButton(title: "Cancel", target: self, action: #selector(cancel))
        let stack = NSStackView(views: [title, text, connect, retry, disconnect, cancelButton]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12; stack.translatesAutoresizingMaskIntoConstraints = false
        let content = NSView(); content.addSubview(stack); window.contentView = content
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24), stack.centerYAnchor.constraint(equalTo: content.centerYAnchor)])
        self.window = window; window.makeKeyAndOrderFront(nil)
    }
    @objc private func begin() {
        do {
            guard let key = Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String, key.validToken else { throw EnrollmentError.message("This build has no public Vault configuration. Install an updated AI Matrx build.") }
            let transaction = try Transaction()
            let context = LAContext(); var detail: NSError?
            guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &detail) else { throw EnrollmentError.message("Vault protection is unavailable on this Mac.") }
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Protect your AI Matrx Vault session") { [weak self] allowed, _ in
                guard allowed else { return }
                DispatchQueue.global(qos: .userInitiated).async {
                    do {
                        let store = try ProviderStore()
                        let state = try store.initializeExplicitConnect { try store.deletePrivate(context: context) }
                        DispatchQueue.main.async { self?.startAuthorization(transaction, generation: state.generation, key: key) }
                    } catch { DispatchQueue.main.async { self?.showError(error) } }
                }
            }
        } catch { showError(error) }
    }
    private func startAuthorization(_ transaction: Transaction, generation: String, key: String) {
        do {
            self.transaction = transaction
            enrollmentGeneration = generation
            let challenge = Data(SHA256.hash(data: Data(transaction.verifier.utf8))).urlSafeBase64()
            var components = URLComponents(url: authorizeURL, resolvingAgainstBaseURL: false)!; components.queryItems = [URLQueryItem(name: "response_type", value: "code"), URLQueryItem(name: "client_id", value: clientID), URLQueryItem(name: "redirect_uri", value: callback.absoluteString), URLQueryItem(name: "state", value: transaction.state), URLQueryItem(name: "code_challenge", value: challenge), URLQueryItem(name: "code_challenge_method", value: "S256"), URLQueryItem(name: "scope", value: "openid email offline_access")]
            guard let url = components.url, window != nil else { throw EnrollmentError.message("Vault setup needs an active provider window. Try again.") }
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callback.scheme!) { [weak self] url, error in self?.complete(url: url, error: error, key: key) }
            session.presentationContextProvider = self; session.prefersEphemeralWebBrowserSession = true; webSession = session
            guard session.start() else { throw EnrollmentError.message("Could not open account connection. Try again.") }
        } catch { showError(error) }
    }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor { window ?? NSApp.keyWindow ?? NSWindow() }
    private func complete(url: URL?, error: Error?, key: String) {
        defer { transaction = nil; webSession = nil }
        guard error == nil, let url, let transaction else { return }
        do {
            guard url.scheme == callback.scheme && url.host == callback.host && url.path == callback.path && url.port == nil && url.fragment == nil else { throw EnrollmentError.message("The account callback was rejected. Start connection again.") }
            let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard items.count == 2, Set(items.map(\.name)).count == 2, let returnedState = items.first(where: { $0.name == "state" })?.value, constantTimeEqual(returnedState, transaction.state), let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty else { throw EnrollmentError.message("The account callback was rejected. Start connection again.") }
            guard let generation = enrollmentGeneration else { throw EnrollmentError.message("Vault connection expired. Start again.") }
            enrollmentGeneration = nil
            exchange(code: code, verifier: transaction.verifier, generation: generation, key: key)
        } catch { showError(error) }
    }
    private func exchange(code: String, verifier: String, generation: String, key: String) {
        var request = URLRequest(url: tokenURL); request.httpMethod = "POST"; request.timeoutInterval = 10; request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept"); request.setValue(key, forHTTPHeaderField: "apikey")
        let parameters = ["grant_type": "authorization_code", "client_id": clientID, "redirect_uri": callback.absoluteString, "code": code, "code_verifier": verifier]
        let body = parameters.map { pair in
            let encoded = pair.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
            return "\(pair.key)=\(encoded)"
        }.joined(separator: "&")
        request.httpBody = body.data(using: .utf8)
        BoundedTransport { [weak self] result in
            do { let (data, http) = try result.get(); guard http.statusCode == 200 else { throw EnrollmentError.message("Account connection is unavailable. Try again.") }; self?.authenticateAndPersist(try VaultEnvelopeCodec.token(data), generation: generation, key: key) } catch { DispatchQueue.main.async { self?.showError(error) } }
        }.start(request)
    }
    private func authenticateAndPersist(_ token: Token, generation: String, key: String) {
        let context = LAContext(); var detail: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &detail) else { showError(EnrollmentError.message("Vault protection is unavailable on this Mac.")); return }
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Protect your AI Matrx Vault session") { [weak self] allowed, _ in guard allowed else { return }; self?.userinfo(token, generation: generation, key: key, context: context) }
    }
    private func userinfo(_ token: Token, generation: String, key: String, context: LAContext) {
        var request = URLRequest(url: userinfoURL); request.timeoutInterval = 10; request.setValue("Bearer \(token.access_token)", forHTTPHeaderField: "Authorization"); request.setValue(key, forHTTPHeaderField: "apikey"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        BoundedTransport { [weak self] result in
            do { let (data, http) = try result.get(); guard http.statusCode == 200 else { throw EnrollmentError.message("Could not verify this account. Try again.") }; let identity = try VaultEnvelopeCodec.userinfo(data); let store = try ProviderStore(); try store.locked { old in guard old.generation == generation else { throw EnrollmentError.message("A host account change cancelled Vault connection. Start again.") }; let next = PublicState(version: 1, generation: UUID().canonical, host_subject: old.host_subject, provider_subject: nil); try store.write(next); let expiry = Int64(Date().timeIntervalSince1970 * 1000) + Int64(token.expires_in) * 1000; try store.save(PrivateSession(version: 1, phase: "active", subject: identity.sub, generation: next.generation, access_token: token.access_token, refresh_token: token.refresh_token, expires_at_ms: expiry), context: context); try store.write(PublicState(version: 1, generation: next.generation, host_subject: next.host_subject, provider_subject: identity.sub)) }; DispatchQueue.main.async { self?.window?.close() } } catch { DispatchQueue.main.async { self?.showError(error) } }
        }.start(request)
    }
    private func showError(_ error: Error) { DispatchQueue.main.async { NSAlert(error: error).runModal() } }
    @objc private func disconnect() {
        do {
            let store = try ProviderStore()
            try store.locked { old in
                let cleared = PublicState(version: 1, generation: UUID().canonical, host_subject: old.host_subject, provider_subject: nil)
                try store.write(cleared)
                let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService, kSecAttrAccount as String: keychainAccount, kSecAttrAccessGroup as String: keychainGroup, kSecUseDataProtectionKeychain as String: true]
                // Local invalidation is authoritative; any server revocation is best-effort and only ever uses this provider session.
                SecItemDelete(query as CFDictionary)
            }
        } catch { showError(error) }
    }
    @objc private func cancel() { extensionContext.cancelRequest(withError: NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userCanceled.rawValue)) }
}
