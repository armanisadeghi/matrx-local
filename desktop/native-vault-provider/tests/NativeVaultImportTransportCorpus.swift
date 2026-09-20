import Foundation
import CryptoKit

private final class Wire: NativeVaultPasskeyTransporting {
    var requests: [URLRequest] = []
    var mutation = UUID()
    var source = Data("private-test-source".utf8)
    var status = 200
    var wrongMutation = false
    var wrongDigest = false
    var afterSend: (() -> Void)?
    func cancel() {}
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        requests.append(request)
        let result: [String: Any] = ["mutation_id": (wrongMutation ? UUID() : mutation).uuidString.lowercased(),
            "item_id": UUID().uuidString.lowercased(), "field_id": UUID().uuidString.lowercased(),
            "passkey_id": UUID().uuidString.lowercased(), "status": "saved_waiting_for_site",
            "source_sha256": NativeVaultPasskeyCodec.base64url(Data(SHA256.hash(data: wrongDigest ? Data() : source)))]
        afterSend?()
        completion(.success((try! JSONSerialization.data(withJSONObject: result), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)))
    }
}

@main struct NativeVaultImportTransportCorpus {
    static func rejects(_ operation: () async throws -> Void) async {
        do { try await operation(); preconditionFailure("unsafe operation accepted") } catch {}
    }
    static func main() async throws {
        let scope = UUID(); let server = Wire(); var current = true
        let bridge = NativeVaultImportTransport(grant: .init(accessToken: "test-native-bearer", subject: UUID().uuidString.lowercased(), generation: UUID().uuidString.lowercased()), lifetime: NativeVaultRequestLifetime(), sessionAccess: NativeVaultSessionAccess(), principalByScope: [scope: "user"], transport: server, current: { current })
        let result = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope)
        precondition(result.mutationID == server.mutation)
        let sent = server.requests.last!
        precondition(sent.url!.path == "/api/vault/native/passkeys/import")
        precondition(sent.value(forHTTPHeaderField: "X-Organization-Id") == scope.uuidString.lowercased())
        precondition(sent.value(forHTTPHeaderField: "Authorization") == "Bearer test-native-bearer")
        let body = try JSONSerialization.jsonObject(with: sent.httpBody!) as! [String: String]
        precondition(Set(body.keys) == Set(["mutation_id", "source", "label", "principal_type", "format_version"]))
        precondition(body["principal_type"] == "user" && body["format_version"] == "cxf1.0")
        server.wrongMutation = true
        await rejects { _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope) }
        await rejects { _ = try await bridge.receipt(mutationID: server.mutation, organizationID: scope) }
        server.wrongMutation = false; server.wrongDigest = true
        await rejects { _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope) }
        server.wrongDigest = false
        server.status = 409
        do {
            _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope)
            preconditionFailure("validated conflict admitted")
        } catch let failure as NativeVaultImportTransportFailure {
            precondition(failure == .refused(reason: .import_unavailable), "conflict must be a closed refusal")
        }
        server.status = 500
        do {
            _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope)
            preconditionFailure("ambiguous server error admitted")
        } catch let failure as NativeVaultImportTransportFailure {
            precondition(failure == .uncertain, "5xx must remain uncertain")
        }
        server.status = 200
        let before = server.requests.count
        await rejects { _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: UUID()) }
        precondition(server.requests.count == before)
        server.afterSend = { current = false }
        await rejects { _ = try await bridge.receipt(mutationID: server.mutation, organizationID: scope) }
        server.afterSend = nil; current = true
        bridge.cancel()
        let cancelledCount = server.requests.count
        do {
            _ = try await bridge.importPasskey(mutationID: server.mutation, source: server.source, label: "Imported passkey", organizationID: scope)
            preconditionFailure("cancelled write admitted")
        } catch let failure as NativeVaultImportTransportFailure {
            precondition(failure == .cancelled, "prevented write must be cancelled, not refused")
        }
        precondition(server.requests.count == cancelledCount)
        let recovered = try await bridge.receipt(mutationID: server.mutation, organizationID: scope)
        precondition(recovered?.mutationID == server.mutation)
        server.status = 404
        let missing = try await bridge.receipt(mutationID: server.mutation, organizationID: scope)
        precondition(missing == nil)
        print("Native import transport corpus passed")
    }
}
