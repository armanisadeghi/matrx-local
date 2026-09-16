import Foundation
import AuthenticationServices

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
        let report = "{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":\"P\"}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}".data(using: .utf8)!
        let decoded = try! NativePasswordCodec.organizations(report, subject: subject)
        require(decoded.organizations.count == 1 && decoded.selected == org, "valid organization report must decode")
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
        print("Native Vault password codec corpus passed")
    }
}
