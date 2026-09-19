import CryptoKit
import Foundation

struct NativePasskeyCapabilities {
    let revision: Int
    let maxSourceBytes: Int
    let maxCredentialIDs: Int
    let maxRequestBodyBytes: Int
}
struct NativePasskeyMatch {
    let itemID: String
    let passkeyID: String
    let credentialID: Data
    let userHandle: Data
    let username: String?
    let displayName: String?
}
struct NativePasskeyReceipt {
    let itemID: String
    let fieldID: String
    let passkeyID: String
}

/// Decodes only the protected endpoint's closed envelopes. Source validation
/// and signing belong to the native core; these values never enter host IPC.
enum NativeVaultPasskeyCodec {
    static let maxEnvelopeBytes = 96 * 1024
    private static func rejected() -> Error { EnrollmentError.message("Passkey response was rejected. Try again.") }
    static func base64url(_ bytes: Data) -> String {
        bytes.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    static func bytes(_ value: JSONValue?, minimum: Int, maximum: Int) throws -> Data {
        guard case let .string(encoded)? = value, encoded.utf8.count <= (maximum * 4 + 2) / 3 else { throw rejected() }
        let padded = encoded.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            + String(repeating: "=", count: (4 - encoded.utf8.count % 4) % 4)
        guard let decoded = Data(base64Encoded: padded), (minimum...maximum).contains(decoded.count), base64url(decoded) == encoded else { throw rejected() }
        return decoded
    }
    private static func integer(_ value: JSONValue?, range: ClosedRange<Int>) throws -> Int {
        guard case let .number(raw)? = value, let number = Int(raw), String(number) == raw, range.contains(number) else { throw rejected() }
        return number
    }
    private static func uuid(_ value: JSONValue?) throws -> String {
        guard case let .string(id)? = value, id.canonicalUUID else { throw rejected() }; return id
    }
    private static func name(_ value: JSONValue?) throws -> String? {
        switch value {
        case .null?: return nil
        case let .string(text)? where text.utf8.count <= 1024: return text
        default: throw rejected()
        }
    }
    static func capabilities(_ data: Data) throws -> NativePasskeyCapabilities {
        let object = try StrictEnvelope.object(data, required: ["protocol_version", "activation_revision", "max_source_bytes", "max_credential_ids", "max_request_body_bytes", "algorithms"], optional: [])
        guard case .number("1")? = object["protocol_version"], case let .array(algorithms)? = object["algorithms"], algorithms.count == 1, case .number("-7") = algorithms[0] else { throw rejected() }
        return try NativePasskeyCapabilities(revision: integer(object["activation_revision"], range: 1...Int.max), maxSourceBytes: integer(object["max_source_bytes"], range: 1...65536), maxCredentialIDs: integer(object["max_credential_ids"], range: 1...128), maxRequestBodyBytes: integer(object["max_request_body_bytes"], range: 1...262144))
    }
    static func matches(_ data: Data) throws -> (matches: [NativePasskeyMatch], truncated: Bool) {
        let object = try StrictEnvelope.object(data, required: ["matches", "truncated"], optional: [])
        guard case let .array(rows)? = object["matches"], rows.count <= 200, case let .bool(truncated)? = object["truncated"] else { throw rejected() }
        var result: [NativePasskeyMatch] = []
        var items = Set<String>(); var passkeys = Set<String>(); var credentials = Set<Data>()
        for row in rows {
            guard case let .object(value) = row, Set(value.keys) == Set(["item_id", "passkey_id", "credential_id", "user_handle", "username", "display_name"]) else { throw rejected() }
            let item = try uuid(value["item_id"]); let passkey = try uuid(value["passkey_id"])
            let credential = try bytes(value["credential_id"], minimum: 16, maximum: 1023)
            guard items.insert(item).inserted, passkeys.insert(passkey).inserted, credentials.insert(credential).inserted else { throw rejected() }
            result.append(try NativePasskeyMatch(itemID: item, passkeyID: passkey, credentialID: credential, userHandle: bytes(value["user_handle"], minimum: 1, maximum: 64), username: name(value["username"]), displayName: name(value["display_name"])))
        }
        return (result, truncated)
    }
    static func materialize(_ data: Data, maxSourceBytes: Int) throws -> Data {
        guard (1...65536).contains(maxSourceBytes) else { throw rejected() }
        let object = try StrictEnvelope.object(data, required: ["source"], optional: [], maxBytes: maxEnvelopeBytes)
        return try bytes(object["source"], minimum: 1, maximum: maxSourceBytes)
    }
    static func receipt(_ data: Data, mutationID: String, source: Data) throws -> NativePasskeyReceipt {
        let value = try StrictEnvelope.object(data, required: ["mutation_id", "item_id", "field_id", "passkey_id", "source_sha256", "status"], optional: [])
        guard mutationID.canonicalUUID, try uuid(value["mutation_id"]) == mutationID,
              case .string("saved_waiting_for_site")? = value["status"],
              case let .string(digest)? = value["source_sha256"],
              digest == base64url(Data(SHA256.hash(data: source))) else { throw rejected() }
        return try NativePasskeyReceipt(itemID: uuid(value["item_id"]), fieldID: uuid(value["field_id"]), passkeyID: uuid(value["passkey_id"]))
    }
}
