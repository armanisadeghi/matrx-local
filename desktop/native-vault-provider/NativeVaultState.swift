import Foundation
import Darwin

let groupID = "group.com.aimatrx.desktop.vault-status"

/// App Group state uses directory-relative syscalls only after Foundation has
/// resolved the entitlement-backed container. The provider never derives this
/// location from a home directory.
final class ProviderStore {
    enum Mode { case explicitConnect, existingOnly }
    private let directoryFD: Int32
    private let mode: Mode

    convenience init(mode: Mode = .explicitConnect) throws {
        guard let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: groupID) else { throw unavailable() }
        try self.init(root: root, mode: mode)
    }
    convenience init(testRoot root: URL, mode: Mode) throws { try self.init(root: root, mode: mode) }
    private init(root: URL, mode: Mode) throws {
        let rootFD = root.path.withCString { open($0, O_RDONLY | O_DIRECTORY | O_NOFOLLOW) }
        guard rootFD >= 0 else { throw filesystemFailure() }
        defer { close(rootFD) }
        var rootInfo = stat()
        guard fstat(rootFD, &rootInfo) == 0, rootInfo.st_uid == getuid(), (rootInfo.st_mode & S_IFMT) == S_IFDIR else { throw unavailable() }
        if mode == .explicitConnect, mkdirat(rootFD, "NativeVault", 0o700) != 0 && errno != EEXIST { throw filesystemFailure() }
        let fd = openat(rootFD, "NativeVault", O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        if fd < 0 && mode == .existingOnly && errno == ENOENT { throw unavailable() }
        guard fd >= 0 else { throw filesystemFailure() }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == getuid(), (info.st_mode & S_IFMT) == S_IFDIR, (info.st_mode & 0o777) == 0o700 else { close(fd); throw unavailable() }
        directoryFD = fd
        self.mode = mode
    }
    deinit { close(directoryFD) }

    func locked<T>(_ operation: (PublicState) throws -> T) throws -> T {
        if mode == .existingOnly { return try operation(read()) }
        let lock = openat(directoryFD, "state.lock", O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard lock >= 0 else { throw filesystemFailure() }
        defer { close(lock) }
        var info = stat()
        guard fstat(lock, &info) == 0, info.st_uid == getuid(), (info.st_mode & S_IFMT) == S_IFREG, (info.st_mode & 0o777) == 0o600 else { throw corrupt() }
        let deadline = Date().addingTimeInterval(2)
        while flock(lock, LOCK_EX | LOCK_NB) != 0 {
            if Date() >= deadline { throw EnrollmentError.message("Vault setup is busy. Try again.") }
            Thread.sleep(forTimeInterval: 0.05)
        }
        defer { flock(lock, LOCK_UN) }
        return try operation(try read())
    }

    /// Explicit Connect is the only state-creating read.  The caller supplies
    /// the already-authorized, provider-specific private-session invalidation;
    /// ordinary and existing-only reads can never invoke it or create state.
    func initializeExplicitConnect(invalidatePrivate: () throws -> Void) throws -> PublicState {
        guard mode == .explicitConnect else { throw unavailable() }
        return try locked { current in
            let existing = try readExisting()
            if let existing {
                // Reconnect first makes public status disconnected under the
                // generation lock, then clears the matching private session.
                let next = PublicState(version: 1, generation: UUID().canonical, host_subject: existing.host_subject, provider_subject: nil)
                try write(next)
                try invalidatePrivate()
                return next
            }
            // No state has been published yet: remove any stale provider item
            // before the first durable generation becomes observable.
            try invalidatePrivate()
            let initial = PublicState(version: 1, generation: current.generation, host_subject: nil, provider_subject: nil)
            try write(initial)
            return initial
        }
    }

    func read() throws -> PublicState {
        if let existing = try readExisting() { return existing }
        if mode == .existingOnly { throw unavailable() }
        return PublicState(version: 1, generation: UUID().canonical, host_subject: nil, provider_subject: nil)
    }

    private func readExisting() throws -> PublicState? {
        let fd = openat(directoryFD, "state.json", O_RDONLY | O_NOFOLLOW)
        if fd < 0 && errno == ENOENT { return nil }
        guard fd >= 0 else { throw filesystemFailure() }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == getuid(), (info.st_mode & S_IFMT) == S_IFREG, (info.st_mode & 0o777) == 0o600, info.st_size >= 0, info.st_size <= 2048 else { throw corrupt() }
        var data = Data(count: Int(info.st_size)); let readCount = data.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        guard readCount == data.count else { throw corrupt() }
        return try VaultEnvelopeCodec.publicState(data)
    }

    func write(_ value: PublicState) throws {
        let data = try StateJSON.encode(value)
        guard data.count <= 2048 else { throw corrupt() }
        let name = ".state-\(UUID().uuidString)"
        let fd = openat(directoryFD, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw unavailable() }
        var remove = true
        defer { close(fd); if remove { _ = unlinkat(directoryFD, name, 0) } }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == getuid(), (info.st_mode & S_IFMT) == S_IFREG, (info.st_mode & 0o777) == 0o600 else { throw corrupt() }
        let written = data.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        guard written == data.count, fsync(fd) == 0, renameat(directoryFD, name, directoryFD, "state.json") == 0, fsync(directoryFD) == 0 else { throw unavailable() }
        remove = false
    }

}

private enum StateJSON {
    static func encode(_ value: PublicState) throws -> Data {
        let body: [String: Any] = ["version": value.version, "generation": value.generation, "host_subject": value.host_subject ?? NSNull(), "provider_subject": value.provider_subject ?? NSNull()]
        return try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
    }
}

private func unavailable() -> Error { EnrollmentError.message("Vault setup is unavailable. Try again.") }
private func corrupt() -> Error { EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
private func filesystemFailure() -> Error { (errno == ELOOP || errno == ENOTDIR) ? corrupt() : unavailable() }
