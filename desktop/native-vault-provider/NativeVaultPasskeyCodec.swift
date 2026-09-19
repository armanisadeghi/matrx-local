import Foundation
import CryptoKit

/// Closed, source-redacted passkey wire envelopes. The controller owns ceremony APIs.
enum NativeVaultPasskeyCodec {
    static let maxSourceBytes = 96 * 1024
    static func source(_ data: Data) throws -> Data {
        _ = try StrictEnvelope.object(data, required: ["source"], optional: [], maxBytes: maxSourceBytes)
        return data
    }
    static func receipt(_ data: Data, mutationID: String, source: Data) throws {
        let value = try StrictEnvelope.object(data, required: ["mutation_id", "item_id", "field_id", "passkey_id", "source_sha256", "status"], optional: [])
        guard case let .string(id)? = value["mutation_id"], id == mutationID,
              case let .string(status)? = value["status"], status == "saved_waiting_for_site",
              case let .string(digest)? = value["source_sha256"],
              digest == Data(SHA256.hash(data: source)).map { String(format: "%02x", $0) }.joined() else { throw EnrollmentError.message("Passkey response was rejected. Try again.") }
    }
    static func materialize(_ data: Data) throws -> Data {
        let value = try StrictEnvelope.object(data, required: ["source"], optional: [], maxBytes: maxSourceBytes)
        guard case let .string(encoded)? = value["source"], let decoded = Data(base64Encoded: encoded + String(repeating: "=", count: (4 - encoded.count % 4) % 4)),
              Data(base64Encoded: encoded + String(repeating: "=", count: (4 - encoded.count % 4) % 4)) != nil,
              decoded.count <= maxSourceBytes,
              decoded.base64EncodedString(options: [.endLineWithLineFeed]).replacingOccurrences(of: "\n", with: "").replacingOccurrences(of: "=", with: "") == encoded else { throw EnrollmentError.message("Passkey response was rejected. Try again.") }
        return decoded
    }
    static func capabilities(_ data: Data) throws {
        let value = try StrictEnvelope.object(data, required: ["protocol_version", "activation_revision", "max_source_bytes", "max_credential_ids", "algorithms"], optional: [])
        guard case .number("1")? = value["protocol_version"], case let .number(revision)? = value["activation_revision"], Int(revision).map({ $0 >= 1 }) == true,
              case let .number(bytes)? = value["max_source_bytes"], Int(bytes).map({ $0 > 0 && $0 <= maxSourceBytes }) == true else { throw EnrollmentError.message("Passkey response was rejected. Try again.") }
    }
}
