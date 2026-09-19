import Foundation

@main struct NativeVaultStateCorpus {
    static let generation = "11111111-1111-4111-8111-111111111111"
    static func main() {
        do { try run(); print("PASS: native Vault state filesystem corpus") } catch { fputs("FAIL: \(error)\n", stderr); exit(1) }
    }
    static func run() throws {
        let root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("native-vault-state-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: root) }
        let before = try FileManager.default.contentsOfDirectory(atPath: root.path)
        do { _ = try ProviderStore(testRoot: root, mode: .existingOnly); throw Failure.expectedRefusal } catch Failure.expectedRefusal { throw Failure.expectedRefusal } catch { }
        guard try FileManager.default.contentsOfDirectory(atPath: root.path) == before else { throw Failure.changedReadOnlyRoot }
        let vault = root.appendingPathComponent("NativeVault")
        try FileManager.default.createSymbolicLink(atPath: vault.path, withDestinationPath: "/tmp")
        try expectCorrupt { _ = try ProviderStore(testRoot: root, mode: .explicitConnect) }
        try FileManager.default.removeItem(at: vault)
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o755])
        try expectUnavailable { _ = try ProviderStore(testRoot: root, mode: .existingOnly) }
        guard try permissions(of: vault) == 0o755 else { throw Failure.changedReadOnlyRoot }
        let store = try ProviderStore(testRoot: root, mode: .explicitConnect)
        guard try permissions(of: vault) == 0o700 else { throw Failure.legacyModeNotMigrated }
        try setPermissions(0o4755, of: vault)
        try expectUnavailable { _ = try ProviderStore(testRoot: root, mode: .explicitConnect) }
        guard try permissions(of: vault) == 0o4755 else { throw Failure.changedSpecialMode }
        try setPermissions(0o700, of: vault)
        let readOnly = try ProviderStore(testRoot: root, mode: .existingOnly)
        do { _ = try readOnly.read(); throw Failure.expectedRefusal } catch Failure.expectedRefusal { throw Failure.expectedRefusal } catch { }
        do { try readOnly.locked { _ in () }; throw Failure.expectedRefusal } catch Failure.expectedRefusal { throw Failure.expectedRefusal } catch { }
        guard try FileManager.default.contentsOfDirectory(atPath: vault.path).isEmpty else { throw Failure.changedReadOnlyRoot }
        let initialized = try store.initializeExplicitConnect(invalidatePrivate: {})
        let reopened = try ProviderStore(testRoot: root, mode: .explicitConnect)
        guard try reopened.read().generation == initialized.generation else { throw Failure.missingStateGenerationChanged }
        let lockURL = root.appendingPathComponent("NativeVault/state.lock")
        try FileManager.default.removeItem(at: lockURL)
        let providerAccess = try ProviderStore(testRoot: root, mode: .providerAccess)
        do {
            try providerAccess.locked { _ in () }
            throw Failure.providerAccessCreatedLock
        } catch Failure.providerAccessCreatedLock {
            throw Failure.providerAccessCreatedLock
        } catch let error as EnrollmentError {
            guard error.errorDescription == "Vault setup is unavailable. Try again." else { throw Failure.wrongErrorCategory }
        }
        guard !FileManager.default.fileExists(atPath: lockURL.path) else { throw Failure.changedReadOnlyRoot }
        try store.locked { _ in () }
        let state = PublicState(version: 2, generation: generation, host_subject: nil, provider_subject: nil)
        try store.write(state)
        guard try store.read().generation == generation else { throw Failure.badRoundTrip }
        let stateURL = root.appendingPathComponent("NativeVault/state.json")
        for corrupt in ["{", #"{"version":2,"generation":"\#(generation)","host_subject":null,"provider_subject":null,"unknown":true}"#, #"{"version":2,"generation":"\#(generation)","host_subject":null}"#] {
            try corrupt.data(using: .utf8)!.write(to: stateURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: stateURL.path)
            do { _ = try store.read(); throw Failure.expectedRefusal } catch Failure.expectedRefusal { throw Failure.expectedRefusal } catch { }
        }
        try FileManager.default.removeItem(at: stateURL)
        try FileManager.default.createSymbolicLink(atPath: stateURL.path, withDestinationPath: "/tmp")
        try expectCorrupt { _ = try store.read() }
        try FileManager.default.removeItem(at: stateURL)
        try FileManager.default.removeItem(at: lockURL)
        try FileManager.default.createSymbolicLink(atPath: lockURL.path, withDestinationPath: "/tmp")
        try expectCorrupt { try store.locked { _ in () } }
    }
    static func expectCorrupt(_ operation: () throws -> Void) throws {
        do { try operation(); throw Failure.expectedRefusal }
        catch Failure.expectedRefusal { throw Failure.expectedRefusal }
        catch let error as EnrollmentError {
            guard error.errorDescription == "Vault status is corrupt. Reconnect the provider." else { throw Failure.wrongErrorCategory }
        }
    }
    static func expectUnavailable(_ operation: () throws -> Void) throws {
        do { try operation(); throw Failure.expectedRefusal }
        catch Failure.expectedRefusal { throw Failure.expectedRefusal }
        catch let error as EnrollmentError {
            guard error.errorDescription == "Vault setup is unavailable. Try again." else { throw Failure.wrongErrorCategory }
        }
    }
    static func permissions(of url: URL) throws -> mode_t {
        var info = stat()
        guard lstat(url.path, &info) == 0 else { throw Failure.statFailed }
        return info.st_mode & 0o7777
    }
    static func setPermissions(_ permissions: mode_t, of url: URL) throws {
        guard chmod(url.path, permissions) == 0 else { throw Failure.chmodFailed }
    }
    enum Failure: Error { case expectedRefusal, changedReadOnlyRoot, providerAccessCreatedLock, badRoundTrip, missingStateGenerationChanged, wrongErrorCategory, legacyModeNotMigrated, changedSpecialMode, statFailed, chmodFailed }
}
