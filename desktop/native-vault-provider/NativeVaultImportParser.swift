import Foundation

/// This adapter is linked only into the signed native process. Neither the input
/// nor generated UniFFI source records are public host-command payloads. Foundation
/// and generated ABI copies are released normally; no complete wipe claim is made.
final class NativeVaultImportParser: NativeVaultImportParsing {
    private let operation = NativeFileImportOperation()
    func cancel() { operation.cancel() }

    func parseCXFv1(_ bytes: Data) throws -> NativeVaultImportInventory {
        guard !bytes.isEmpty, bytes.count <= NativeVaultImportFile.maximumBytes else {
            throw NativeVaultImportFile.Failure.limit
        }
        let inventory = try operation.parse(bytes: bytes)
        return NativeVaultImportInventory(
            total: Int(inventory.total),
            candidates: inventory.candidates.map {
                .init(title: $0.title, canonicalSource: $0.canonicalSource)
            },
            unsupported: inventory.unsupported.map {
                let reason: NativeVaultImportInventory.UnsupportedReason
                switch $0.reason {
                case .scoped: reason = .scoped
                case .mixedOrMultipleCredentials: reason = .mixed_or_multiple_credentials
                case .unsupportedCredential: reason = .unsupported_credential
                }
                return .init(index: Int($0.index), title: $0.title, reason: reason)
            }
        )
    }
}
