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
    // The Python RP verifier receives these exact bytes and checks the hash
    // embedded in the real Rust-issued authenticator response.
    Data("{\"type\":\"\(type)\",\"challenge\":\"\(base64url(challenge))\",\"origin\":\"https://example.com\",\"crossOrigin\":false}".utf8)
}

private actor Ceremony: NativeCeremony {
    private var source: Data?
    func verifyUser() async throws {}
    func persistRegistration(canonicalSource: Data) async throws { source = canonicalSource }
    func savedSource() -> Data? { source }
}

@main
struct NativeVaultBridgeFido2 {
    static func main() async {
        guard CommandLine.arguments.count == 3,
              let createChallenge = decode(CommandLine.arguments[1]),
              let getChallenge = decode(CommandLine.arguments[2]) else { exit(64) }
        let ceremony = Ceremony()
        do {
            let createData = clientData("webauthn.create", createChallenge)
            let registration = try await NativeOperation().register(
                input: NativeRegistrationInput(rpId: "example.com", userHandle: Data("swift-rp-user".utf8), username: "swift-rp-user", displayName: nil, clientDataHash: Data(SHA256.hash(data: createData)), supportedAlgorithms: [-7], excludedCredentialIds: []),
                existingSources: [], maxSourceBytes: 65_536, ceremony: ceremony)
            guard let source = await ceremony.savedSource() else { exit(65) }
            let assertion = try await NativeOperation().authenticate(
                input: NativeAssertionInput(rpId: "example.com", clientDataHash: Data(SHA256.hash(data: clientData("webauthn.get", getChallenge))), allowedCredentialIds: [registration.credentialId]),
                canonicalSource: source, maxSourceBytes: 65_536, ceremony: ceremony)
            let output: [String: String] = [
                "credentialId": base64url(registration.credentialId),
                "attestationObject": base64url(registration.attestationObject),
                "assertionCredentialId": base64url(assertion.credentialId),
                "authenticatorData": base64url(assertion.authenticatorData),
                "signature": base64url(assertion.signature),
                "userHandle": base64url(assertion.userHandle),
            ]
            let encoded = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
            FileHandle.standardOutput.write(encoded)
        } catch { exit(66) }
    }
}
