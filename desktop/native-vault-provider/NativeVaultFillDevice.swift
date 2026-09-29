import CryptoKit
import Foundation
import Security

// The password provider's own fill credential (access ladder T-30).
//
// aidream answers `/vault/native/passwords/{id}/materialize` only for a request
// signed by a device key registered to this person over the provider's own live
// OAuth session, with a never-used nonce. The key is a Secure Enclave P-256 key:
// it signs, and its private half never leaves this Mac's enclave. Keychain keeps
// only the enclave's wrapped handle plus the server's device id.
//
// Registering needs the person's AI Matrx password once (the same step-up the
// browser extension uses); it is sent on that one request and never kept.
//
// Wire contract — identical to aidream `fill_devices.canonical_fill_message` v2
// and matrx-extend `src/lib/vault/fill-device.ts` (one contract, never a second):
//   "matrx-vault-fill/v2\n{surface}\n{itemId}\n{timestampMs}\n{nonce}\n{deviceId}\n{userId}\n{sha256hex(body)}"
// ECDSA P-256 / SHA-256, raw r||s base64url in X-Matrx-Fill-Signature.

enum NativeVaultFillWire {
    static let prefix = "matrx-vault-fill/v2"
    static let surface = "native_password_materialize"
    static let registerPath = "api/vault/fill-devices"
    static let stepUpMethodsPath = "api/vault/fill-devices/step-up-methods"
    /// The web app is the passkey's relying party: approval happens there
    /// (the same page the browser extension opens), and that page also lets an
    /// account with no passkey add one.
    static let webOrigin = URL(string: "https://aimatrx.com")!
    static let approvePagePath = "vault/approve-browser"
    static let headerDevice = "X-Matrx-Fill-Device"
    static let headerTimestamp = "X-Matrx-Fill-Timestamp"
    static let headerNonce = "X-Matrx-Fill-Nonce"
    static let headerSignature = "X-Matrx-Fill-Signature"

    static func message(surface: String, itemID: String, timestampMs: String, nonce: String, deviceID: String, userID: String, body: Data) -> Data {
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        return Data([prefix, surface, itemID, timestampMs, nonce, deviceID, userID, digest].joined(separator: "\n").utf8)
    }
    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    static func nonce() -> String {
        var bytes = [UInt8](repeating: 0, count: 24)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { return UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
        return base64URL(Data(bytes))
    }
    /// RFC 7638 thumbprint (SHA-256 hex) — identical to aidream `jwk_thumbprint`
    /// and the extension's `publicKeyThumbprint`.
    static func thumbprint(_ jwk: [String: String]) -> String? {
        guard let crv = jwk["crv"], let kty = jwk["kty"], let x = jwk["x"], let y = jwk["y"] else { return nil }
        let canonical = "{\"crv\":\"\(crv)\",\"kty\":\"\(kty)\",\"x\":\"\(x)\",\"y\":\"\(y)\"}"
        return SHA256.hash(data: Data(canonical.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    /// The short code the approval page shows: first 16 hex in groups of four.
    static func shortCode(_ thumbprint: String) -> String {
        stride(from: 0, to: 16, by: 4).map { i in String(thumbprint.dropFirst(i).prefix(4)) }.joined(separator: " ").uppercased()
    }
    static func approvalURL(thumbprint: String, label: String, origin: URL = webOrigin) -> URL {
        var components = URLComponents(url: origin.appendingPathComponent(approvePagePath), resolvingAgainstBaseURL: false)!
        // `kind=mac` tells the approve page this is the native Mac password app,
        // not a browser extension, so its copy says "this Mac" correctly.
        components.queryItems = [URLQueryItem(name: "key", value: thumbprint), URLQueryItem(name: "label", value: label), URLQueryItem(name: "kind", value: "mac")]
        return components.url!
    }
    /// The P-256 public JWK the server's `normalize_public_jwk` accepts.
    static func publicJWK(x963 raw: Data) -> [String: String]? {
        guard raw.count == 65, raw.first == 0x04 else { return nil }
        return ["kty": "EC", "crv": "P-256", "x": base64URL(raw.subdata(in: 1..<33)), "y": base64URL(raw.subdata(in: 33..<65))]
    }
}

/// A signing key whose private half this code cannot read.
protocol NativeVaultFillSigningKey {
    var publicJWK: [String: String] { get }
    /// Raw 64-byte r||s ECDSA P-256 / SHA-256 signature.
    func sign(_ message: Data) throws -> Data
}

/// Creates and restores keys. Production is the Secure Enclave; the corpus
/// injects a software key and says so.
struct NativeVaultFillKeyFactory {
    var create: () throws -> (handle: Data, key: NativeVaultFillSigningKey)
    var restore: (Data) throws -> NativeVaultFillSigningKey

    static let secureEnclave = NativeVaultFillKeyFactory(
        create: {
            guard SecureEnclave.isAvailable else { throw NativeVaultFillDeviceError.noSecureEnclave }
            guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, .privateKeyUsage, nil) else { throw NativeVaultFillDeviceError.storeUnavailable }
            let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access)
            return (key.dataRepresentation, EnclaveFillKey(key: key))
        },
        restore: { handle in
            guard SecureEnclave.isAvailable else { throw NativeVaultFillDeviceError.noSecureEnclave }
            return EnclaveFillKey(key: try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: handle))
        }
    )
}

private struct EnclaveFillKey: NativeVaultFillSigningKey {
    let key: SecureEnclave.P256.Signing.PrivateKey
    var publicJWK: [String: String] { NativeVaultFillWire.publicJWK(x963: key.publicKey.x963Representation) ?? [:] }
    func sign(_ message: Data) throws -> Data { try key.signature(for: message).rawRepresentation }
}

enum NativeVaultFillDeviceError: LocalizedError, Equatable {
    case noSecureEnclave, storeUnavailable, cancelled, sessionEnded, reconnect, turnedOff, noStepUpMethod, refused(String), unavailable(String)
    var errorDescription: String? {
        switch self {
        case .noSecureEnclave: return "This Mac has no Secure Enclave, so the AI Matrx password provider cannot fill saved passwords here. Use the AI Matrx browser extension instead."
        case .storeUnavailable: return "This Mac's password-filling key could not be read. Try again."
        case .cancelled: return "Password filling was not turned on for this Mac."
        case .sessionEnded: return "Filling from this Mac was turned off in your vault settings. Reconnect the AI Matrx Vault provider, then confirm your password to turn it back on."
        case .reconnect: return "Vault connection needs reconnect."
        case .turnedOff: return NativeVaultFillDeviceError.turnedOffMessage
        case .noStepUpMethod: return "Your AI Matrx account has no password or passkey yet, so this Mac cannot fill saved passwords. Add a passkey to your account on aimatrx.com, then fill again."
        case let .refused(message), let .unavailable(message): return message
        }
    }
}

extension NativeVaultFillDeviceError {
    /// The person turned filling off for this Mac in the web Vault (the server
    /// answers 401 `fill_device_revoked`, never a plain expiry).
    static let turnedOffMessage = "Password filling was turned off for this Mac. Approve it again to keep filling."
}

/// How the signed-in person can confirm it is them (aidream `step_up_methods`).
struct NativeFillStepUpMethods: Equatable {
    var password: Bool
    var passkey: Bool
    /// Unknown (the check failed): offer both; the server still decides.
    static let unknown = NativeFillStepUpMethods(password: true, passkey: true)
    static func parse(_ data: Data, status: Int) -> NativeFillStepUpMethods {
        guard status == 200, let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let password = o["password"] as? Bool, let passkey = o["passkey"] as? Bool else { return .unknown }
        return NativeFillStepUpMethods(password: password, passkey: passkey)
    }
}

/// What the Mac asks the person to turn filling on (or back on).
struct NativeFillStepUpRequest {
    let notice: String?
    let methods: NativeFillStepUpMethods
    /// The web page where this Mac's key is approved with the account passkey.
    let approvalURL: URL
    /// The code that page shows, so the person can check it is this Mac.
    let code: String
}

enum NativeFillStepUp: Equatable {
    case password(String)
    /// The person says they approved this Mac's key on the web.
    case passkeyApproved
    case declined
}

/// One person's registration on this Mac.
struct NativeVaultFillDeviceRecord: Codable, Equatable {
    let version: Int
    let subject: String
    let key_handle: Data
    var device_id: String?
}

protocol NativeVaultFillDeviceStoring: AnyObject {
    func load(subject: String) throws -> NativeVaultFillDeviceRecord?
    func save(_ record: NativeVaultFillDeviceRecord) throws
    func delete(subject: String) throws
}

private let fillDeviceService = "com.aimatrx.desktop.vault-provider.fill-device"
private let fillDeviceGroup = "JH83UH9P4D.com.aimatrx.desktop.vault-provider"

/// Data Protection Keychain, this device only, never synchronized. The stored
/// handle is useless off this Mac's Secure Enclave.
final class NativeVaultFillDeviceKeychain: NativeVaultFillDeviceStoring {
    private let security: PrivateSessionSecurity
    init(security: PrivateSessionSecurity = .system) { self.security = security }
    private func base(_ subject: String) -> KeychainQuery {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: fillDeviceService, kSecAttrAccount as String: subject,
         kSecAttrAccessGroup as String: fillDeviceGroup, kSecUseDataProtectionKeychain as String: true, kSecAttrSynchronizable as String: false]
    }
    func load(subject: String) throws -> NativeVaultFillDeviceRecord? {
        var query = base(subject); query[kSecReturnData as String] = true
        var result: CFTypeRef?
        let status = security.copyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw NativeVaultFillDeviceError.storeUnavailable }
        guard let record = try? JSONDecoder().decode(NativeVaultFillDeviceRecord.self, from: data), record.version == 1, record.subject == subject else {
            try delete(subject: subject); return nil
        }
        return record
    }
    func save(_ record: NativeVaultFillDeviceRecord) throws {
        try delete(subject: record.subject)
        var query = base(record.subject)
        query[kSecValueData as String] = try JSONEncoder().encode(record)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard security.add(query as CFDictionary, nil) == errSecSuccess else { throw NativeVaultFillDeviceError.storeUnavailable }
    }
    func delete(subject: String) throws {
        let status = security.delete(base(subject) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw NativeVaultFillDeviceError.storeUnavailable }
    }
}

/// Corpus/harness store. Never used by the shipped extension.
final class NativeVaultFillDeviceMemoryStore: NativeVaultFillDeviceStoring {
    private let lock = NSLock()
    private var records: [String: NativeVaultFillDeviceRecord] = [:]
    func load(subject: String) throws -> NativeVaultFillDeviceRecord? { lock.lock(); defer { lock.unlock() }; return records[subject] }
    func save(_ record: NativeVaultFillDeviceRecord) throws { lock.lock(); records[record.subject] = record; lock.unlock() }
    func delete(subject: String) throws { lock.lock(); records[subject] = nil; lock.unlock() }
}

/// What the provider needs before a signed fill: a key, and whether the server
/// has accepted it for the current sign-in.
struct NativeVaultFillDeviceState: @unchecked Sendable {
    let record: NativeVaultFillDeviceRecord
    let key: NativeVaultFillSigningKey
    var registered: Bool { record.device_id?.canonicalUUID == true }
}

/// Immutable after init; both stores are thread-safe (Keychain / lock).
final class NativeVaultFillDevice: @unchecked Sendable {
    let store: NativeVaultFillDeviceStoring
    let keys: NativeVaultFillKeyFactory
    let now: () -> Date
    init(store: NativeVaultFillDeviceStoring = NativeVaultFillDeviceKeychain(), keys: NativeVaultFillKeyFactory = .secureEnclave, now: @escaping () -> Date = Date.init) {
        self.store = store; self.keys = keys; self.now = now
    }

    /// The existing key for this person, or a new one (never registered yet).
    func current(subject: String) throws -> NativeVaultFillDeviceState {
        if let record = try store.load(subject: subject) {
            if let key = try? keys.restore(record.key_handle) { return NativeVaultFillDeviceState(record: record, key: key) }
            // The enclave no longer knows this handle (new Mac, wiped enclave):
            // start over with a new key; the server refuses the old one anyway.
            try store.delete(subject: subject)
        }
        return try replaceKey(subject: subject)
    }
    /// A brand-new key (the previous one was turned off on the server).
    func replaceKey(subject: String) throws -> NativeVaultFillDeviceState {
        let created = try keys.create()
        let record = NativeVaultFillDeviceRecord(version: 1, subject: subject, key_handle: created.handle, device_id: nil)
        try store.save(record)
        return NativeVaultFillDeviceState(record: record, key: created.key)
    }
    func markRegistered(_ state: NativeVaultFillDeviceState, deviceID: String) throws -> NativeVaultFillDeviceState {
        var record = state.record; record.device_id = deviceID
        try store.save(record)
        return NativeVaultFillDeviceState(record: record, key: state.key)
    }
    /// The server no longer accepts this registration (new sign-in or turned off).
    func markUnregistered(subject: String) {
        guard var record = try? store.load(subject: subject), record.device_id != nil else { return }
        record.device_id = nil
        try? store.save(record)
    }

    /// Signed headers for ONE materialize request over these exact body bytes.
    func signedHeaders(_ state: NativeVaultFillDeviceState, itemID: String, userID: String, body: Data) throws -> [String: String] {
        guard let deviceID = state.record.device_id, state.registered else { throw NativeVaultFillDeviceError.storeUnavailable }
        let timestamp = String(Int64((now().timeIntervalSince1970 * 1000).rounded(.down)))
        let nonce = NativeVaultFillWire.nonce()
        let message = NativeVaultFillWire.message(surface: NativeVaultFillWire.surface, itemID: itemID.lowercased(), timestampMs: timestamp, nonce: nonce, deviceID: deviceID, userID: userID, body: body)
        let signature = try state.key.sign(message)
        guard signature.count == 64 else { throw NativeVaultFillDeviceError.storeUnavailable }
        return [NativeVaultFillWire.headerDevice: deviceID, NativeVaultFillWire.headerTimestamp: timestamp,
                NativeVaultFillWire.headerNonce: nonce, NativeVaultFillWire.headerSignature: NativeVaultFillWire.base64URL(signature)]
    }

    /// `password` nil = claim the passkey approval given on the web for this key.
    static func registrationBody(_ state: NativeVaultFillDeviceState, password: String?, label: String) throws -> Data {
        let jwk = state.key.publicJWK
        guard jwk.count == 4 else { throw NativeVaultFillDeviceError.storeUnavailable }
        var body: [String: Any] = ["public_key_jwk": jwk, "label": label]
        if let password { body["password"] = password }
        return try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
    }

    enum RegistrationOutcome: Equatable { case registered(String), keyRevoked, wrongPassword(String), stepUpRequired(String), noStepUpMethod, failed(NativeVaultFillDeviceError) }

    /// Classify the server's answer to `POST /api/vault/fill-devices`.
    static func registrationOutcome(_ data: Data, status: Int) -> RegistrationOutcome {
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        if status == 200 {
            guard let id = object?["id"] as? String, id.canonicalUUID else { return .failed(.unavailable("Vault response was rejected. Try again.")) }
            return .registered(id)
        }
        if status == 401 { return .failed(.reconnect) }
        // aidream's error handler may lift the HTTPException detail to the top
        // level; read both shapes, exactly as the extension's `refusalOf` does.
        let detail = (object?["detail"] as? [String: Any]) ?? object
        let code = detail?["error"] as? String
        let message = (detail?["user_message"] as? String) ?? (detail?["message"] as? String)
        switch (status, code) {
        case (403, "key_revoked"): return .keyRevoked
        case (403, "step_up_failed"): return .wrongPassword(message ?? "That password did not match your AI Matrx account.")
        // No password sent and no passkey approval to claim (not approved yet,
        // or the 5-minute approval expired).
        case (403, "step_up_required"): return .stepUpRequired("No passkey approval for this Mac was found yet. Approve it on aimatrx.com with your passkey (check the code matches), then continue.")
        case (403, "no_step_up_method"): return .noStepUpMethod
        case (403, "session_ended"): return .failed(.sessionEnded)
        case (403, _): return .failed(.refused(message ?? "This Mac could not be set up to fill saved passwords."))
        default: return .failed(.unavailable(message ?? "Vault is temporarily unavailable. Try again."))
        }
    }

    static var label: String { "AI Matrx password provider on Mac" }
}
