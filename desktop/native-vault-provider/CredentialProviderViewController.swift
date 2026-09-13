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
private let groupID = "group.com.aimatrx.desktop.vault-status"
private let keychainService = "com.aimatrx.desktop.vault-provider.session"
private let keychainAccount = "current-session"
private let keychainGroup = "JH83UH9P4D.com.aimatrx.desktop.vault-provider"

private enum EnrollmentError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { value } else { nil } }
}
private struct PublicState: Codable { let version: Int; let generation: String; let host_subject: String?; let provider_subject: String? }
private struct PrivateSession: Codable { let version: Int; let phase: String; let subject: String; let generation: String; let access_token: String; let refresh_token: String; let expires_at_ms: Int64 }
private struct Token: Decodable { let access_token: String; let token_type: String; let expires_in: Int; let refresh_token: String; let scope: String? }
private struct Identity: Decodable { let sub: String; let email: String?; let email_verified: Bool? }

private indirect enum JSONValue { case object([String: JSONValue]), array([JSONValue]), string(String), number(String), bool(Bool), null }

/// Deliberately small JSON parser for fixed Auth/Keychain envelopes. It does
/// not normalize duplicate keys: each decoded key is checked in its owning
/// object, including keys expressed through Unicode escapes.
private struct StrictJSON {
    private var bytes: [UInt8]; private var index = 0
    init(_ data: Data) throws { guard data.count <= 64 * 1024 else { throw EnrollmentError.message("Account response was rejected. Try again.") }; bytes = Array(data) }
    mutating func parse() throws -> JSONValue { let value = try value(0); space(); guard index == bytes.count else { throw bad() }; return value }
    private mutating func value(_ depth: Int) throws -> JSONValue {
        guard depth <= 8 else { throw bad() }; space(); guard index < bytes.count else { throw bad() }
        switch bytes[index] { case 123: return .object(try object(depth + 1)); case 91: return .array(try array(depth + 1)); case 34: return .string(try string()); case 116: try literal("true"); return .bool(true); case 102: try literal("false"); return .bool(false); case 110: try literal("null"); return .null; case 45, 48...57: return .number(try number()); default: throw bad() }
    }
    private mutating func object(_ depth: Int) throws -> [String: JSONValue] {
        take(123); space(); var result: [String: JSONValue] = [:]; if accept(125) { return result }
        while true { space(); guard index < bytes.count, bytes[index] == 34 else { throw bad() }; let key = try string(); guard result[key] == nil else { throw bad() }; space(); take(58); result[key] = try value(depth); space(); if accept(125) { return result }; take(44) }
    }
    private mutating func array(_ depth: Int) throws -> [JSONValue] {
        take(91); space(); var result: [JSONValue] = []; if accept(93) { return result }
        while true { result.append(try value(depth)); space(); if accept(93) { return result }; take(44) }
    }
    private mutating func string() throws -> String {
        take(34); var scalars = String.UnicodeScalarView()
        while index < bytes.count { let byte = bytes[index]; index += 1; if byte == 34 { return String(scalars) }; if byte < 32 { throw bad() }; if byte != 92 { scalars.append(UnicodeScalar(byte)); continue }; guard index < bytes.count else { throw bad() }; let escape = bytes[index]; index += 1
            switch escape { case 34,92,47: scalars.append(UnicodeScalar(escape)); case 98: scalars.append("\u{08}"); case 102: scalars.append("\u{0c}"); case 110: scalars.append("\n"); case 114: scalars.append("\r"); case 116: scalars.append("\t"); case 117: let first = try hex4(); if (0xD800...0xDBFF).contains(first) { guard index + 1 < bytes.count, bytes[index] == 92, bytes[index+1] == 117 else { throw bad() }; index += 2; let second = try hex4(); guard (0xDC00...0xDFFF).contains(second) else { throw bad() }; scalars.append(UnicodeScalar(0x10000 + ((first - 0xD800) << 10) + second)!) } else { guard !(0xDC00...0xDFFF).contains(first), let scalar = UnicodeScalar(first) else { throw bad() }; scalars.append(scalar) }; default: throw bad() }
        }; throw bad()
    }
    private mutating func hex4() throws -> UInt32 { guard index + 4 <= bytes.count else { throw bad() }; var value: UInt32 = 0; for _ in 0..<4 { let c = bytes[index]; index += 1; let digit: UInt32; switch c { case 48...57: digit = UInt32(c - 48); case 65...70: digit = UInt32(c - 55); case 97...102: digit = UInt32(c - 87); default: throw bad() }; value = value * 16 + digit }; return value }
    private mutating func number() throws -> String { let start = index; _ = accept(45); guard index < bytes.count else { throw bad() }; if accept(48) { } else { guard digit19() else { throw bad() }; while digit() {} }; if accept(46) { guard digit() else { throw bad() }; while digit() {} }; if accept(69) || accept(101) { _ = accept(43) || accept(45); guard digit() else { throw bad() }; while digit() {} }; return String(decoding: bytes[start..<index], as: UTF8.self) }
    private mutating func literal(_ text: String) throws { for byte in text.utf8 { guard index < bytes.count, bytes[index] == byte else { throw bad() }; index += 1 } }
    private mutating func space() { while index < bytes.count, [9,10,13,32].contains(bytes[index]) { index += 1 } }
    private mutating func accept(_ byte: UInt8) -> Bool { guard index < bytes.count, bytes[index] == byte else { return false }; index += 1; return true }
    private mutating func take(_ byte: UInt8) { guard accept(byte) else { fatalError("strict JSON internal grammar error") } }
    private mutating func digit() -> Bool { guard index < bytes.count, bytes[index] >= 48, bytes[index] <= 57 else { return false }; index += 1; return true }
    private mutating func digit19() -> Bool { guard index < bytes.count, bytes[index] >= 49, bytes[index] <= 57 else { return false }; index += 1; return true }
    private func bad() -> Error { EnrollmentError.message("Account response was rejected. Try again.") }
}

private enum StrictEnvelope {
    static func object(_ data: Data, required: Set<String>, optional: Set<String>) throws -> [String: JSONValue] {
        var parser = try StrictJSON(data); guard case let .object(value) = try parser.parse(), required.isSubset(of: Set(value.keys)), Set(value.keys).isSubset(of: required.union(optional)) else { throw EnrollmentError.message("Account response was rejected. Try again.") }; return value
    }
}

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
private extension String { var validToken: Bool { !isEmpty && utf8.count <= 16 * 1024 && utf8.allSatisfy { $0 >= 0x21 && $0 <= 0x7e } } }
private extension UUID { var canonical: String { uuidString.lowercased() } }
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

/// The App Group stores only non-secret coordination metadata. Every operation
/// holds the file lock through its state transition; private session material
/// remains in the provider-only Data Protection Keychain group.
private final class ProviderStore {
    private let directory: URL
    init() throws {
        guard let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupID) else { throw EnrollmentError.message("The Vault provider App Group is unavailable. Install a signed AI Matrx build.") }
        directory = root.appendingPathComponent("NativeVault", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    func locked<T>(_ operation: (PublicState) throws -> T) throws -> T {
        let lock = directory.appendingPathComponent("state.lock")
        let fd = open(lock.path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw EnrollmentError.message("Vault setup is unavailable. Try again.") }
        defer { close(fd) }
        let deadline = Date().addingTimeInterval(2)
        while flock(fd, LOCK_EX | LOCK_NB) != 0 { if Date() >= deadline { throw EnrollmentError.message("Vault setup is busy. Try again.") }; Thread.sleep(forTimeInterval: 0.05) }
        defer { flock(fd, LOCK_UN) }
        return try operation(try read())
    }
    func read() throws -> PublicState {
        let file = directory.appendingPathComponent("state.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return PublicState(version: 1, generation: UUID().canonical, host_subject: nil, provider_subject: nil) }
        let data = try Data(contentsOf: file)
        guard data.count <= 2048 else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        _ = try StrictEnvelope.object(data, required: ["version", "generation", "host_subject", "provider_subject"], optional: [])
        let value = try JSONDecoder().decode(PublicState.self, from: data)
        guard value.version == 1, UUID(uuidString: value.generation)?.canonical == value.generation, value.host_subject.map({ UUID(uuidString: $0)?.canonical == $0 }) ?? true, value.provider_subject.map({ UUID(uuidString: $0)?.canonical == $0 }) ?? true else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        return value
    }
    func write(_ value: PublicState) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let temporary = directory.appendingPathComponent(".state-\(UUID().uuidString)")
        try encoder.encode(value).write(to: temporary, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        let final = directory.appendingPathComponent("state.json")
        if FileManager.default.fileExists(atPath: final.path) { _ = try FileManager.default.replaceItemAt(final, withItemAt: temporary) } else { try FileManager.default.moveItem(at: temporary, to: final) }
    }
    func save(_ value: PrivateSession, context: LAContext) throws {
        let data = try JSONEncoder().encode(value); guard data.count <= 48 * 1024 else { throw EnrollmentError.message("Vault session is too large. Reconnect the provider.") }
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, .userPresence, nil) else { throw EnrollmentError.message("Could not protect the Vault session.") }
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService, kSecAttrAccount as String: keychainAccount, kSecAttrAccessGroup as String: keychainGroup, kSecUseDataProtectionKeychain as String: true]
        SecItemDelete(base as CFDictionary)
        context.interactionNotAllowed = true
        var query = base; query[kSecValueData as String] = data; query[kSecAttrAccessControl as String] = access; query[kSecUseAuthenticationContext as String] = context
        guard SecItemAdd(query as CFDictionary, nil) == errSecSuccess else { throw EnrollmentError.message("Could not save the Vault session. Reconnect the provider.") }
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
            let transaction = try Transaction(); self.transaction = transaction
            let store = try ProviderStore()
            // Capture the shared generation before leaving for web auth. A host
            // actor transition during the await invalidates this transaction.
            enrollmentGeneration = try store.locked { $0.generation }
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
            do { let (data, http) = try result.get(); guard http.statusCode == 200 else { throw EnrollmentError.message("Account connection is unavailable. Try again.") }; _ = try StrictEnvelope.object(data, required: ["access_token", "token_type", "expires_in", "refresh_token"], optional: ["id_token", "scope"]); let token = try JSONDecoder().decode(Token.self, from: data); guard token.token_type.lowercased() == "bearer", (1...86400).contains(token.expires_in), token.access_token.validToken, token.refresh_token.validToken else { throw EnrollmentError.message("Account response was rejected. Try again.") }; self?.authenticateAndPersist(token, generation: generation, key: key) } catch { DispatchQueue.main.async { self?.showError(error) } }
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
            do { let (data, http) = try result.get(); guard http.statusCode == 200 else { throw EnrollmentError.message("Could not verify this account. Try again.") }; _ = try StrictEnvelope.object(data, required: ["sub"], optional: ["email", "email_verified"]); let identity = try JSONDecoder().decode(Identity.self, from: data); guard UUID(uuidString: identity.sub)?.canonical == identity.sub else { throw EnrollmentError.message("Account identity was rejected. Try again.") }; let store = try ProviderStore(); try store.locked { old in guard old.generation == generation else { throw EnrollmentError.message("A host account change cancelled Vault connection. Start again.") }; let next = PublicState(version: 1, generation: UUID().canonical, host_subject: old.host_subject, provider_subject: nil); try store.write(next); let expiry = Int64(Date().timeIntervalSince1970 * 1000) + Int64(token.expires_in) * 1000; try store.save(PrivateSession(version: 1, phase: "active", subject: identity.sub, generation: next.generation, access_token: token.access_token, refresh_token: token.refresh_token, expires_at_ms: expiry), context: context); try store.write(PublicState(version: 1, generation: next.generation, host_subject: next.host_subject, provider_subject: identity.sub)) }; DispatchQueue.main.async { self?.window?.close() } } catch { DispatchQueue.main.async { self?.showError(error) } }
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
