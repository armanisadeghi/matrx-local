import CryptoKit
import Foundation

private func base64url(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}
private func decode(_ value: String) -> Data? {
    let padded = value.replacingOccurrences(of: "-", with: "+")
        .replacingOccurrences(of: "_", with: "/")
        .padding(toLength: ((value.count + 3) / 4) * 4, withPad: "=", startingAt: 0)
    return Data(base64Encoded: padded)
}
private func clientData(_ type: String, _ challenge: Data) -> Data {
    Data("{\"type\":\"\(type)\",\"challenge\":\"\(base64url(challenge))\",\"origin\":\"https://example.com\",\"crossOrigin\":false}".utf8)
}
private enum CallbackMode { case accept, denied, failed, unexpected, refusedPersistence, failedPersistence, unexpectedPersistence }
private enum Unexpected: Error { case callback }

private actor Ceremony: NativeCeremony {
    private let verification: CallbackMode
    private let persistence: CallbackMode
    private var source: Data?
    private var displayWasNil = false
    private var displayWasEmpty = false
    init(verification: CallbackMode = .accept, persistence: CallbackMode = .accept) { self.verification = verification; self.persistence = persistence }
    func verifyUser() async throws {
        switch verification {
        case .accept: return
        case .denied: throw VerificationCallbackError.Denied
        case .failed: throw VerificationCallbackError.Failed
        case .unexpected: throw VerificationCallbackError.Unexpected
        default: return
        }
    }
    func persistRegistration(canonicalSource: Data) async throws {
        switch persistence {
        case .refusedPersistence: throw PersistenceCallbackError.Refused
        case .failedPersistence: throw PersistenceCallbackError.Failed
        case .unexpectedPersistence: throw PersistenceCallbackError.Unexpected
        default: break
        }
        // Inspect persisted metadata only inside this in-memory harness; the
        // source is never emitted and is used only by the next assertion.
        let payload = try JSONSerialization.jsonObject(with: canonicalSource) as? [String: Any]
        displayWasNil = payload?["display_name"] is NSNull
        displayWasEmpty = (payload?["display_name"] as? String) == ""
        source = canonicalSource
    }
    func savedSource() -> Data? { source }
    func displayStates() -> (Bool, Bool) { (displayWasNil, displayWasEmpty) }
}

private actor GateCeremony: NativeCeremony {
    private var started = false
    private var startWaiter: CheckedContinuation<Void, Never>?
    private var releaseWaiter: CheckedContinuation<Void, Error>?
    func verifyUser() async throws {}
    func persistRegistration(canonicalSource: Data) async throws {
        started = true; startWaiter?.resume(); startWaiter = nil
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in releaseWaiter = continuation }
        }, onCancel: { Task { await self.cancelPersistence() } })
    }
    func waitUntilStarted() async { if started { return }; await withCheckedContinuation { startWaiter = $0 } }
    func release() { releaseWaiter?.resume(); releaseWaiter = nil }
    private func cancelPersistence() { releaseWaiter?.resume(throwing: CancellationError()); releaseWaiter = nil }
}

private func registrationInput(displayName: String? = nil, rpId: String = "example.com") -> NativeRegistrationInput {
    NativeRegistrationInput(rpId: rpId, userHandle: Data("swift-rp-user".utf8), username: "swift-rp-user", displayName: displayName, clientDataHash: Data(repeating: 9, count: 32), supportedAlgorithms: [-7], excludedCredentialIds: [])
}
private func assertionInput(_ credentialId: Data, rpId: String = "example.com", allowed: [Data]? = nil) -> NativeAssertionInput {
    NativeAssertionInput(rpId: rpId, clientDataHash: Data(repeating: 7, count: 32), allowedCredentialIds: allowed ?? [credentialId])
}
private func expect(_ expected: BridgeError, _ body: @escaping @Sendable () async throws -> Void) async throws {
    do { try await body(); throw Unexpected.callback } catch let error as BridgeError where error == expected { return }
}

@main
struct NativeVaultBridgeFido2 {
    static func required<T>(_ value: T?) throws -> T { guard let value else { throw Unexpected.callback }; return value }
    static func acceptance() async throws {
        let nilCeremony = Ceremony()
        let nilRegistration = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: nilCeremony)
        let nilSource = try required(await nilCeremony.savedSource())
        let nilState = await nilCeremony.displayStates()
        guard nilState.0 && !nilState.1 else { throw Unexpected.callback }
        let emptyCeremony = Ceremony()
        _ = try await NativeOperation().register(input: registrationInput(displayName: ""), existingSources: [], maxSourceBytes: 65_536, ceremony: emptyCeremony)
        let emptyState = await emptyCeremony.displayStates()
        guard !emptyState.0 && emptyState.1 else { throw Unexpected.callback }

        // Actual generated callbacks cover declared denied/failed/unexpected outcomes.
        try await expect(.VerificationDenied) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .denied)); return }
        try await expect(.OperationFailed) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .failed)); return }
        try await expect(.OperationFailed) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .unexpected)); return }
        try await expect(.PersistenceFailed) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .refusedPersistence)); return }
        try await expect(.OperationFailed) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .failedPersistence)); return }
        try await expect(.OperationFailed) { try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .unexpectedPersistence)); return }

        let before = NativeOperation(); before.cancel()
        try await expect(.Cancelled) { try await before.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }
        let after = NativeOperation()
        _ = try await after.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony())
        after.cancel()
        try await expect(.AlreadyUsed) { try await after.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }

        // Deterministic callback gates prove both cancel/completion race winners.
        let cancellationGate = GateCeremony(); let cancellationOperation = NativeOperation()
        let cancellationTask = Task { try await cancellationOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: cancellationGate) }
        await cancellationGate.waitUntilStarted(); cancellationOperation.cancel(); await cancellationGate.release()
        do { _ = try await cancellationTask.value; throw Unexpected.callback } catch let error as BridgeError where error == .Cancelled {}
        let completionGate = GateCeremony(); let completionOperation = NativeOperation()
        let completionTask = Task { try await completionOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: completionGate) }
        await completionGate.waitUntilStarted(); await completionGate.release(); _ = try await completionTask.value
        completionOperation.cancel()
        try await expect(.AlreadyUsed) { try await completionOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }

        // The source stays in harness memory while actual bridge checks reject
        // lower limits, wrong RP, and a mismatched allow-list.
        try await expect(.InvalidSource) { try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId), canonicalSource: nilSource, maxSourceBytes: 1, ceremony: Ceremony()); return }
        try await expect(.NoCredentials) { try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId, rpId: "wrong.example"), canonicalSource: nilSource, maxSourceBytes: 65_536, ceremony: Ceremony()); return }
        try await expect(.NoCredentials) { try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId, allowed: [Data(repeating: 1, count: 16)]), canonicalSource: nilSource, maxSourceBytes: 65_536, ceremony: Ceremony()); return }
    }
    static func main() async {
        if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "acceptance" {
            do { try await acceptance(); print("PASS native Swift bridge acceptance") } catch { exit(67) }
            return
        }
        guard CommandLine.arguments.count == 3, let createChallenge = decode(CommandLine.arguments[1]), let getChallenge = decode(CommandLine.arguments[2]) else { exit(64) }
        let ceremony = Ceremony()
        do {
            let registration = try await NativeOperation().register(input: NativeRegistrationInput(rpId: "example.com", userHandle: Data("swift-rp-user".utf8), username: "swift-rp-user", displayName: nil, clientDataHash: Data(SHA256.hash(data: clientData("webauthn.create", createChallenge))), supportedAlgorithms: [-7], excludedCredentialIds: []), existingSources: [], maxSourceBytes: 65_536, ceremony: ceremony)
            let source = try required(await ceremony.savedSource())
            let assertion = try await NativeOperation().authenticate(input: NativeAssertionInput(rpId: "example.com", clientDataHash: Data(SHA256.hash(data: clientData("webauthn.get", getChallenge))), allowedCredentialIds: [registration.credentialId]), canonicalSource: source, maxSourceBytes: 65_536, ceremony: ceremony)
            let output: [String: String] = ["credentialId": base64url(registration.credentialId), "attestationObject": base64url(registration.attestationObject), "assertionCredentialId": base64url(assertion.credentialId), "authenticatorData": base64url(assertion.authenticatorData), "signature": base64url(assertion.signature), "userHandle": base64url(assertion.userHandle)]
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]))
        } catch { exit(66) }
    }
}
