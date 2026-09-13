import Foundation
import LocalAuthentication
import Security

private enum Failure: Error { case bad }

@main
struct NativeVaultPrivateSessionCorpus {
    static let subject = "11111111-1111-4111-8111-111111111111"
    static let generation = "22222222-2222-4222-8222-222222222222"
    static let otherGeneration = "33333333-3333-4333-8333-333333333333"

    static func main() throws {
        try readActiveUsesExactQueryAndReturnsDecodedEnvelope()
        try missingDoesNotDeleteOrInitialize()
        try pendingAndStaleRefuseAndDeleteExactItem()
        try keychainErrorRefusesWithoutDelete()
        try nilKeychainDataIsCorruptAndDeleted()
        try queryMutationWithoutAccountRefuses()
        try saveDeletesThenAddsProtectedExactItem()
        try delayedIdentityClearHoldsFilesystemLock()
        print("native private session corpus passed")
    }

    static func state() -> PublicState { PublicState(version: 1, generation: generation, host_subject: nil, provider_subject: subject) }
    static func envelope(phase: String = "active", generation: String = generation) throws -> Data {
        try VaultEnvelopeCodec.encodePrivateSession(PrivateSession(version: 1, phase: phase, subject: subject, generation: generation, access_token: phase == "active" ? "access" : "", refresh_token: phase == "active" ? "refresh" : "", expires_at_ms: 1))
    }
    private static func fake(copy: @escaping (UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus, add: @escaping (CFDictionary) -> OSStatus = { _ in errSecSuccess }, delete: @escaping (CFDictionary) -> OSStatus = { _ in errSecSuccess }) -> (NativeVaultPrivateSession, Recorder) {
        let recorder = Recorder()
        let security = PrivateSessionSecurity(
            copyMatching: { query, output in
                recorder.copyQueries.append(query as NSDictionary)
                if let context = (query as NSDictionary)[kSecUseAuthenticationContext as String] as? LAContext { recorder.copyContexts.append(context) }
                return copy(output)
            },
            add: { query, _ in recorder.addQueries.append(query as NSDictionary); return add(query) },
            delete: { query in
                recorder.deleteQueries.append(query as NSDictionary)
                if let context = (query as NSDictionary)[kSecUseAuthenticationContext as String] as? LAContext { recorder.deleteContexts.append(context) }
                return delete(query)
            }
        )
        return (NativeVaultPrivateSession(security: security), recorder)
    }
    private static func context() -> LAContext { LAContext() }
    private static func required(_ query: NSDictionary, _ key: CFString, _ expected: Any) throws {
        guard let actual = query[key as String] as? NSObject, actual.isEqual(expected) else { throw Failure.bad }
    }

    static func readActiveUsesExactQueryAndReturnsDecodedEnvelope() throws {
        let data = try envelope()
        let (adapter, recorder) = fake(copy: { output in output?.pointee = data as CFData; return errSecSuccess })
        let read = try adapter.readActive(context: context(), matching: state())
        guard read?.access_token == "access", recorder.deleteQueries.isEmpty, recorder.copyQueries.count == 1 else { throw Failure.bad }
        let query = recorder.copyQueries[0]
        try required(query, kSecClass, kSecClassGenericPassword)
        try required(query, kSecAttrService, "com.aimatrx.desktop.vault-provider.session")
        try required(query, kSecAttrAccount, "current-session")
        try required(query, kSecAttrAccessGroup, "JH83UH9P4D.com.aimatrx.desktop.vault-provider")
        try required(query, kSecUseDataProtectionKeychain, true)
        try required(query, kSecAttrSynchronizable, false)
        guard query[kSecUseAuthenticationContext as String] is LAContext, query[kSecReturnData as String] as? Bool == true else { throw Failure.bad }
        guard context().interactionNotAllowed == false else { throw Failure.bad }
        guard recorder.copyContexts.allSatisfy(\.interactionNotAllowed) else { throw Failure.bad }
    }

    static func missingDoesNotDeleteOrInitialize() throws {
        let (adapter, recorder) = fake(copy: { _ in errSecItemNotFound })
        guard try adapter.readActive(context: context(), matching: state()) == nil, recorder.deleteQueries.isEmpty else { throw Failure.bad }
    }

    static func pendingAndStaleRefuseAndDeleteExactItem() throws {
        for data in [try envelope(phase: "refresh_pending"), try envelope(generation: otherGeneration), Data("{bad".utf8)] {
            let (adapter, recorder) = fake(copy: { output in output?.pointee = data as CFData; return errSecSuccess })
            do { _ = try adapter.readActive(context: context(), matching: state()); throw Failure.bad }
            catch let error as LocalizedError {
                guard error.errorDescription == "Vault session is corrupt. Reconnect the provider.", recorder.deleteQueries.count == 1 else { throw Failure.bad }
                let query = recorder.deleteQueries[0]
                try required(query, kSecClass, kSecClassGenericPassword)
                try required(query, kSecAttrService, "com.aimatrx.desktop.vault-provider.session")
                try required(query, kSecAttrAccount, "current-session")
                try required(query, kSecAttrAccessGroup, "JH83UH9P4D.com.aimatrx.desktop.vault-provider")
                try required(query, kSecUseDataProtectionKeychain, true)
                try required(query, kSecAttrSynchronizable, false)
                guard query[kSecUseAuthenticationContext as String] is LAContext, recorder.deleteContexts.allSatisfy(\.interactionNotAllowed) else { throw Failure.bad }
            }
        }
    }

    static func nilKeychainDataIsCorruptAndDeleted() throws {
        let (adapter, recorder) = fake(copy: { _ in errSecSuccess })
        do { _ = try adapter.readActive(context: context(), matching: state()); throw Failure.bad }
        catch let error as LocalizedError { guard error.errorDescription == "Vault session is corrupt. Reconnect the provider.", recorder.deleteQueries.count == 1 else { throw Failure.bad } }
    }

    static func queryMutationWithoutAccountRefuses() throws {
        let data = try envelope()
        let (adapter, recorder) = fake(copy: { output in output?.pointee = data as CFData; return errSecSuccess })
        _ = try adapter.readActive(context: context(), matching: state())
        var mutant = recorder.copyQueries[0] as! KeychainQuery
        mutant.removeValue(forKey: kSecAttrAccount as String)
        do {
            try PrivateSessionQueryContract.validate(mutant, requiresData: true)
        } catch is EnrollmentError {
            return
        }
        throw Failure.bad
    }

    static func keychainErrorRefusesWithoutDelete() throws {
        let (adapter, recorder) = fake(copy: { _ in errSecAuthFailed })
        do { _ = try adapter.readActive(context: context(), matching: state()); throw Failure.bad }
        catch let error as LocalizedError { guard error.errorDescription == "Vault session is unavailable. Reconnect the provider.", recorder.deleteQueries.isEmpty else { throw Failure.bad } }
    }

    static func saveDeletesThenAddsProtectedExactItem() throws {
        let (adapter, recorder) = fake(copy: { _ in errSecItemNotFound }, delete: { _ in errSecItemNotFound })
        try adapter.save(PrivateSession(version: 1, phase: "active", subject: subject, generation: generation, access_token: "access", refresh_token: "refresh", expires_at_ms: 1), context: context())
        guard recorder.deleteQueries.count == 1, recorder.addQueries.count == 1 else { throw Failure.bad }
        let query = recorder.addQueries[0]
        try required(query, kSecAttrService, "com.aimatrx.desktop.vault-provider.session")
        try required(query, kSecAttrAccount, "current-session")
        try required(query, kSecAttrAccessGroup, "JH83UH9P4D.com.aimatrx.desktop.vault-provider")
        try required(query, kSecUseDataProtectionKeychain, true)
        try required(query, kSecAttrSynchronizable, false)
        guard query[kSecAttrAccessControl as String] != nil, query[kSecAttrAccessible as String] == nil, query[kSecUseAuthenticationContext as String] is LAContext else { throw Failure.bad }
        guard let data = query[kSecValueData as String] as? Data, try VaultEnvelopeCodec.privateSession(data).subject == subject else { throw Failure.bad }
    }
    static func delayedIdentityClearHoldsFilesystemLock() throws {
        let root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("native-private-index-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let first = try ProviderStore(testRoot: root, mode: .explicitConnect)
        _ = try first.initializeExplicitConnect(invalidatePrivate: {})
        let started = DispatchSemaphore(value: 0); let finished = DispatchSemaphore(value: 0)
        var completion: ((Bool, Error?) -> Void)?
        let index = ProviderIdentityIndex { callback in completion = callback; started.signal() }
        var workerFailure: Error?
        DispatchQueue.global(qos: .userInitiated).async {
            do { try first.locked { _ in try index.clearWhileLocked() } } catch { workerFailure = error }
            finished.signal()
        }
        guard started.wait(timeout: .now() + 1) == .success else { throw Failure.bad }
        let competing = try ProviderStore(testRoot: root, mode: .explicitConnect)
        do { _ = try competing.locked { _ in () }; throw Failure.bad }
        catch let error as LocalizedError { guard error.errorDescription == "Vault setup is busy. Try again." else { throw Failure.bad } }
        completion?(true, nil)
        guard finished.wait(timeout: .now() + 3) == .success, workerFailure == nil else { throw Failure.bad }
        _ = try competing.locked { _ in () }
    }

}

private final class Recorder {
    var copyQueries: [NSDictionary] = []
    var addQueries: [NSDictionary] = []
    var deleteQueries: [NSDictionary] = []
    var copyContexts: [LAContext] = []
    var deleteContexts: [LAContext] = []
}
