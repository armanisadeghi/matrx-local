import AuthenticationServices
import Foundation

private enum IdentityFailure: Error { case expected }
private func reject(_ block: () throws -> Void) throws { do { try block(); throw IdentityFailure.expected } catch IdentityFailure.expected { throw IdentityFailure.expected } catch { } }

@main struct NativeVaultIdentityCorpus {
    static let snapshot = String(repeating: "a", count: 64)
    static func page(_ identities: String, complete: Bool = true, after: String = "null") -> Data { Data("{\"snapshot_id\":\"\(snapshot)\",\"identities\":\(identities),\"next_after\":\(after),\"complete\":\(complete),\"unsupported_count\":0}".utf8) }
    static func main() throws {
        try synchronizerCases()
        _ = try NativeVaultIdentityCodec.page(Data(repeating: 32, count: 65 * 1024) + page("[]"))
        try reject { _ = try NativeVaultIdentityCodec.page(Data(repeating: 32, count: 96 * 1024) + page("[]")) }
        let state = PublicState(version: 2, generation: "11111111-1111-4111-8111-111111111111", host_subject: nil, provider_subject: "22222222-2222-4222-8222-222222222222", suggestions: NativeSuggestions(organization_id: "33333333-3333-4333-8333-333333333333", revision: "44444444-4444-4444-8444-444444444444", status: "ready", refreshed_at_ms: 1, count: 1, unsupported_count: 0))
        let password = NativeVaultIndexedIdentity(itemID: "55555555-5555-4555-8555-555555555555", kind: .password, serviceType: "url", serviceIdentifier: "https://example.com/Case", passkeyID: nil, rpID: nil, credentialID: nil, userHandle: nil, user: "account@example.com")
        let record = try NativeVaultIdentityRecord.make(password, state: state, organization: state.suggestions.organization_id!, revision: state.suggestions.revision)
        guard NativeVaultIdentityRecord.parse(record, state: state)?.item == password.itemID else { throw IdentityFailure.expected }
        let changed = PublicState(version: 2, generation: state.generation, host_subject: nil, provider_subject: state.provider_subject, suggestions: NativeSuggestions(organization_id: state.suggestions.organization_id, revision: "66666666-6666-4666-8666-666666666666", status: "ready", refreshed_at_ms: 1, count: 1, unsupported_count: 0))
        guard NativeVaultIdentityRecord.parse(record, state: changed) == nil else { throw IdentityFailure.expected }
        let second = "{\"item_id\":\"55555555-5555-4555-8555-555555555555\",\"kind\":\"password\",\"service\":{\"type\":\"url\",\"identifier\":\"https://example.com/other\"},\"user\":\"account@example.com\"}"
        let first = "{\"item_id\":\"55555555-5555-4555-8555-555555555555\",\"kind\":\"password\",\"service\":{\"type\":\"url\",\"identifier\":\"https://example.com/Case\"},\"user\":\"account@example.com\"}"
        let parsed = try NativeVaultIdentityCodec.page(page("[\(first),\(second)]")); guard parsed.identities.count == 2 else { throw IdentityFailure.expected }
        try reject { _ = try NativeVaultIdentityCodec.page(page("[]", complete: false)) }
        try reject { _ = try NativeVaultIdentityCodec.page(Data("{\"snapshot_id\":\"s\",\"identities\":[],\"next_after\":null,\"complete\":true,\"unsupported_count\":0}".utf8)) }
        try reject { _ = try NativeVaultIdentityCodec.page(page("[{\"item_id\":\"55555555-5555-4555-8555-555555555555\",\"kind\":\"password\",\"service\":{\"type\":\"domain\",\"identifier\":\"example.com\"},\"user\":\"a\"}]")) }
    }
}

private final class AppleStoreProbe: NativeVaultIdentityStoring, @unchecked Sendable {
    var enabled = true
    var error: Error?
    var installed: [[ASCredentialIdentity]] = []
    var hold = false
    var callback: ((Bool, Error?) -> Void)?
    func state(_ completion: @escaping (Bool, Error?) -> Void) { completion(enabled, nil) }
    func replace(_ entries: [ASCredentialIdentity], completion: @escaping (Bool, Error?) -> Void) {
        DispatchQueue.main.async {
            self.installed.append(entries)
            if self.hold { self.callback = completion } else { completion(self.error == nil, self.error) }
        }
    }
}
private extension NativeVaultIdentityCorpus {
    static func wait(_ condition: () -> Bool) throws {
        let end = Date().addingTimeInterval(15)
        while !condition(), Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.01)) }
        guard condition() else { throw IdentityFailure.expected }
    }
    static func synchronizerCases() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let disk = try ProviderStore(testRoot: root, mode: .explicitConnect)
        _ = try disk.initializeExplicitConnect(invalidatePrivate: {})
        let subject = "22222222-2222-4222-8222-222222222222"
        let org = "33333333-3333-4333-8333-333333333333"
        let initial = PublicState(version: 2, generation: "11111111-1111-4111-8111-111111111111", host_subject: nil, provider_subject: subject)
        try disk.write(initial)
        let apple = AppleStoreProbe()
        let sync = NativeVaultIdentitySynchronizer(store: apple, stateStore: { try ProviderStore(testRoot: root, mode: .providerAccess) })
        let row = "{\"item_id\":\"55555555-5555-4555-8555-555555555555\",\"kind\":\"password\",\"service\":{\"type\":\"url\",\"identifier\":\"https://example.com/Case\"},\"user\":\"test\"}"
        func response(_ request: URLRequest, _ status: Int) -> HTTPURLResponse { HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)! }
        var calls = 0
        var outcome: Result<PublicState, Error>?
        sync.refresh(accessToken: "disposable-test-token", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            calls += 1
            if calls == 1 { done(.success((page("[\(row)]", complete: false, after: "\"page-two\""), response(request, 200)))) }
            else if calls == 2 { done(.success((page("[]"), response(request, 200)))) }
            else {
                let body = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                precondition(body["snapshot_id"] as? String == snapshot && body["after"] is NSNull)
                done(.success((page("[\(row)]"), response(request, 200))))
            }
        }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        let ready = try outcome!.get()
        guard calls == 3, apple.installed.count == 1, apple.installed[0].count == 1, ready.suggestions.count == 1, try disk.read().suggestions.revision == ready.suggestions.revision else { throw IdentityFailure.expected }
        print("Passed: complete paging")
        // Block filesystem access for the stale transition: failure cannot
        // become visible until that transition has completed durably.
        let staleEntered = DispatchSemaphore(value: 0)
        let staleRelease = DispatchSemaphore(value: 0)
        var stores = 0
        let staleSync = NativeVaultIdentitySynchronizer(store: apple, stateStore: {
            stores += 1
            if stores == 2 { staleEntered.signal(); staleRelease.wait() }
            return try ProviderStore(testRoot: root, mode: .providerAccess)
        })
        var staleOutcome: Result<PublicState, Error>?
        var statusAtFailure: String?
        staleSync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in done(.success((Data(), response(request, 503)))) }, isCurrent: { true }, completion: {
            staleOutcome = $0; statusAtFailure = try! disk.read().suggestions.status
            staleSync.cancel()
        })
        guard staleEntered.wait(timeout: .now() + 15) == .success else { throw IdentityFailure.expected }
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        let completedBeforeWrite = staleOutcome != nil
        staleRelease.signal()
        try wait { staleOutcome != nil }
        guard !completedBeforeWrite, statusAtFailure == "stale" else { throw IdentityFailure.expected }
        try disk.write(ready)
        // Cancel inside the final authority callback's lifetime predicate.
        // This forces cancellation after its first active-token comparison,
        // before replaceComplete receives control.
        let raceReached = DispatchSemaphore(value: 0)
        var raceStores = 0
        let raceSync = NativeVaultIdentitySynchronizer(store: apple, stateStore: {
            raceStores += 1
            if raceStores == 3 { raceReached.signal() }
            return try ProviderStore(testRoot: root, mode: .providerAccess)
        })
        var raceCalls = 0; var canceledInGuard = false; var raceCompleted = false
        raceSync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            raceCalls += 1; done(.success((page("[]"), response(request, 200))))
        }, isCurrent: {
            if raceCalls == 2, !canceledInGuard { canceledInGuard = true; raceSync.cancel() }
            return true
        }, completion: { _ in raceCompleted = true })
        guard raceReached.wait(timeout: .now() + 15) == .success else { throw IdentityFailure.expected }
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        guard canceledInGuard, !raceCompleted, apple.installed.count == 1, try disk.read().suggestions.status == "ready" else { throw IdentityFailure.expected }
        // A failed continuation never installs a partial list.
        outcome = nil; calls = 0
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            calls += 1
            done(.success((calls == 1 ? page("[\(row)]", complete: false, after: "\"next\"") : page("[]", complete: false, after: "\"next\""), response(request, 200))))
        }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        guard case .failure = outcome!, try disk.read().suggestions.status == "stale", apple.installed.count == 1, try disk.read().suggestions.revision == ready.suggestions.revision else { throw IdentityFailure.expected }
        print("Passed: failed continuation")
        // Only one complete restart is permitted after a changing snapshot.
        outcome = nil; calls = 0
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            calls += 1; done(.success((Data(), response(request, 409))))
        }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        guard calls == 2, case .failure = outcome!, apple.installed.count == 1 else { throw IdentityFailure.expected }
        print("Passed: bounded retry")
        // Cancellation after Apple starts must not publish a ready revision.
        apple.hold = true; outcome = nil
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in done(.success((page("[\(row)]"), response(request, 200)))) }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { apple.callback != nil }
        sync.cancel(); apple.callback!(true, nil); apple.callback = nil
        RunLoop.current.run(until: Date().addingTimeInterval(0.15))
        guard outcome == nil, try disk.read().suggestions.revision == ready.suggestions.revision else { throw IdentityFailure.expected }
        print("Passed: cancellation")
        // An older network response cannot overwrite a newer successful refresh.
        apple.hold = false; outcome = nil
        var oldRequest: URLRequest?
        var oldReply: ((Result<(Data, HTTPURLResponse), Error>) -> Void)?
        var oldCompleted = false
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            oldRequest = request; oldReply = done
        }, isCurrent: { true }, completion: { _ in oldCompleted = true })
        try wait { oldReply != nil }
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in done(.success((page("[]"), response(request, 200)))) }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        let newest = try outcome!.get()
        let installations = apple.installed.count
        oldReply!(.success((page("[\(row)]"), response(oldRequest!, 200))))
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        guard !oldCompleted, apple.installed.count == installations, try disk.read().suggestions.revision == newest.suggestions.revision else { throw IdentityFailure.expected }
        print("Passed: overlap")
        // Apple failure preserves the committed revision and yields a useful error.
        outcome = nil; apple.error = NSError(domain: ASCredentialIdentityStoreErrorDomain, code: 2)
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in done(.success((page("[\(row)]"), response(request, 200)))) }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        guard case let .failure(error) = outcome!, case NativeIdentityStoreFailure.busy = error, try disk.read().suggestions.revision == newest.suggestions.revision else { throw IdentityFailure.expected }
        try disk.write(newest)
        apple.error = nil; apple.enabled = false; outcome = nil
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { _, _ in preconditionFailure("Disabled store must not fetch") }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        guard case let .failure(error) = outcome!, case NativeIdentityStoreFailure.disabled = error, try disk.read().suggestions.status == "stale" else { throw IdentityFailure.expected }
        print("Passed: OS errors")
        // One snapshot conflict restarts collection and can still complete.
        apple.enabled = true; apple.hold = false; outcome = nil; calls = 0
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            calls += 1
            done(.success((calls == 1 ? Data() : page("[]"), response(request, calls == 1 ? 409 : 200))))
        }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        _ = try outcome!.get()
        guard calls == 3 else { throw IdentityFailure.expected }
        // A real account-generation change during fetch prevents Apple writes.
        outcome = nil; calls = 0
        let beforeSwitch = apple.installed.count
        let switched = PublicState(version: 2, generation: "77777777-7777-4777-8777-777777777777", host_subject: nil, provider_subject: subject)
        sync.refresh(accessToken: "test", subject: subject, generation: initial.generation, organization: org, send: { request, done in
            calls += 1
            if calls == 2 { try! disk.locked { _ in try disk.write(switched) } }
            done(.success((page("[]"), response(request, 200))))
        }, isCurrent: { true }, completion: { outcome = $0 })
        try wait { outcome != nil }
        guard case .failure = outcome!, apple.installed.count == beforeSwitch, try disk.read().generation == switched.generation else { throw IdentityFailure.expected }
        // Existing v1 enrollment is upgraded only after Apple confirms clearing.
        apple.enabled = true; apple.hold = true
        let legacy = PublicState(version: 1, generation: initial.generation, host_subject: nil, provider_subject: subject)
        try disk.write(legacy)
        var migrated: Result<Void, Error>?
        sync.migrateV1(subject: subject, generation: initial.generation, completion: { migrated = $0 })
        try wait { apple.callback != nil }
        guard try disk.read().version == 1, apple.installed.last?.isEmpty == true else { throw IdentityFailure.expected }
        apple.callback!(true, nil); apple.callback = nil
        try wait { migrated != nil }
        try migrated!.get()
        let upgraded = try disk.read()
        guard upgraded.version == 2, upgraded.generation == legacy.generation, upgraded.provider_subject == legacy.provider_subject, upgraded.suggestions.count == 0 else { throw IdentityFailure.expected }
        print("Passed: legacy migration")
        print("Native identity synchronizer: paging, authority recheck, partial failure, bounded retry and cancellation passed")
    }
}
