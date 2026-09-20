import Foundation
import AuthenticationServices
import LocalAuthentication
import Security

final class ScriptedPasswordTransport: NativeVaultPasswordTransporting {
    var requests: [URLRequest] = []
    var replies: [Result<(Data, HTTPURLResponse), Error>] = []
    var beforeReply: ((Int) -> Void)?
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) { requests.append(request); beforeReply?(requests.count); completion(replies.removeFirst()) }
}


final class PasswordStateBox {
    private let lock = NSLock()
    private var state: NativePasswordCurrentState
    init(_ state: NativePasswordCurrentState) { self.state = state }
    func read() -> NativePasswordCurrentState { lock.lock(); defer { lock.unlock() }; return state }
    func replace(_ next: NativePasswordCurrentState) { lock.lock(); defer { lock.unlock() }; state = next }
}

final class PasswordCompletionLockProbe {
    private let lock = NSLock()
    private let observation = NSLock()
    private var released = false
    func withLock(_ state: NativePasswordCurrentState, body: (NativePasswordCurrentState) throws -> Void) rethrows {
        lock.lock()
        defer {
            lock.unlock()
            observation.lock(); released = true; observation.unlock()
        }
        try body(state)
    }
    func wasReleased() -> Bool { observation.lock(); defer { observation.unlock() }; return released }
}

@main
struct NativeVaultPasswordCorpus {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        guard condition() else { fatalError(message) }
    }
    static func rejects(_ body: () throws -> Void, _ message: String) {
        do { try body(); fatalError(message) } catch { }
    }
    @MainActor static func waitUntil(_ message: String, _ condition: () -> Bool) {
        let deadline = Date().addingTimeInterval(5)
        while !condition() && Date() < deadline { _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01)) }
        require(condition(), message)
    }
    @MainActor static func main() {
        let subject = "11111111-1111-4111-8111-111111111111"
        let org = "22222222-2222-4222-8222-222222222222"
        let item = "33333333-3333-4333-8333-333333333333"
        // org-default-exempt: a fixture must NAME the inert field to prove it is ignored
        let report = "{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":\"P\"}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}".data(using: .utf8)!
        let decoded = try! NativeOrganizationCodec.organizations(report, subject: subject)
        require(decoded.count == 1 && decoded[0].id == org, "valid organization report must decode")
        // THE SAVED DEFAULT IS INERT (Arman, 2026-09-19). This report names an
        // account-level default the user is not even a member of. The decoder
        // must hand back the memberships and nothing else — it used to return
        // that id as `selected`, and `selectOrganization` used to build the
        // AutoFill request under it, which is a password read out of a tenant
        // the person never chose on this Mac.
        let strangerOrg = "44444444-4444-4444-8444-444444444444"
        // org-default-exempt: a fixture must NAME the inert field to prove it is ignored
        let withStrangerDefault = "{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":\"P\"}],\"default_organization_id\":\"\(strangerOrg)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}".data(using: .utf8)!
        let ignoring = try! NativeOrganizationCodec.organizations(withStrangerDefault, subject: subject)
        require(ignoring.count == 1 && ignoring[0].id == org, "the account-level default must not reach the caller")
        let match = "{\"matches\":[{\"item_id\":\"\(item)\",\"display_name\":\"Example\",\"request_identifier_index\":0}],\"truncated\":false,\"reason\":null}".data(using: .utf8)!
        require((try! NativePasswordCodec.matches(match)).matches.first?.itemID == item, "value-free match must decode")
        rejects({ _ = try NativePasswordCodec.matches("{\"matches\":[],\"matches\":[],\"truncated\":false,\"reason\":null}".data(using: .utf8)!) }, "duplicate response keys must fail")
        rejects({ _ = try NativePasswordCodec.materialized("{\"username\":\"u\",\"password\":\"\"}".data(using: .utf8)!) }, "empty password must fail")
        rejects({ _ = try NativePasswordCodec.materialized("{\"username\":\"u\",\"password\":\"p\",\"extra\":true}".data(using: .utf8)!) }, "unknown materialize key must fail")
        let coordinator = NativePasswordOperationCoordinator()
        let first = coordinator.begin([("url", "https://one.example")])
        require(coordinator.current(first), "new operation must be current")
        let replacement = coordinator.begin([("url", "https://two.example")])
        require(!coordinator.current(first) && coordinator.current(replacement), "replacement must terminalize only the old operation")
        coordinator.clearCompleted(first)
        require(coordinator.current(replacement), "stale completion must not clear replacement")
        require(coordinator.prepareCompletion(replacement), "current operation completes once")
        require(!coordinator.prepareCompletion(replacement), "completion must be exactly once")
        let actual = try! NativePasswordStage.identifiers([ASCredentialServiceIdentifier(identifier: "one.example", type: .domain), ASCredentialServiceIdentifier(identifier: "https://two.example/path", type: .URL)])
        require(actual.count == 2 && actual[0].1 == "one.example" && actual[1].1 == "https://two.example/path", "actual Apple identifier order must be preserved")
        let oversized = ASCredentialServiceIdentifier(identifier: String(repeating: "a", count: 2049), type: .domain)
        rejects({ _ = try NativePasswordStage.identifiers([oversized]) }, "oversized Apple identifier must reject the whole request")
        let grant = NativeVaultSessionAccess.Grant(accessToken: "token", subject: subject, generation: "generation-a")
        require(NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-a", subject: subject), grant), "current state must admit matching grant")
        require(!NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-b", subject: subject), grant), "generation change must reject stale grant")
        require(!NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-a", subject: org), grant), "subject change must reject stale grant")
        require(NativePasswordStage.interactionRequiredCode == ASExtensionError.userInteractionRequired.rawValue, "modern no-interaction policy must require interaction")
        let response = { (json: String) in (json.data(using: .utf8)!, HTTPURLResponse(url: URL(string: "https://server.app.matrxserver.com")!, statusCode: 200, httpVersion: nil, headerFields: nil)!) }
        let transport = ScriptedPasswordTransport()
        // org-default-exempt: a fixture must NAME the inert field to prove it is ignored
        transport.replies = [.success(response("{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":null}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}")), .success(response("{\"matches\":[{\"item_id\":\"\(item)\",\"display_name\":\"Example\",\"request_identifier_index\":0}],\"truncated\":false,\"reason\":null}")), .success(response("{\"username\":\"u\",\"password\":\"p\"}"))]
        let controller = CredentialProviderViewController(); controller.nativePasswordKeyOverride = "public-build-key"; controller.nativePasswordTransport = transport; controller.nativePasswordAuthorize = { $0(true) }; controller.nativePasswordAcquire = { $0(.success(grant)) }; controller.nativePasswordCurrentState = { NativePasswordCurrentState(generation: "generation-a", subject: subject) }; controller.nativePasswordOrganizationChoice = { _ in 0 }; controller.nativePasswordMatchChoice = { _ in 0 }
        var completed: [(String, String)] = []; var cancellations: [NSError] = []
        controller.nativePasswordCompleteSink = { credential, done in completed.append(credential); done() }; controller.nativePasswordCancelSink = { cancellations.append($0) }
        controller.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
        waitUntil("controller did not finish") { !completed.isEmpty || !cancellations.isEmpty }
        require(completed.count == 1 && completed[0].0 == "u" && completed[0].1 == "p" && cancellations.isEmpty && transport.requests.count == 3, "actual controller prepare path must reach exactly one completion")
        let identity = ASPasswordCredentialIdentity(serviceIdentifier: ASCredentialServiceIdentifier(identifier: "example.com", type: .domain), user: "u", recordIdentifier: "test")
        controller.provideCredentialWithoutUserInteraction(for: ASPasswordCredentialRequest(credentialIdentity: identity))
        require(cancellations.last?.code == ASExtensionError.userInteractionRequired.rawValue, "actual modern callback must require interaction")

        // The actual direct-selection callback uses its signed binding's exact
        // organization and service digest. It never opens the generic picker.
        let selectedTransport = ScriptedPasswordTransport()
        // org-default-exempt: synthetic server envelope exercises rejection of saved-preference authority; never selects a request scope.
        selectedTransport.replies = [.success(response("{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":null}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}")), .success(response("{\"matches\":[{\"item_id\":\"\(item)\",\"display_name\":\"Example\",\"request_identifier_index\":0}],\"truncated\":false,\"reason\":null}")), .success(response("{\"username\":\"u\",\"password\":\"p\"}"))]
        let selected = CredentialProviderViewController(); selected.nativePasswordKeyOverride = "public-build-key"; selected.nativePasswordTransport = selectedTransport; selected.nativePasswordAuthorize = { $0(true) }; selected.nativePasswordAcquire = { $0(.success(grant)) }; selected.nativePasswordCurrentState = { NativePasswordCurrentState(generation: grant.generation, subject: subject) }
        selected.nativeIdentityBindingOverride = { _ in NativeVaultIdentityBinding(item: item, kind: .password, organization: org, passkey: nil, serviceDigest: NativeVaultIdentityRecord.serviceDigest(type: "domain", identifier: "example.com")) }
        var selectedCompleted = 0; var selectedCancelled = 0
        selected.nativePasswordCompleteSink = { _, done in selectedCompleted += 1; done() }; selected.nativePasswordCancelSink = { _ in selectedCancelled += 1 }
        selected.prepareInterfaceToProvideCredential(for: ASPasswordCredentialRequest(credentialIdentity: identity))
        waitUntil("selected controller callback did not finish") { selectedCompleted > 0 || selectedCancelled > 0 }
        require(selectedCompleted == 1 && selectedCancelled == 0 && selectedTransport.requests.count == 3, "selected callback must use its exact signed account binding")

        // Losing membership in a suggestion's bound organization must refuse
        // before listing another tenant's matches, even with one membership left.
        let missingBindingTransport = ScriptedPasswordTransport()
        missingBindingTransport.replies = [.success((report, response("{}").1)), .failure(URLError(.cancelled))]
        let missingBinding = CredentialProviderViewController()
        missingBinding.nativePasswordKeyOverride = "public-build-key"
        missingBinding.nativePasswordTransport = missingBindingTransport
        missingBinding.nativePasswordAuthorize = { $0(true) }
        missingBinding.nativePasswordAcquire = { $0(.success(grant)) }
        missingBinding.nativePasswordCurrentState = { NativePasswordCurrentState(generation: grant.generation, subject: subject) }
        missingBinding.nativeIdentityBindingOverride = { _ in NativeVaultIdentityBinding(item: item, kind: .password, organization: strangerOrg, passkey: nil, serviceDigest: NativeVaultIdentityRecord.serviceDigest(type: "domain", identifier: "example.com")) }
        var missingBindingCancelled = 0
        missingBinding.nativePasswordCancelSink = { _ in missingBindingCancelled += 1 }
        missingBinding.nativePasswordCompleteSink = { _, _ in fatalError("Missing bound membership must never complete") }
        missingBinding.prepareInterfaceToProvideCredential(for: ASPasswordCredentialRequest(credentialIdentity: identity))
        waitUntil("missing suggestion membership did not cancel") { missingBindingCancelled == 1 }
        require(missingBindingTransport.requests.count == 1, "missing bound membership must not fall back to another organization")

        let routeReplies = [Result<(Data, HTTPURLResponse), Error>.success((report, response("{}").1)), .success((match, response("{}").1)), .success(response("{\"username\":\"u\",\"password\":\"p\"}"))]
        for stage in 1...3 {
            for changedSubject in [false, true] {
                let state = PasswordStateBox(NativePasswordCurrentState(generation: grant.generation, subject: subject))
                let stagedTransport = ScriptedPasswordTransport(); stagedTransport.replies = routeReplies
                stagedTransport.beforeReply = { count in
                    if count == stage { state.replace(NativePasswordCurrentState(generation: changedSubject ? grant.generation : "new-generation", subject: changedSubject ? org : subject)) }
                }
                let staged = CredentialProviderViewController(); staged.nativePasswordKeyOverride = "public-build-key"; staged.nativePasswordTransport = stagedTransport; staged.nativePasswordAuthorize = { $0(true) }; staged.nativePasswordAcquire = { $0(.success(grant)) }; staged.nativePasswordCurrentState = { state.read() }; staged.nativePasswordOrganizationChoice = { _ in 0 }; staged.nativePasswordMatchChoice = { _ in 0 }
                var delivered = 0; var refused = 0
                staged.nativePasswordCompleteSink = { _, _ in delivered += 1 }; staged.nativePasswordCancelSink = { _ in refused += 1 }
                staged.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
                waitUntil("controller did not reject changed account") { refused > 0 || delivered > 0 }
                require(delivered == 0 && refused == 1 && stagedTransport.requests.count == stage, "actual controller must fence account change at response stage \(stage)")
            }
        }
        let rejectedController = CredentialProviderViewController(); rejectedController.nativePasswordKeyOverride = "public-build-key"
        var authorizationCalls = 0; var inputRefusals = 0
        rejectedController.nativePasswordAuthorize = { done in authorizationCalls += 1; done(false) }
        rejectedController.nativePasswordCancelSink = { _ in inputRefusals += 1 }
        rejectedController.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain), oversized])
        require(inputRefusals == 1 && authorizationCalls == 0, "actual controller must reject the whole identifier request before authorization")

        let lockProbe = PasswordCompletionLockProbe()
        let noCallbackTransport = ScriptedPasswordTransport(); noCallbackTransport.replies = routeReplies
        let noCallback = CredentialProviderViewController(); noCallback.nativePasswordKeyOverride = "public-build-key"; noCallback.nativePasswordTransport = noCallbackTransport; noCallback.nativePasswordAuthorize = { $0(true) }; noCallback.nativePasswordAcquire = { $0(.success(grant)) }; noCallback.nativePasswordCurrentState = { NativePasswordCurrentState(generation: grant.generation, subject: subject) }; noCallback.nativePasswordOrganizationChoice = { _ in 0 }; noCallback.nativePasswordMatchChoice = { _ in 0 }
        noCallback.nativePasswordCompletionLock = { body in try lockProbe.withLock(NativePasswordCurrentState(generation: grant.generation, subject: subject), body: body) }
        var noCallbackDelivered = 0; var heldDuringDelivery = false; var noCallbackRefused = 0
        // Apple is allowed never to call this handler. Hold it deliberately.
        noCallback.nativePasswordCompleteSink = { _, _ in noCallbackDelivered += 1; heldDuringDelivery = !lockProbe.wasReleased() }
        noCallback.nativePasswordCancelSink = { _ in noCallbackRefused += 1 }
        noCallback.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
        waitUntil("completion lock must release without Apple's callback") { lockProbe.wasReleased() }
        require(noCallbackDelivered == 1 && heldDuringDelivery && noCallbackRefused == 0, "completion must invoke Apple once under lock, then release without callback")
        // The actual Apple passkey entrypoint must invalidate an older password
        // before validation, even when the new request itself is unsupported.
        let crossFlow = CredentialProviderViewController()
        crossFlow.nativePasswordKeyOverride = "public-build-key"
        var heldAuthorization: ((Bool) -> Void)?
        var staleAcquires = 0; var replacementRefusals = 0
        crossFlow.nativePasswordAuthorize = { heldAuthorization = $0 }
        crossFlow.nativePasswordAcquire = { _ in staleAcquires += 1 }
        crossFlow.nativePasskeyCoordinator = NativeVaultPasskeyCoordinator(
            sessionAccess: NativeVaultSessionAccess(), key: { "public-build-key" },
            cancel: { _ in replacementRefusals += 1 },
            completeRegistration: { _ in fatalError("invalid request registered") },
            completeAssertion: { _ in fatalError("invalid request asserted") })
        crossFlow.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
        let oldPassword = crossFlow.nativePasswordCoordinator.active!
        let oldLifetime = crossFlow.nativeRequest
        var cancelledResources = 0
        oldLifetime.own { cancelledResources += 1 }
        crossFlow.prepareInterface(forPasskeyRegistration: ASPasswordCredentialRequest(credentialIdentity: identity))
        require(oldPassword.terminal && !oldLifetime.isCurrent && cancelledResources == 1 && replacementRefusals == 1,
                "cross-flow replacement must retire password and owned resources before refusing new request")
        heldAuthorization?(true)
        var actorDrained = false
        Task { @MainActor in actorDrained = true }
        waitUntil("replacement actor did not drain") { actorDrained }
        require(staleAcquires == 0, "late password authorization must not acquire credentials for a replacement request")
        var lateResourceCancelled = false
        oldLifetime.own { lateResourceCancelled = true }
        require(lateResourceCancelled, "resource registered after cancellation must be cancelled immediately")
        var transportSettled = 0
        let cancelledTransport = BoundedTransport { result in
            if case .failure = result { transportSettled += 1 }
        }
        cancelledTransport.cancel(); cancelledTransport.cancel()
        cancelledTransport.start(URLRequest(url: URL(string: "https://must-not-send.invalid")!))
        require(transportSettled == 1, "cancel-before-dispatch must settle once and never start a URL task")
        // Drive the actual password callback through real session acquisition.
        // Only operating-system boundaries are injected; cancellation must not
        // reach AuthenticationServices before Apple's cleanup callback returns.
        let cleanupRoot = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: cleanupRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: cleanupRoot) }
        let cleanupDisk = try! ProviderStore(testRoot: cleanupRoot, mode: .explicitConnect)
        _ = try! cleanupDisk.initializeExplicitConnect(invalidatePrivate: {})
        let cleanupGeneration = "44444444-4444-4444-8444-444444444444"
        try! cleanupDisk.write(PublicState(version: 2, generation: cleanupGeneration, host_subject: nil, provider_subject: subject))
        let missingKeychain = NativeVaultPrivateSession(security: PrivateSessionSecurity(copyMatching: { _, _ in errSecItemNotFound }, add: { _, _ in fatalError("Missing session cannot be saved") }, delete: { _ in errSecSuccess }))
        let indexLock = NSLock()
        var indexCallback: ((Bool, Error?) -> Void)?
        let realAccess = NativeVaultSessionAccess(store: { try ProviderStore(testRoot: cleanupRoot, mode: .providerAccess) }, privateSession: { missingKeychain }, identityIndex: ProviderIdentityIndex { done in indexLock.lock(); indexCallback = done; indexLock.unlock() })
        let cleanupController = CredentialProviderViewController()
        cleanupController.nativePasswordKeyOverride = "public-build-key"
        cleanupController.nativePasswordAuthorize = { $0(true) }
        cleanupController.nativePasswordAcquire = { done in realAccess.acquire(key: "public-build-key", context: LAContext(), lifetime: cleanupController.nativeRequest, completion: done) }
        var cleanupCancellations = 0
        cleanupController.nativePasswordCancelSink = { _ in
            require((try! cleanupDisk.read()).provider_subject == nil, "Controller cancellation must see durable invalidation")
            cleanupCancellations += 1
        }
        cleanupController.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
        waitUntil("actual session did not reach Apple cleanup") { indexLock.lock(); defer { indexLock.unlock() }; return indexCallback != nil }
        require(cleanupCancellations == 0, "Password callback must remain open while Apple cleanup is outstanding")
        indexLock.lock(); let releaseIndex = indexCallback!; indexLock.unlock()
        releaseIndex(true, nil)
        waitUntil("controller did not cancel after cleanup") { cleanupCancellations == 1 }
        // Actual password controller post-grant timeout must stale the captured
        // ready snapshot before it cancels the credential request.
        let staleRoot = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("native-post-grant-\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: staleRoot, withIntermediateDirectories: true); defer { try? FileManager.default.removeItem(at: staleRoot) }
        let staleDisk = try! ProviderStore(testRoot: staleRoot, mode: .explicitConnect); _ = try! staleDisk.initializeExplicitConnect(invalidatePrivate: {})
        let staleGeneration = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", staleRevision = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        let staleState = PublicState(version: 2, generation: staleGeneration, host_subject: nil, provider_subject: subject, suggestions: NativeSuggestions(organization_id: org, revision: staleRevision, status: "ready", refreshed_at_ms: 77, count: 4, unsupported_count: 1))
        try! staleDisk.write(staleState)
        let staleController = CredentialProviderViewController(); staleController.nativePasswordKeyOverride = "public-build-key"
        staleController.sessionAccess = NativeVaultSessionAccess(store: { try ProviderStore(testRoot: staleRoot, mode: .providerAccess) }, identityIndex: ProviderIdentityIndex { _ in fatalError("Ambiguous password failure must preserve Apple entries") })
        let staleTransport = ScriptedPasswordTransport(); staleTransport.replies = [.failure(URLError(.timedOut))]; staleController.nativePasswordTransport = staleTransport
        staleController.nativePasswordAuthorize = { $0(true) }; staleController.nativePasswordAcquire = { $0(.success(NativeVaultSessionAccess.Grant(accessToken: "token", subject: subject, generation: staleGeneration, suggestionState: staleState))) }; staleController.nativePasswordCurrentState = { NativePasswordCurrentState(generation: staleGeneration, subject: subject) }
        var staleCancels = 0; staleController.nativePasswordCancelSink = { _ in
            let state = try! staleDisk.read(); require(state.suggestions.status == "stale" && state.suggestions.revision == staleRevision && state.suggestions.count == 4 && state.suggestions.unsupported_count == 1, "password post-grant timeout must stale before cancellation"); staleCancels += 1
        }
        staleController.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: "example.com", type: .domain)])
        waitUntil("post-grant timeout did not cancel") { staleCancels == 1 }
        // The actual scope-picker organization request must use the same
        // captured response reconciliation before any picker presentation.
        try! staleDisk.write(staleState)
        let scopeController = CredentialProviderViewController()
        scopeController.sessionAccess = NativeVaultSessionAccess(store: { try ProviderStore(testRoot: staleRoot, mode: .providerAccess) }, identityIndex: ProviderIdentityIndex { _ in fatalError("Scope 503 must preserve Apple entries") })
        let scopeTransport = ScriptedPasswordTransport(); let scopeHTTP = HTTPURLResponse(url: URL(string: "https://server.app.matrxserver.com")!, statusCode: 503, httpVersion: nil, headerFields: nil)!; scopeTransport.replies = [.success((Data("{}".utf8), scopeHTTP))]; scopeController.nativePasswordTransport = scopeTransport
        scopeController.presentSuggestionScopePicker(grant: NativeVaultSessionAccess.Grant(accessToken: "token", subject: subject, generation: staleGeneration, suggestionState: staleState), lifetime: scopeController.nativeRequest)
        waitUntil("scope failure did not stale captured state") {
            let state = try! staleDisk.read()
            return state.suggestions.status == "stale"
        }
        let scopeState = try! staleDisk.read()
        require(scopeState.suggestions.revision == staleRevision && scopeState.suggestions.count == 4 && scopeState.suggestions.unsupported_count == 1, "scope failure must preserve suggestion metadata")
        print("Native Vault password codec corpus passed")
    }
}
