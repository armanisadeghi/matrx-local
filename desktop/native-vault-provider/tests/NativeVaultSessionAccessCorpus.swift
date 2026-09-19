import Foundation
import AuthenticationServices
import LocalAuthentication
import Security

private final class SessionKeychainProbe {
    private let lock = NSLock()
    private var value: Data?
    func set(_ data: Data?) { lock.lock(); defer { lock.unlock() }; value = data }
    func read() -> Data? { lock.lock(); defer { lock.unlock() }; return value }
    var security: PrivateSessionSecurity {
        PrivateSessionSecurity(copyMatching: { _, output in
            guard let value = self.read() else { return errSecItemNotFound }
            output?.pointee = value as CFData; return errSecSuccess
        }, add: { query, _ in
            self.set((query as NSDictionary)[kSecValueData as String] as? Data); return errSecSuccess
        }, delete: { _ in self.set(nil); return errSecSuccess })
    }
}
private final class SessionTransportProbe: NativeVaultPasswordTransporting {
    var handler: ((URLRequest, @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) -> Void)!
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) { handler(request, completion) }
}
@main private struct SessionAccessCorpus {
    static let subject = "11111111-1111-4111-8111-111111111111"
    static let generation = "22222222-2222-4222-8222-222222222222"
    static let replacement = "33333333-3333-4333-8333-333333333333"
    static func require(_ okay: @autoclosure () throws -> Bool, _ message: String) throws {
        if try !okay() { fatalError(message) }
    }
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let disk = try ProviderStore(testRoot: root, mode: .explicitConnect)
        _ = try disk.initializeExplicitConnect(invalidatePrivate: {})
        let keychain = SessionKeychainProbe()
        let privateSession = NativeVaultPrivateSession(security: keychain.security)
        let network = SessionTransportProbe()
        func makeAccess(_ index: ProviderIdentityIndex = ProviderIdentityIndex { done in done(true, nil) }) -> NativeVaultSessionAccess {
            NativeVaultSessionAccess(transport: network, store: { try ProviderStore(testRoot: root, mode: .providerAccess) }, privateSession: { privateSession }, identityIndex: index)
        }
        let access = makeAccess()
        func seed(expired: Bool = true, phase: String = "active", binding: String = generation) throws {
            try disk.write(PublicState(version: 2, generation: binding, host_subject: nil, provider_subject: subject))
            keychain.set(try VaultEnvelopeCodec.encodePrivateSession(PrivateSession(version: 1, phase: phase, subject: subject, generation: binding, access_token: phase == "active" ? "synthetic-access" : "", refresh_token: phase == "active" ? "synthetic-refresh" : "", expires_at_ms: Int64(Date().timeIntervalSince1970 * 1000) + (expired ? -1000 : 60000))))
        }
        func acquire(_ access: NativeVaultSessionAccess = access, lifetime: NativeVaultRequestLifetime = NativeVaultRequestLifetime()) throws -> Result<NativeVaultSessionAccess.Grant, Error> {
            let semaphore = DispatchSemaphore(value: 0)
            var result: Result<NativeVaultSessionAccess.Grant, Error>?
            access.acquire(key: "synthetic-public-key", context: LAContext(), lifetime: lifetime) { result = $0; semaphore.signal() }
            guard semaphore.wait(timeout: .now() + 15) == .success, let result else { fatalError("Real session acquisition did not finish") }
            return result
        }
        func reply(_ request: URLRequest, status: Int, body: String) -> Result<(Data, HTTPURLResponse), Error> {
            .success((Data(body.utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!))
        }
        func terminal(_ result: Result<NativeVaultSessionAccess.Grant, Error>) -> (generation: String, subject: String?)? {
            guard case let .failure(error) = result else { return nil }
            return (error as? NativeVaultSessionFailure)?.terminalBinding
        }
        try seed(expired: false)
        network.handler = { request, done in done(reply(request, status: 200, body: "{\"sub\":\"\(subject)\"}")) }
        let active = try acquire().get()
        try require(active.subject == subject && active.generation == generation, "Active session must validate and recheck the real test store")

        try seed()
        network.handler = { request, done in done(reply(request, status: 400, body: "{\"error\":\"invalid_grant\"}")) }
        let revoked = try acquire()
        try require(terminal(revoked)?.generation == generation && terminal(revoked)?.subject == subject, "Confirmed revocation must retain original account binding")
        try require(try VaultEnvelopeCodec.privateSession(keychain.read()!).phase == "refresh_pending", "Revoked request must have consumed the refresh token before sending")

        for malformed in ["{\"error\":\"other\",\"error\":\"invalid_grant\"}", "{\"error\":\"invalid_grant\",\"unrecognized_authority\":true}"] {
            try seed()
            network.handler = { request, done in done(reply(request, status: 400, body: malformed)) }
            try require(terminal(try acquire()) == nil, "Ambiguous error envelopes must never authorize terminal cleanup")
        }

        for status in [401, 503] {
            try seed()
            network.handler = { request, done in done(reply(request, status: status, body: "{\"error\":\"invalid_grant\"}")) }
            let ambiguous = try acquire()
            if case .success = ambiguous { fatalError("Unsuccessful HTTP response cannot yield a session") }
            try require(terminal(ambiguous) == nil, "An unconfirmed HTTP failure must not authorize terminal cleanup")
        }
        try seed()
        network.handler = { _, done in done(.failure(URLError(.timedOut))) }
        let timeout = try acquire()
        try require(terminal(timeout) == nil, "Network timeout must remain ambiguous")
        let pending = keychain.read()
        network.handler = { _, _ in fatalError("Consumed refresh must never be replayed") }
        let retry = try acquire()
        if case .success = retry { fatalError("Pending refresh cannot yield a session") }
        try require(terminal(retry) == nil && keychain.read() == pending, "Pending retry must preserve metadata and consumed-token envelope")

        try seed()
        keychain.set(Data("invalid envelope".utf8))
        let corrupt = try acquire()
        try require(terminal(corrupt)?.generation == generation, "Malformed private envelope must be terminal only for its captured public binding")

        try seed()
        var sends = 0
        var replacementBytes: Data?
        network.handler = { request, done in
            sends += 1
            if sends == 1 {
                done(reply(request, status: 200, body: "{\"access_token\":\"refreshed-access\",\"refresh_token\":\"refreshed-refresh\",\"expires_in\":3600,\"token_type\":\"bearer\"}"))
            } else {
                try! disk.locked { _ in try seed(expired: false, binding: replacement) }
                replacementBytes = keychain.read()
                done(reply(request, status: 200, body: "{\"sub\":\"\(subject)\"}"))
            }
        }
        let replaced = try acquire()
        if case .success = replaced { fatalError("Old refresh cannot publish a grant after replacement") }
        try require(keychain.read() == replacementBytes && disk.read().generation == replacement, "Old refresh cannot overwrite a newer enrollment")

        try seed()
        let heldLock = NSLock()
        var heldApple: ((Bool, Error?) -> Void)?
        func appleCallback() -> ((Bool, Error?) -> Void)? { heldLock.lock(); defer { heldLock.unlock() }; return heldApple }
        let heldAccess = makeAccess(ProviderIdentityIndex { completion in heldLock.lock(); heldApple = completion; heldLock.unlock() })
        network.handler = { request, done in done(reply(request, status: 400, body: "{\"error\":\"invalid_grant\"}")) }
        let heldLifetime = NativeVaultRequestLifetime(); let heldSemaphore = DispatchSemaphore(value: 0); var heldResult: Result<NativeVaultSessionAccess.Grant, Error>?
        heldAccess.acquire(key: "synthetic-public-key", context: LAContext(), lifetime: heldLifetime) { heldResult = $0; heldSemaphore.signal() }
        for _ in 0..<100 where appleCallback() == nil { Thread.sleep(forTimeInterval: 0.01) }
        try require(appleCallback() != nil && heldSemaphore.wait(timeout: .now()) == .timedOut, "terminal completion must wait for Apple index clear")
        try require(disk.read().provider_subject == nil, "terminal state must invalidate before Apple callback completes")
        appleCallback()!(true, nil)
        try require(heldSemaphore.wait(timeout: .now() + 2) == .success && terminal(heldResult!) != nil, "terminal failure must surface after Apple clear")

        try seed()
        network.handler = { request, done in
            try! disk.locked { _ in try seed(expired: false, binding: replacement) }
            done(reply(request, status: 400, body: "{\"error\":\"invalid_grant\"}"))
        }
        let delayedRevocation = try acquire()
        try require(terminal(delayedRevocation)?.generation == generation && disk.read().generation == replacement, "Delayed revocation must carry old binding, never the replacement binding")
        try seed()
        let failingIndex = makeAccess(ProviderIdentityIndex { done in done(false, EnrollmentError.message("Apple index clear failed")) })
        network.handler = { request, done in done(reply(request, status: 400, body: "{\"error\":\"invalid_grant\"}")) }
        let clearFailure = try acquire(failingIndex)
        if case let .failure(error) = clearFailure { try require((error as? LocalizedError)?.errorDescription == "Disconnect could not finish. Retry Disconnect.", "Apple failure must replace the original session error") } else { fatalError("Apple clear error must surface") }
        try require(disk.read().provider_subject == nil, "Apple clear error follows durable terminal invalidation")

        try disk.write(PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: nil))
        keychain.set(nil)
        let missing = try acquire(makeAccess())
        try require(terminal(missing)?.generation == generation && terminal(missing)?.subject == nil, "missing Keychain session must retain nil-subject disconnected binding")

        let organization = "88888888-8888-4888-8888-888888888888", revision = "99999999-9999-4999-8999-999999999999"
        var cancellationIndexCalls = 0
        let cancellationAccess = makeAccess(ProviderIdentityIndex { done in cancellationIndexCalls += 1; done(true, nil) })
        try disk.write(PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: subject, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: "ready", refreshed_at_ms: 123, count: 7, unsupported_count: 2)))
        let cancelledLifetime = NativeVaultRequestLifetime(); cancelledLifetime.cancel()
        let cancelled = try acquire(cancellationAccess, lifetime: cancelledLifetime)
        if case .success = cancelled { fatalError("cancelled acquire cannot grant access") }
        let afterCancellation = try disk.read()
        try require(afterCancellation.suggestions.status == "ready" && afterCancellation.suggestions.revision == revision && cancellationIndexCalls == 0, "cancelled original lifetime must preserve disk and Apple index")

        try seed(phase: "refresh_pending", binding: generation)
        try disk.write(PublicState(version: 2, generation: replacement, host_subject: nil, provider_subject: subject))
        let pendingMismatch = try acquire(makeAccess())
        try require(terminal(pendingMismatch)?.generation == replacement, "refresh-pending bytes mismatched to current binding must be terminal")

        var staleIndexCalls = 0
        let staleAccess = makeAccess(ProviderIdentityIndex { done in staleIndexCalls += 1; done(true, nil) })
        try disk.write(PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: subject, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: "ready", refreshed_at_ms: 456, count: 9, unsupported_count: 3)))
        keychain.set(try VaultEnvelopeCodec.encodePrivateSession(PrivateSession(version: 1, phase: "active", subject: subject, generation: generation, access_token: "a", refresh_token: "r", expires_at_ms: Int64(Date().timeIntervalSince1970 * 1000) - 1)))
        network.handler = { _, done in done(.failure(URLError(.timedOut))) }
        let stale = try acquire(staleAccess)
        if case .success = stale { fatalError("timeout cannot grant access") }
        let staleState = try disk.read()
        try require(staleState.suggestions.status == "stale" && staleState.suggestions.revision == revision && staleState.suggestions.count == 9 && staleState.suggestions.unsupported_count == 3 && staleIndexCalls == 0, "ambiguous failure must mark ready state stale without changing metadata or Apple index")

        // A pending retry must also durably stale a newly ready revision,
        // without replaying the consumed refresh token.
        try disk.write(PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: subject, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: "ready", refreshed_at_ms: 456, count: 9, unsupported_count: 3)))
        let pendingBytes = keychain.read()
        network.handler = { _, _ in fatalError("Pending session must not send a second refresh") }
        _ = try acquire(staleAccess)
        try require(disk.read().suggestions.status == "stale" && keychain.read() == pendingBytes && staleIndexCalls == 0, "Pending retry must stale metadata while preserving consumed envelope")

        try seed()
        let cancelledDuringRequest = NativeVaultRequestLifetime()
        let beforeCancel = try disk.read()
        network.handler = { request, done in
            cancelledDuringRequest.cancel()
            done(reply(request, status: 400, body: "{\"error\":\"invalid_grant\"}"))
        }
        let cancelledInFlight = try acquire(cancellationAccess, lifetime: cancelledDuringRequest)
        if case let .failure(error) = cancelledInFlight { try require((error as? URLError)?.code == .cancelled, "Canceled network reply must settle as cancellation") } else { fatalError("Cancelled refresh granted access") }
        try require(disk.read().generation == beforeCancel.generation && disk.read().provider_subject == beforeCancel.provider_subject && cancellationIndexCalls == 0, "Cancelled terminal response cannot clear the current account")

        try seed()
        var successSends = 0
        network.handler = { request, done in
            successSends += 1
            done(reply(request, status: 200, body: successSends == 1 ? "{\"access_token\":\"refreshed-access\",\"refresh_token\":\"refreshed-refresh\",\"expires_in\":3600,\"token_type\":\"bearer\"}" : "{\"sub\":\"\(subject)\"}"))
        }
        let refreshed = try acquire().get()
        try require(successSends == 2 && refreshed.accessToken == "refreshed-access" && VaultEnvelopeCodec.privateSession(keychain.read()!).phase == "active", "Successful refresh must validate and persist its new session")
        try seed()
        var corruptionClears = 0
        let undeletableSession = NativeVaultPrivateSession(security: PrivateSessionSecurity(copyMatching: { _, output in output?.pointee = Data("malformed".utf8) as CFData; return errSecSuccess }, add: { _, _ in fatalError("Corrupt session cannot be saved") }, delete: { _ in errSecInteractionNotAllowed }))
        let undeletableAccess = NativeVaultSessionAccess(transport: network, store: { try ProviderStore(testRoot: root, mode: .providerAccess) }, privateSession: { undeletableSession }, identityIndex: ProviderIdentityIndex { done in corruptionClears += 1; done(true, nil) })
        let corruptFailure = try acquire(undeletableAccess)
        try require(terminal(corruptFailure)?.generation == generation && disk.read().provider_subject == nil && corruptionClears == 1, "Corrupt Keychain deletion failure must remain terminal and clear public/Apple authority")
        if case let .failure(error) = corruptFailure {
            try require((error as? LocalizedError)?.errorDescription?.contains("private copy could not be cleared") == true, "Private deletion failure must retain an explicit cleanup remedy")
        }

        // A post-grant failure can stale only the revision that issued the grant.
        try seed(expired: false)
        let oldState = try disk.read()
        let oldGrant = NativeVaultSessionAccess.Grant(accessToken: "synthetic", subject: subject, generation: generation, suggestionState: oldState)
        try disk.write(PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: subject, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: "ready", refreshed_at_ms: 456, count: 9, unsupported_count: 3)))
        let responseSettled = DispatchSemaphore(value: 0)
        access.reconcileResponse(.failure(URLError(.timedOut)), grant: oldGrant, lifetime: NativeVaultRequestLifetime()) { _ in responseSettled.signal() }
        try require(responseSettled.wait(timeout: .now() + 2) == .success && disk.read().suggestions.status == "ready", "Old grant failure must not stale a newly published revision")
        print("Real session access passed: active validation, terminal ordering, stale preservation, pending binding and account replacement")
    }
}
