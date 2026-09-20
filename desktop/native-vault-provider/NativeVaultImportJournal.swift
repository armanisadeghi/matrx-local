import Foundation
import CryptoKit

/// Durable recovery metadata deliberately excludes paths, source bytes, tokens,
/// key material, and server result identifiers. The importer owns the directory.
struct NativeVaultImportJournal: Codable, Equatable {
    struct Entry: Codable, Equatable {
        enum State: String, Codable { case pending, started, committed, unsupported, failed, uncertain, not_attempted }
        let slotID: UUID
        let mutationID: UUID?
        var state: State
        /// Fixed public accounting reason only; never a source label or detail.
        let reason: String?
    }
    let operationID: UUID
    let subject: String
    let generation: String
    let organizationID: UUID
    let previewDigest: String
    var entries: [Entry]

    /// Keep accounts' unresolved receipt ledgers separate without placing a
    /// subject or generation in a filename. This also makes a corrupt old
    /// account ledger unable to block the replacement account.
    static func url(base: URL, subject: String, generation: String) -> URL {
        let binding = Data("\(subject)\u{0}\(generation)".utf8)
        let suffix = SHA256.hash(data: binding).prefix(16).map { String(format: "%02x", $0) }.joined()
        return base.deletingPathExtension().appendingPathExtension("\(suffix).json")
    }

    static func load(from url: URL) throws -> NativeVaultImportJournal? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
    }

    func save(to url: URL) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(self)
        let temporary = directory.appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString)")
        try data.write(to: temporary, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        if FileManager.default.fileExists(atPath: url.path) {
            _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary, backupItemName: nil, options: [])
        } else {
            try FileManager.default.moveItem(at: temporary, to: url)
        }
    }
}
