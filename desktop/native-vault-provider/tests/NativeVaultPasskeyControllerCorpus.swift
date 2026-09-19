import AuthenticationServices
import CryptoKit
import Foundation

private final class Transport: NativeVaultPasskeyTransporting {
    var requests: [URLRequest] = []
    var cancellations = 0
    var failCreate = false
    func cancel() { cancellations += 1 }
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        requests.append(request)
        let path = request.url!.path
        let object: [String: Any]
        if path == "/api/auth/organizations" { object = ["authenticated": true, "user_id": "subject", "organizations": [["id": "00000000-0000-4000-8000-000000000001", "name": "Personal", "is_personal": true, "abbreviation": NSNull()]], "default_organization_id": NSNull(), "default_preference_status": "unset", "warnings": [], "missing_organization_count": 0] }
        else if path.hasSuffix("/capabilities") { object = ["protocol_version": 1, "activation_revision": 1, "max_source_bytes": 65536, "max_credential_ids": 128, "max_request_body_bytes": 131072, "algorithms": [-7]] }
        else if path.hasSuffix("/matches") { object = ["matches": [], "truncated": false] }
        else if path.hasSuffix("/passkeys") {
            if failCreate { completion(.failure(URLError(.timedOut))); return }
            let requestObject = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
            let encoded = (requestObject["source"] as! String).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            let source = Data(base64Encoded: encoded + String(repeating: "=", count: (4 - encoded.count % 4) % 4))!
            let digest = Data(SHA256.hash(data: source)).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
            object = ["mutation_id": requestObject["mutation_id"]!, "item_id": "00000000-0000-4000-8000-000000000002", "field_id": "00000000-0000-4000-8000-000000000003", "passkey_id": "00000000-0000-4000-8000-000000000004", "source_sha256": digest, "status": "saved_waiting_for_site"]
        } else { object = ["detail": ["code": "native_unavailable"]] }
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        completion(.success((try! JSONSerialization.data(withJSONObject: object), response)))
    }
}

@main struct NativeVaultPasskeyControllerCorpus {
    static func wait(_ condition: @escaping () -> Bool) async { for _ in 0..<1000 { if condition() { return }; try? await Task.sleep(nanoseconds: 10_000_000) }; fatalError("timed out") }
    static func request() -> ASPasskeyCredentialRequest {
        let identity = ASPasskeyCredentialIdentity(relyingPartyIdentifier: "example.com", userName: "user", credentialID: Data(repeating: 7, count: 16), userHandle: Data("handle".utf8), recordIdentifier: nil)
        return ASPasskeyCredentialRequest(credentialIdentity: identity, clientDataHash: Data(repeating: 9, count: 32), userVerificationPreference: .preferred, supportedAlgorithms: [ASCOSEAlgorithmIdentifier(rawValue: -7)])
    }
    static func malformedRequest() -> ASPasskeyCredentialRequest {
        let identity = ASPasskeyCredentialIdentity(relyingPartyIdentifier: "", userName: "user", credentialID: Data(repeating: 7, count: 16), userHandle: Data("handle".utf8), recordIdentifier: nil)
        return ASPasskeyCredentialRequest(credentialIdentity: identity, clientDataHash: Data(repeating: 9, count: 32), userVerificationPreference: .preferred, supportedAlgorithms: [ASCOSEAlgorithmIdentifier(rawValue: -7)])
    }
    static func main() async {
        let transport = Transport(); var completed = false; var cancelled = false
        let grant = NativeVaultSessionAccess.Grant(accessToken: "token", subject: "subject", generation: "generation")
        var evaluatedContexts: [ObjectIdentifier] = []
        let coordinator = await MainActor.run { NativeVaultPasskeyCoordinator(sessionAccess: NativeVaultSessionAccess(), transport: transport, key: { "key" }, cancel: { _ in cancelled = true }, completeRegistration: { _ in completed = true }, completeAssertion: { _ in }, acquire: { $0(.success(grant)) }, currentState: { NativePasswordCurrentState(generation: "generation", subject: "subject") }, completionLock: { try $0(NativePasswordCurrentState(generation: "generation", subject: "subject")) }, organizationChoice: { _ in 0 }, matchChoice: { _ in 0 }, label: { $0 }, evaluator: { context, _, completion in evaluatedContexts.append(ObjectIdentifier(context)); completion(true) }) }
        let controller = await MainActor.run { CredentialProviderViewController() }
        await MainActor.run { controller.nativePasskeyCoordinator = coordinator; controller.prepareInterface(forPasskeyRegistration: request()) }
        await wait { completed || cancelled }; precondition(completed, "registration cancelled after \(transport.requests.map { $0.url!.path })"); precondition(!cancelled); precondition(evaluatedContexts.count == 2 && evaluatedContexts[0] == evaluatedContexts[1]); precondition(transport.requests.contains { $0.url!.path.hasSuffix("/passkeys") })
        cancelled = false
        let hanging = await MainActor.run { NativeVaultPasskeyCoordinator(sessionAccess: NativeVaultSessionAccess(), transport: transport, key: { "key" }, cancel: { _ in cancelled = true }, completeRegistration: { _ in }, completeAssertion: { _ in }, acquire: { callback in DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) { callback(.success(grant)) } }, operationTimeout: 0.02) }
        await MainActor.run { controller.nativePasskeyCoordinator = hanging; controller.prepareInterface(forPasskeyRegistration: request()) }; await wait { cancelled }
        precondition(transport.cancellations > 0, "timeout did not cancel the active transport")

        var denied = false; var acquired = 0
        let denial = await MainActor.run { NativeVaultPasskeyCoordinator(sessionAccess: NativeVaultSessionAccess(), transport: transport, key: { "key" }, cancel: { _ in denied = true }, completeRegistration: { _ in preconditionFailure("denied request completed") }, completeAssertion: { _ in }, acquire: { callback in acquired += 1; callback(.success(grant)) }, currentState: { NativePasswordCurrentState(generation: "generation", subject: "subject") }, completionLock: { try $0(NativePasswordCurrentState(generation: "generation", subject: "subject")) }, organizationChoice: { _ in 0 }, evaluator: { _, _, completion in completion(false) }) }
        await MainActor.run { controller.nativePasskeyCoordinator = denial; controller.prepareInterface(forPasskeyRegistration: request()) }
        await wait { denied }; precondition(acquired == 0, "denied local authentication reached session acquisition")

        var malformedCancelled = false; var malformedAcquire = 0
        let malformed = await MainActor.run { NativeVaultPasskeyCoordinator(sessionAccess: NativeVaultSessionAccess(), transport: transport, key: { "key" }, cancel: { _ in malformedCancelled = true }, completeRegistration: { _ in preconditionFailure("malformed request completed") }, completeAssertion: { _ in }, acquire: { callback in malformedAcquire += 1; callback(.success(grant)) }) }
        await MainActor.run { controller.nativePasskeyCoordinator = malformed; controller.prepareInterface(forPasskeyRegistration: malformedRequest()) }
        await wait { malformedCancelled }; precondition(malformedAcquire == 0, "malformed request reached local authentication")
        print("PASS native passkey actual callback coordinator corpus")
    }
}
