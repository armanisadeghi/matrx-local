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

// This narrow decoder reads only the public COSE coordinates inside the
// registration's attestation object. It keeps the generated-Swift bridge test
// independent of the returned DER, so a substituted valid private key fails.
private struct CborCursor {
    private let bytes: [UInt8]
    private var index = 0
    init(_ data: Data) { bytes = Array(data) }

    private mutating func header() throws -> (UInt8, UInt64) {
        guard index < bytes.count else { throw Unexpected.callback }
        let first = bytes[index]; index += 1
        let major = first >> 5
        let additional = first & 0x1f
        let count: Int
        switch additional {
        case 0...23: return (major, UInt64(additional))
        case 24: count = 1
        case 25: count = 2
        case 26: count = 4
        case 27: count = 8
        default: throw Unexpected.callback
        }
        guard index + count <= bytes.count else { throw Unexpected.callback }
        var value: UInt64 = 0
        for _ in 0..<count { value = (value << 8) | UInt64(bytes[index]); index += 1 }
        return (major, value)
    }
    private mutating func byteString() throws -> Data {
        let (major, length) = try header()
        guard major == 2, length <= UInt64(bytes.count - index) else { throw Unexpected.callback }
        let end = index + Int(length)
        defer { index = end }
        return Data(bytes[index..<end])
    }
    private mutating func textString() throws -> String {
        let (major, length) = try header()
        guard major == 3, length <= UInt64(bytes.count - index) else { throw Unexpected.callback }
        let end = index + Int(length)
        defer { index = end }
        guard let value = String(bytes: bytes[index..<end], encoding: .utf8) else { throw Unexpected.callback }
        return value
    }
    private mutating func integer() throws -> Int64 {
        let (major, value) = try header()
        guard value <= UInt64(Int64.max) else { throw Unexpected.callback }
        switch major {
        case 0: return Int64(value)
        case 1: return -1 - Int64(value)
        default: throw Unexpected.callback
        }
    }
    private mutating func skip() throws {
        let (major, value) = try header()
        switch major {
        case 0, 1, 7: return
        case 2, 3: guard value <= UInt64(bytes.count - index) else { throw Unexpected.callback }; index += Int(value)
        case 4: for _ in 0..<value { try skip() }
        case 5: for _ in 0..<(value * 2) { try skip() }
        case 6: try skip()
        default: throw Unexpected.callback
        }
    }
    mutating func attestedP256PublicKey() throws -> P256.Signing.PublicKey {
        let (major, entries) = try header()
        guard major == 5 else { throw Unexpected.callback }
        var authData: Data?
        for _ in 0..<entries {
            let key = try textString()
            if key == "authData" { authData = try byteString() } else { try skip() }
        }
        guard let authData, authData.count >= 55, authData[32] & 0x40 != 0 else { throw Unexpected.callback }
        let credentialLength = Int(authData[53]) << 8 | Int(authData[54])
        let coseStart = 55 + credentialLength
        guard coseStart < authData.count else { throw Unexpected.callback }
        var cose = CborCursor(authData.suffix(from: coseStart))
        let (coseMajor, coseEntries) = try cose.header()
        guard coseMajor == 5 else { throw Unexpected.callback }
        var x: Data?
        var y: Data?
        for _ in 0..<coseEntries {
            let label = try cose.integer()
            switch label {
            case -2: x = try cose.byteString()
            case -3: y = try cose.byteString()
            default: try cose.skip()
            }
        }
        guard let x, let y, x.count == 32, y.count == 32 else { throw Unexpected.callback }
        return try P256.Signing.PublicKey(x963Representation: Data([0x04]) + x + y)
    }
}
private enum CallbackMode { case accept, denied, failed, unexpected, refusedPersistence, failedPersistence, unexpectedPersistence }
private enum Unexpected: Error, CustomStringConvertible {
    case callback
    var description: String { "NATIVE_TEST_PRIVATE_CALLBACK_SENTINEL" }
}

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
        case .unexpected: throw Unexpected.callback
        default: return
        }
    }
    func persistRegistration(canonicalSource: Data) async throws {
        switch persistence {
        case .refusedPersistence: throw PersistenceCallbackError.Refused
        case .failedPersistence: throw PersistenceCallbackError.Failed
        case .unexpectedPersistence: throw Unexpected.callback
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

        // This calls generated Swift bindings over the private source-v1
        // bridge. It proves exact public metadata and that a signature made
        // with returned DER verifies against the original attested public key.
        let exported = try nativeExportSourcePkcs8(source: nilSource, maxSourceBytes: 65_536)
        guard exported.rpId == "example.com",
              exported.credentialId == nilRegistration.credentialId,
              exported.userHandle == Data("swift-rp-user".utf8),
              exported.username == "swift-rp-user",
              exported.displayName == nil else { throw Unexpected.callback }
        let exportKey = try P256.Signing.PrivateKey(derRepresentation: exported.pkcs8Der)
        let exportPayload = Data("native-source-v1-swift-export".utf8)
        let exportSignature = try exportKey.signature(for: exportPayload)
        var attestation = CborCursor(nilRegistration.attestationObject)
        let attestedPublicKey = try attestation.attestedP256PublicKey()
        guard attestedPublicKey.isValidSignature(exportSignature, for: exportPayload) else { throw Unexpected.callback }

        let emptyCeremony = Ceremony()
        let emptyRegistration = try await NativeOperation().register(input: registrationInput(displayName: ""), existingSources: [], maxSourceBytes: 65_536, ceremony: emptyCeremony)
        let emptySource = try required(await emptyCeremony.savedSource())
        let emptyState = await emptyCeremony.displayStates()
        guard !emptyState.0 && emptyState.1 else { throw Unexpected.callback }
        let emptyExport = try nativeExportSourcePkcs8(source: emptySource, maxSourceBytes: 65_536)
        guard emptyExport.displayName == "", emptyExport.credentialId == emptyRegistration.credentialId else { throw Unexpected.callback }

        // Generated Swift must preserve the bridge's fixed source refusal
        // behavior independently of the Rust-only export tests.
        try await expect(.InvalidSource) { _ = try nativeExportSourcePkcs8(source: Data(), maxSourceBytes: 65_536); return }
        try await expect(.InvalidSource) { _ = try nativeExportSourcePkcs8(source: Data("malformed".utf8), maxSourceBytes: 65_536); return }
        try await expect(.InvalidSource) { _ = try nativeExportSourcePkcs8(source: nilSource, maxSourceBytes: 0); return }
        try await expect(.InvalidSource) { _ = try nativeExportSourcePkcs8(source: nilSource, maxSourceBytes: 65_537); return }
        try await expect(.InvalidSource) { _ = try nativeExportSourcePkcs8(source: nilSource, maxSourceBytes: UInt32(nilSource.count - 1)); return }

        // Invalid requests must refuse before touching the verification callback.
        try await expect(.InvalidRequest) { _ = try await NativeOperation().register(input: registrationInput(rpId: "INVALID/RP"), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .denied)); return }
        try await expect(.InvalidRequest) { _ = try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId, rpId: "INVALID/RP"), canonicalSource: nilSource, maxSourceBytes: 65_536, ceremony: Ceremony(verification: .denied)); return }

        // Actual generated callbacks cover declared denied/failed/unexpected outcomes.
        try await expect(.VerificationDenied) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .denied)); return }
        try await expect(.OperationFailed) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .failed)); return }
        try await expect(.OperationFailed) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(verification: .unexpected)); return }
        try await expect(.PersistenceFailed) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .refusedPersistence)); return }
        try await expect(.OperationFailed) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .failedPersistence)); return }
        try await expect(.OperationFailed) { _ = try await NativeOperation().register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony(persistence: .unexpectedPersistence)); return }

        let before = NativeOperation(); before.cancel()
        try await expect(.Cancelled) { _ = try await before.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }
        let after = NativeOperation()
        _ = try await after.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony())
        after.cancel()
        try await expect(.AlreadyUsed) { _ = try await after.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }

        // Deterministic callback gates prove both cancel/completion race winners.
        let cancellationGate = GateCeremony(); let cancellationOperation = NativeOperation()
        let cancellationTask = Task { try await cancellationOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: cancellationGate) }
        await cancellationGate.waitUntilStarted(); cancellationOperation.cancel(); await cancellationGate.release()
        do { _ = try await cancellationTask.value; throw Unexpected.callback } catch let error as BridgeError where error == .Cancelled {}
        let completionGate = GateCeremony(); let completionOperation = NativeOperation()
        let completionTask = Task { try await completionOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: completionGate) }
        await completionGate.waitUntilStarted(); await completionGate.release(); _ = try await completionTask.value
        completionOperation.cancel()
        try await expect(.AlreadyUsed) { _ = try await completionOperation.register(input: registrationInput(), existingSources: [], maxSourceBytes: 65_536, ceremony: Ceremony()); return }

        // The source stays in harness memory while actual bridge checks reject
        // lower limits, wrong RP, and a mismatched allow-list.
        try await expect(.InvalidSource) { _ = try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId), canonicalSource: nilSource, maxSourceBytes: 1, ceremony: Ceremony()); return }
        try await expect(.NoCredentials) { _ = try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId, rpId: "wrong.example"), canonicalSource: nilSource, maxSourceBytes: 65_536, ceremony: Ceremony()); return }
        try await expect(.NoCredentials) { _ = try await NativeOperation().authenticate(input: assertionInput(nilRegistration.credentialId, allowed: [Data(repeating: 1, count: 16)]), canonicalSource: nilSource, maxSourceBytes: 65_536, ceremony: Ceremony()); return }
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
