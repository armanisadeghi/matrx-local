import Foundation
import AuthenticationServices
import LocalAuthentication
import Security

private let privateSessionService = "com.aimatrx.desktop.vault-provider.session"
private let privateSessionAccount = "current-session"
private let privateSessionGroup = "JH83UH9P4D.com.aimatrx.desktop.vault-provider"

typealias KeychainQuery = [String: Any]

enum NativeVaultSessionFailure: LocalizedError {
    case localSessionCorrupt
    case localSessionCleanupFailed
    case corruptCleanupBound(generation: String, subject: String?)
    case refreshPending
    case corruptBound(generation: String, subject: String?)
    case providerDisconnected(generation: String, subject: String?)
    case refreshRevoked(generation: String, subject: String?)
    var terminalBinding: (generation: String, subject: String?)? {
        switch self { case .localSessionCorrupt, .localSessionCleanupFailed, .refreshPending: return nil; case let .corruptBound(generation, subject), let .corruptCleanupBound(generation, subject), let .refreshRevoked(generation, subject), let .providerDisconnected(generation, subject): return (generation, subject) }
    }
    var errorDescription: String? {
        switch self {
        case .localSessionCorrupt, .corruptBound: return "Vault session is corrupt. Reconnect the provider."
        case .localSessionCleanupFailed, .corruptCleanupBound: return "Vault session is corrupt and its private copy could not be cleared. Reconnect the provider to retry cleanup."
        case .refreshPending: return "Vault connection refresh is still pending. Reconnect the provider."
        case .refreshRevoked, .providerDisconnected: return "Vault connection needs reconnect."
        }
    }
}

/// Injectable boundary around Security.framework. Production calls the real
/// Keychain; the corpus records queries and results without claiming a test
/// Keychain group proves entitlement access.
struct PrivateSessionSecurity {
    var copyMatching: (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus
    var add: (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus
    var delete: (CFDictionary) -> OSStatus

    static let system = PrivateSessionSecurity(
        copyMatching: { SecItemCopyMatching($0, $1) },
        add: { SecItemAdd($0, $1) },
        delete: { SecItemDelete($0) }
    )
}

struct ProviderIdentityIndex {
    var removeAll: (@escaping (Bool, Error?) -> Void) -> Void
    static let system = ProviderIdentityIndex { completion in
        ASCredentialIdentityStore.shared.removeAllCredentialIdentities(completion)
    }

    /// Must run on the background thread holding ProviderStore's file lock.
    /// There is deliberately no timeout: releasing the lock while this callback
    /// is still live could let it erase identities from a newer enrollment.
    func clearWhileLocked() throws {
        let semaphore = DispatchSemaphore(value: 0)
        var success = false; var failure: Error?
        removeAll { didClear, error in success = didClear; failure = error; semaphore.signal() }
        semaphore.wait()
        guard success, failure == nil else {
            throw EnrollmentError.message("Disconnect could not finish. Retry Disconnect.")
        }
    }
}

enum PrivateSessionQueryContract {
    static func validate(_ query: KeychainQuery, requiresData: Bool) throws {
        guard query[kSecClass as String] as? String == (kSecClassGenericPassword as String),
              query[kSecAttrService as String] as? String == privateSessionService,
              query[kSecAttrAccount as String] as? String == privateSessionAccount,
              query[kSecAttrAccessGroup as String] as? String == privateSessionGroup,
              query[kSecUseDataProtectionKeychain as String] as? Bool == true,
              query[kSecAttrSynchronizable as String] as? Bool == false,
              query[kSecUseAuthenticationContext as String] is LAContext,
              (!requiresData || query[kSecReturnData as String] as? Bool == true) else {
            throw EnrollmentError.message("Vault session is unavailable. Reconnect the provider.")
        }
    }
}

final class NativeVaultPrivateSession {
    private let security: PrivateSessionSecurity
    init(security: PrivateSessionSecurity = .system) { self.security = security }

    func authenticatedContext(reason: String) throws -> LAContext {
        let context = LAContext(); var detail: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &detail) else {
            throw EnrollmentError.message("Vault protection is unavailable on this Mac.")
        }
        // Callers evaluate this before taking ProviderStore's file lock.
        return context
    }

    func readActive(context: LAContext, matching state: PublicState) throws -> PrivateSession? {
        context.interactionNotAllowed = true
        var result: CFTypeRef?
        let query = readQuery(context: context)
        try PrivateSessionQueryContract.validate(query, requiresData: true)
        let status = security.copyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw unavailable() }
        guard let data = result as? Data else {
            try discardCorrupt(context: context)
            throw corrupt()
        }
        let session: PrivateSession
        do {
            session = try VaultEnvelopeCodec.privateSession(data)
        } catch {
            // An authorized read is the only point allowed to remove malformed,
            // pending, or stale private bytes. Missing items never initialize.
            try discardCorrupt(context: context)
            throw corrupt()
        }
        // A refresh token was consumed before its request left this provider.
        // Its pending marker is ambiguous: preserve public suggestions and do
        // not delete/replay it merely because a later callback observes it.
        guard session.subject == state.provider_subject, session.generation == state.generation else {
            try discardCorrupt(context: context)
            throw corrupt()
        }
        if session.phase == "refresh_pending" { throw NativeVaultSessionFailure.refreshPending }
        return session
    }

    func save(_ session: PrivateSession, context: LAContext) throws {
        context.interactionNotAllowed = true
        let data = try VaultEnvelopeCodec.encodePrivateSession(session)
        guard data.count <= 48 * 1024 else { throw corrupt() }
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, .userPresence, nil) else {
            throw EnrollmentError.message("Could not protect the Vault session.")
        }
        try delete(context: context)
        var query = baseQuery()
        query[kSecValueData as String] = data
        query[kSecAttrAccessControl as String] = access
        query[kSecUseAuthenticationContext as String] = context
        try PrivateSessionQueryContract.validate(query, requiresData: false)
        guard security.add(query as CFDictionary, nil) == errSecSuccess else {
            throw EnrollmentError.message("Could not save the Vault session. Reconnect the provider.")
        }
    }

    /// Consume the active session before a refresh request leaves the provider.
    /// A crash or an ambiguous network outcome therefore cannot replay its
    /// refresh token. The caller retains only the returned scoped copy for the
    /// one request, while the Keychain immediately contains no token strings.
    func beginRefresh(_ active: PrivateSession, context: LAContext) throws -> PrivateSession {
        guard active.phase == "active", !active.access_token.isEmpty, !active.refresh_token.isEmpty else {
            throw corrupt()
        }
        let pending = PrivateSession(
            version: 1,
            phase: "refresh_pending",
            subject: active.subject,
            generation: active.generation,
            access_token: "",
            refresh_token: "",
            expires_at_ms: active.expires_at_ms
        )
        try save(pending, context: context)
        return pending
    }

    func delete(context: LAContext) throws {
        context.interactionNotAllowed = true
        let query = deleteQuery(context: context)
        try PrivateSessionQueryContract.validate(query, requiresData: false)
        let status = security.delete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw EnrollmentError.message("Could not clear the Vault session. Reconnect the provider.")
        }
    }

    private func discardCorrupt(context: LAContext) throws {
        do { try delete(context: context) }
        catch { throw NativeVaultSessionFailure.localSessionCleanupFailed }
    }

    private func baseQuery() -> KeychainQuery {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: privateSessionService,
            kSecAttrAccount as String: privateSessionAccount,
            kSecAttrAccessGroup as String: privateSessionGroup,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
        ]
    }
    private func readQuery(context: LAContext) -> KeychainQuery {
        var query = baseQuery(); query[kSecReturnData as String] = true; query[kSecUseAuthenticationContext as String] = context
        return query
    }
    private func deleteQuery(context: LAContext) -> KeychainQuery {
        var query = baseQuery(); query[kSecUseAuthenticationContext as String] = context; return query
    }
    private func corrupt() -> Error { NativeVaultSessionFailure.localSessionCorrupt }
    private func unavailable() -> Error { EnrollmentError.message("Vault session is unavailable. Reconnect the provider.") }
}
