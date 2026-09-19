import CryptoKit
import Foundation

private enum Failure: Error { case assertion }
private func check(_ value: Bool) throws { if !value { throw Failure.assertion } }
private func reject(_ body: () throws -> Void) throws {
    do { try body() } catch is EnrollmentError { return }
    throw Failure.assertion
}
private func json(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) }

@main struct NativeVaultPasskeyCodecCorpus {
    static func main() throws {
        let id = "00000000-0000-4000-8000-000000000001"
        let source = Data(repeating: 255, count: 65536)
        let encoded = NativeVaultPasskeyCodec.base64url(source)
        let body = try json(["source": encoded])
        try check(body.count > 65536)
        try check(try NativeVaultPasskeyCodec.materialize(body, maxSourceBytes: 65536) == source)
        try reject { _ = try NativeVaultPasskeyCodec.materialize(body, maxSourceBytes: 65535) }
        try reject { _ = try NativeVaultPasskeyCodec.materialize(json(["source": ""]), maxSourceBytes: 65536) }
        try reject { _ = try NativeVaultPasskeyCodec.materialize(json(["source": "_w=="]), maxSourceBytes: 65536) }
        try reject { _ = try NativeVaultPasskeyCodec.materialize(json(["source": "/w"]), maxSourceBytes: 65536) }
        try reject { _ = try NativeVaultPasskeyCodec.materialize(Data("{\"source\":\"_w\",\"source\":\"_w\"}".utf8), maxSourceBytes: 65536) }
        try reject { _ = try StrictJSON(body) }
        try reject { _ = try StrictJSON(body, maxBytes: 98305) }
        var capabilities: [String: Any] = ["protocol_version": 1, "activation_revision": 1, "max_source_bytes": 65536, "max_credential_ids": 128, "max_request_body_bytes": 262144, "algorithms": [-7]]
        let cap = try NativeVaultPasskeyCodec.capabilities(json(capabilities))
        try check(cap.maxSourceBytes == 65536 && cap.maxCredentialIDs == 128 && cap.maxRequestBodyBytes == 262144)
        for (key, invalid) in [("activation_revision", true as Any), ("max_source_bytes", 65537 as Any), ("max_credential_ids", 0 as Any), ("max_request_body_bytes", 262145 as Any), ("algorithms", [-257] as Any)] {
            var bad = capabilities; bad[key] = invalid
            try reject { _ = try NativeVaultPasskeyCodec.capabilities(json(bad)) }
        }
        capabilities["extra"] = 1
        try reject { _ = try NativeVaultPasskeyCodec.capabilities(json(capabilities)) }
        let credential = Data(repeating: 255, count: 32)
        var row: [String: Any] = ["item_id": id, "passkey_id": id, "credential_id": NativeVaultPasskeyCodec.base64url(credential), "user_handle": "_w", "username": NSNull(), "display_name": ""]
        let matches = try NativeVaultPasskeyCodec.matches(json(["matches": [row], "truncated": false]))
        try check(matches.matches.count == 1 && matches.matches[0].credentialID == credential && matches.matches[0].username == nil && matches.matches[0].displayName == "")
        try reject { _ = try NativeVaultPasskeyCodec.matches(json(["matches": [row, row], "truncated": false])) }
        row["user_handle"] = ""
        try reject { _ = try NativeVaultPasskeyCodec.matches(json(["matches": [row], "truncated": false])) }
        var receipt: [String: Any] = ["mutation_id": id, "item_id": id, "field_id": id, "passkey_id": id, "status": "saved_waiting_for_site", "source_sha256": SHA256.hash(data: source).map { String(format: "%02x", $0) }.joined()]
        _ = try NativeVaultPasskeyCodec.receipt(json(receipt), mutationID: id, source: source)
        try reject { _ = try NativeVaultPasskeyCodec.receipt(json(receipt), mutationID: "00000000-0000-4000-8000-000000000002", source: source) }
        try reject { _ = try NativeVaultPasskeyCodec.receipt(json(receipt), mutationID: id, source: Data([0])) }
        receipt["item_id"] = "not-a-uuid"
        try reject { _ = try NativeVaultPasskeyCodec.receipt(json(receipt), mutationID: id, source: source) }
        print("PASS native passkey envelope corpus")
    }
}
