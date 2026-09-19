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
}
