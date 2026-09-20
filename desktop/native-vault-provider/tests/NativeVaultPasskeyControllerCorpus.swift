import AuthenticationServices
import CryptoKit
import Foundation
import LocalAuthentication

private typealias Reply = (Result<(Data, HTTPURLResponse), Error>) -> Void
private enum Response { case ok, timeout, missing, corrupt, excluded, conflict, serverError }
private final class ExclusionRequest: ASPasskeyCredentialRequest {
    var descriptors: [ASAuthorizationPlatformPublicKeyCredentialDescriptor] = []
    override var excludedCredentials: [ASAuthorizationPlatformPublicKeyCredentialDescriptor]? { descriptors }
}

/// An external HTTP boundary. Every successful ceremony still runs the real
/// controller, coordinator, generated Swift bridge and maintained authenticator.
private final class Server: NativeVaultPasskeyTransporting {
    var requests: [URLRequest] = []
    var posts: [Data] = []
    var postResponses: [Response] = []
    var receiptResponses: [Response] = []
    var source: Data?
    var receipt: [String: Any]?
    var wrongMatchHandle = false
    var maximumBodyBytes = 131072
    var holdSuffix: String?
    var didSend: ((URLRequest) -> Void)?
    private let lock = NSLock()
    private var pending: Reply?
    private var cancelled = 0
    var cancellations: Int { lock.lock(); defer { lock.unlock() }; return cancelled }
    var isPending: Bool { lock.lock(); defer { lock.unlock() }; return pending != nil }
    func cancel() {
        lock.lock(); cancelled += 1; let callback = pending; pending = nil; lock.unlock()
        callback?(.failure(URLError(.cancelled)))
    }
    static func decode(_ value: String) -> Data {
        let text = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        return Data(base64Encoded: text + String(repeating: "=", count: (4 - text.count % 4) % 4))!
    }
    var sourceObject: [String: Any] { try! JSONSerialization.jsonObject(with: source!) as! [String: Any] }
    func send(_ request: URLRequest, completion: @escaping Reply) {
        requests.append(request); didSend?(request)
        let path = request.url!.path
        if let holdSuffix, path.hasSuffix(holdSuffix) {
            lock.lock(); precondition(pending == nil); pending = completion; lock.unlock(); return
        }
        var response = Response.ok
        var object: [String: Any]
        if path == "/api/auth/organizations" {
            // org-default-exempt: a fixture must NAME the inert field to prove it is ignored
            object = ["authenticated": true, "user_id": "subject", "organizations": [["id": "00000000-0000-4000-8000-000000000001", "name": "Personal", "is_personal": true, "abbreviation": NSNull()]], "default_organization_id": NSNull(), "default_preference_status": "unset", "warnings": [], "missing_organization_count": 0]
        } else if path.hasSuffix("/capabilities") {
            object = ["protocol_version": 1, "activation_revision": 1, "max_source_bytes": 65536, "max_credential_ids": 128, "max_request_body_bytes": maximumBodyBytes, "algorithms": [-7]]
        } else if path.hasSuffix("/matches") {
            var matches: [[String: Any]] = []
            if source != nil {
                let value = sourceObject
                matches = [["item_id": "00000000-0000-4000-8000-000000000002", "passkey_id": "00000000-0000-4000-8000-000000000004", "credential_id": value["credential_id"]!, "user_handle": wrongMatchHandle ? NativeVaultPasskeyCodec.base64url(Data("other".utf8)) : value["user_handle"]!, "username": "user", "display_name": NSNull()]]
            }
            object = ["matches": matches, "truncated": false]
        } else if path.hasSuffix("/materialize") {
            object = ["source": NativeVaultPasskeyCodec.base64url(source!)]
        } else if path.contains("/receipts/") {
            response = receiptResponses.isEmpty ? .ok : receiptResponses.removeFirst()
            object = receipt ?? [:]
        } else if path.hasSuffix("/passkeys") {
            let body = request.httpBody!; posts.append(body)
            let value = try! JSONSerialization.jsonObject(with: body) as! [String: Any]
            let saved = Self.decode(value["source"] as! String); source = saved
            receipt = ["mutation_id": value["mutation_id"]!, "item_id": "00000000-0000-4000-8000-000000000002", "field_id": "00000000-0000-4000-8000-000000000003", "passkey_id": "00000000-0000-4000-8000-000000000004", "source_sha256": NativeVaultPasskeyCodec.base64url(Data(SHA256.hash(data: saved))), "status": "saved_waiting_for_site"]
            response = postResponses.isEmpty ? .ok : postResponses.removeFirst()
            object = receipt!
        } else { preconditionFailure("unexpected provider endpoint") }
        var status = 200
        switch response {
        case .ok: break
        case .timeout: completion(.failure(URLError(.timedOut))); return
        case .missing: status = 404; object = ["detail": ["code": "native_unavailable"]]
        case .corrupt: object["source_sha256"] = NativeVaultPasskeyCodec.base64url(Data(repeating: 0, count: 32))
        case .excluded: status = 409; object = ["detail": ["code": "credential_excluded"]]
        case .conflict: status = 409; object = ["detail": ["code": "mutation_conflict"]]
        case .serverError: status = 503; object = ["detail": ["code": "native_unavailable"]]
        }
        completion(.success((try! JSONSerialization.data(withJSONObject: object), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)))
    }
}

private final class Generation {
    private let lock = NSLock()
    private var value = "generation"
    func replace() { lock.lock(); value = "replacement"; lock.unlock() }
    func read() -> NativePasswordCurrentState { lock.lock(); defer { lock.unlock() }; return NativePasswordCurrentState(generation: value, subject: "subject") }
}

@MainActor private final class Journey {
    let server: Server
    let generation = Generation()
    let controller = CredentialProviderViewController()
    var coordinator: NativeVaultPasskeyCoordinator!
    var registration: ASPasskeyRegistrationCredential?
    var assertion: ASPasskeyAssertionCredential?
    var error: String?
    var evaluations: [ObjectIdentifier] = []
    var acquired = 0
    var denyEvaluation: Int?
    var lockEntered = false
    var lockRelease: DispatchSemaphore?
    var completionUnderLock = false
    private var lockHeld = false
    init(server: Server = Server(), timeout: TimeInterval = 3) {
        self.server = server
        let grant = NativeVaultSessionAccess.Grant(accessToken: "synthetic-token", subject: "subject", generation: "generation")
        coordinator = NativeVaultPasskeyCoordinator(
            sessionAccess: NativeVaultSessionAccess(), transport: server, key: { "synthetic-key" },
            cancel: { [unowned self] in error = $0 },
            completeRegistration: { [unowned self] in precondition(lockHeld && !server.posts.isEmpty); completionUnderLock = true; registration = $0 },
            completeAssertion: { [unowned self] in precondition(lockHeld); completionUnderLock = true; assertion = $0 },
            acquire: { [unowned self] callback in acquired += 1; callback(.success(grant)) },
            currentState: { [generation] in generation.read() },
            completionLock: { [unowned self] body in
                // Deliberately block the *background* lock path. A blocking
                // main-actor implementation cannot pass the race below.
                precondition(!Thread.isMainThread)
                if let barrier = lockRelease { DispatchQueue.main.async { self.lockEntered = true }; barrier.wait() }
                lockHeld = true; defer { lockHeld = false }; try body(generation.read())
            }, organizationChoice: { _ in 0 }, matchChoice: { _ in 0 }, label: { $0 },
            evaluator: { [unowned self] context, _, callback in
                precondition(!context.interactionNotAllowed, "second verification must permit OS interaction")
                evaluations.append(ObjectIdentifier(context))
                // Model the real Keychain read's effect between evaluations.
                context.interactionNotAllowed = true
                callback(denyEvaluation != evaluations.count)
            }, operationTimeout: timeout)
        controller.nativePasskeyCoordinator = coordinator
        controller.nativePasswordCancelSink = { _ in }
    }
    func register(_ request: ASPasskeyCredentialRequest = makeRequest()) { controller.prepareInterface(forPasskeyRegistration: request) }
    func authenticate(_ request: ASPasskeyCredentialRequest) { controller.prepareInterfaceToProvideCredential(for: request) }
    var done: Bool { registration != nil || assertion != nil || error != nil }
    func finish() async { await wait { self.done } }
}

@MainActor private func wait(_ condition: @escaping () -> Bool) async {
    for _ in 0..<1000 { if condition() { return }; try? await Task.sleep(nanoseconds: 5_000_000) }
    preconditionFailure("native callback did not settle")
}

private func makeRequest(rp: String = "example.com", credential: Data = Data(repeating: 7, count: 16), handle: Data = Data("handle".utf8), hash: Data = Data(repeating: 9, count: 32)) -> ASPasskeyCredentialRequest {
    let identity = ASPasskeyCredentialIdentity(relyingPartyIdentifier: rp, userName: "user", credentialID: credential, userHandle: handle, recordIdentifier: nil)
    return ASPasskeyCredentialRequest(credentialIdentity: identity, clientDataHash: hash, userVerificationPreference: .preferred, supportedAlgorithms: [ASCOSEAlgorithmIdentifier(rawValue: -7)])
}

@main struct NativeVaultPasskeyControllerCorpus {
    @MainActor static func main() async {
        for path in ["success", "redirect", "failure", "hold"] {
            weak var bounded: BoundedTransport?
            let transport = NativeVaultPasskeyTransport { completion in
                let value = BoundedTransport(completion); bounded = value; return value
            }
            var settled = false; var status: Int?
            let port = ProcessInfo.processInfo.environment["NATIVE_VAULT_TEST_HTTP_PORT"]!
            transport.send(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/\(path)")!)) { result in
                DispatchQueue.main.async { if case let .success(value) = result { status = value.1.statusCode }; settled = true }
            }
            if path == "hold" { transport.cancel() }
            await wait { settled }
            precondition(status == (path == "success" ? 200 : path == "redirect" ? 302 : nil), "transport outcome mismatch: \(path)")
            await wait { bounded == nil }
        }
        for blank in ["", " \n "] {
            let match = NativePasskeyMatch(itemID: "item", passkeyID: "passkey", credentialID: Data(), userHandle: Data(), username: "account@example.com", displayName: blank)
            precondition(NativeVaultPasskeyCoordinator.accountLabel(match, index: 0) == "account@example.com")
        }
        let positive = Journey(); positive.register(); await positive.finish()
        precondition(positive.registration != nil && positive.error == nil && positive.completionUnderLock)
        precondition(positive.evaluations.count == 2 && positive.evaluations[0] == positive.evaluations[1])
        let source = positive.server.source!
        var envelope = try! JSONSerialization.jsonObject(with: positive.server.posts[0]) as! [String: Any]
        envelope["source"] = ""
        let envelopeBytes = try! JSONSerialization.data(withJSONObject: envelope, options: [.sortedKeys]).count
        let tightServer = Server(); tightServer.maximumBodyBytes = envelopeBytes + 4 * ((source.count + 2) / 3)
        let tight = Journey(server: tightServer); tight.register(); await tight.finish()
        precondition(tight.registration != nil, "a valid source fitting the advertised body limit was refused")
        precondition(tightServer.posts[0].count <= tightServer.maximumBodyBytes)
        let object = positive.server.sourceObject
        let credentialID = Server.decode(object["credential_id"] as! String)
        let handle = Server.decode(object["user_handle"] as! String)
        let assertionServer = Server(); assertionServer.source = source
        let assertion = Journey(server: assertionServer)
        assertion.authenticate(makeRequest(credential: credentialID, handle: handle)); await assertion.finish()
        precondition(assertion.assertion != nil && assertion.error == nil && assertion.completionUnderLock)
        precondition(assertion.assertion!.credentialID == credentialID && assertion.assertion!.userHandle == handle)
        precondition(assertion.assertion!.clientDataHash == Data(repeating: 9, count: 32))
        let publicProof = [
            "attestation": positive.registration!.attestationObject.base64EncodedString(),
            "credential_id": credentialID.base64EncodedString(),
            "authenticator_data": assertion.assertion!.authenticatorData.base64EncodedString(),
            "signature": assertion.assertion!.signature.base64EncodedString(),
            "client_data_hash": assertion.assertion!.clientDataHash.base64EncodedString(),
        ]
        // Public ceremony outputs only; no canonical source/private key leaves
        // the test process. An independent maintained verifier checks these.
        try! JSONSerialization.data(withJSONObject: publicProof).write(to: URL(fileURLWithPath: ProcessInfo.processInfo.environment["NATIVE_VAULT_TEST_PROOF"]!))

        for denial in [1, 2] {
            let test = Journey(); test.denyEvaluation = denial; test.register(); await test.finish()
            precondition(test.registration == nil && test.server.posts.isEmpty)
            precondition(test.acquired == (denial == 1 ? 0 : 1))
        }
        for malformed in [makeRequest(rp: ""), makeRequest(rp: String(repeating: "a", count: 254)), makeRequest(handle: Data()), makeRequest(hash: Data(repeating: 0, count: 31))] {
            let test = Journey(); test.register(malformed); await test.finish()
            precondition(test.acquired == 0 && test.evaluations.isEmpty && test.server.requests.isEmpty)
        }
        for handle in [Data(), Data(repeating: 1, count: 65)] {
            let test = Journey(); test.authenticate(makeRequest(handle: handle)); await test.finish()
            precondition(test.acquired == 0 && test.evaluations.isEmpty && test.server.requests.isEmpty)
        }
        for excluded in [[Data(repeating: 1, count: 15)], [Data(repeating: 1, count: 1024)], Array(repeating: Data(repeating: 1, count: 16), count: 2), (0..<129).map { Data(repeating: UInt8($0), count: 16) }] {
            let request = ExclusionRequest(credentialIdentity: ASPasskeyCredentialIdentity(relyingPartyIdentifier: "example.com", userName: "user", credentialID: credentialID, userHandle: handle, recordIdentifier: nil), clientDataHash: Data(repeating: 9, count: 32), userVerificationPreference: .required, supportedAlgorithms: [ASCOSEAlgorithmIdentifier(rawValue: -7)])
            request.descriptors = excluded.map { ASAuthorizationPlatformPublicKeyCredentialDescriptor(credentialID: $0) }
            let test = Journey(); test.register(request); await test.finish()
            precondition(test.acquired == 0 && test.evaluations.isEmpty && test.server.requests.isEmpty)
        }
        for responses: ([Response], [Response], Int) in [([.timeout], [.ok], 1), ([.timeout, .ok], [.missing], 2), ([.serverError, .timeout], [.missing, .ok], 2), ([.conflict], [.ok], 1)] {
            let server = Server(); server.postResponses = responses.0; server.receiptResponses = responses.1
            let test = Journey(server: server); test.register(); await test.finish()
            precondition(test.registration != nil && test.error == nil && server.posts.count == responses.2)
            if server.posts.count == 2 { precondition(server.posts[0] == server.posts[1], "retry generated another key or mutation") }
        }
        for response in [Response.corrupt, .excluded, .conflict] {
            let server = Server(); server.postResponses = [response]; server.receiptResponses = [.corrupt]
            let test = Journey(server: server); test.register(); await test.finish()
            precondition(test.registration == nil && server.posts.count == 1)
        }
        let uncertainServer = Server(); uncertainServer.postResponses = [.timeout, .timeout]; uncertainServer.receiptResponses = [.missing, .missing]
        let uncertain = Journey(server: uncertainServer); uncertain.register(); await uncertain.finish()
        precondition(uncertain.registration == nil && uncertain.error!.contains("may already be saved"))
        precondition(uncertainServer.posts.count == 2 && uncertainServer.posts[0] == uncertainServer.posts[1])
        for wrong in [makeRequest(rp: "wrong.example", credential: credentialID, handle: handle), makeRequest(credential: Data(repeating: 1, count: 16), handle: handle), makeRequest(credential: credentialID, handle: Data("wrong".utf8))] {
            let server = Server(); server.source = source
            let test = Journey(server: server); test.authenticate(wrong); await test.finish(); precondition(test.assertion == nil)
        }
        let stale = Journey(); stale.server.didSend = { request in if request.url!.path.hasSuffix("/capabilities") { stale.generation.replace() } }
        stale.register(); await stale.finish(); precondition(stale.registration == nil && stale.server.posts.isEmpty)

        for path in ["/organizations", "/capabilities", "/passkeys"] {
            let server = Server(); server.holdSuffix = path
            let test = Journey(server: server); test.register(); await wait { server.isPending }
            let cancelledBefore = server.cancellations
            test.controller.provideCredentialWithoutUserInteraction(for: makeRequest())
            await wait { !server.isPending }
            try? await Task.sleep(nanoseconds: 20_000_000)
            precondition(server.cancellations > cancelledBefore && test.registration == nil && test.error == nil)
        }
        let timeoutServer = Server(); timeoutServer.holdSuffix = "/capabilities"
        let timeout = Journey(server: timeoutServer, timeout: 0.05); timeout.register(); await timeout.finish()
        precondition(!timeoutServer.isPending && timeoutServer.cancellations > 0 && timeout.registration == nil)

        let lockRace = Journey(); let barrier = DispatchSemaphore(value: 0); lockRace.lockRelease = barrier
        lockRace.register(); await wait { lockRace.lockEntered }
        lockRace.generation.replace(); barrier.signal(); await lockRace.finish()
        precondition(lockRace.registration == nil)
        print("PASS actual native passkey callbacks: registration, assertion, UV, receipt recovery, exact replay, malformed input, account replacement, cancellation and lock race")
    }
}
