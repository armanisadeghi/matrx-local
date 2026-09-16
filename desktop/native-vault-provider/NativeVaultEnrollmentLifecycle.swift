import Foundation
import CryptoKit

extension Data {
    func nativeVaultURLSafeBase64() -> String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

private func lifecycleConstantTimeEqual(_ left: String, _ right: String) -> Bool {
    let lhs = Array(left.utf8), rhs = Array(right.utf8)
    var mismatch = lhs.count ^ rhs.count
    let width = max(lhs.count, rhs.count)
    for index in 0..<width { mismatch |= Int((index < lhs.count ? lhs[index] : 0) ^ (index < rhs.count ? rhs[index] : 0)) }
    return mismatch == 0
}

/// Main-actor controller state is represented by a unique operation.  A
/// callback can consume only the operation that created it; stale callbacks
/// cannot clear or complete a newer Connect attempt.
final class NativeVaultOperationCommitGuard {
    enum CancellationResult: Equatable { case cancelled, alreadyCommitted, alreadyCancelled }
    private enum State { case active, committed, cancelled }
    private let lock = NSLock()
    private var state: State = .active

    /// The caller holds the ProviderStore lock before entering this method.
    /// Holding this guard through the mutation makes Cancel linearize either
    /// before all persistent writes or after the complete commit.
    func commit(_ body: () throws -> Void) throws -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard state == .active else { return false }
        try body()
        state = .committed
        return true
    }
    func cancel() -> CancellationResult {
        lock.lock(); defer { lock.unlock() }
        switch state {
        case .active: state = .cancelled; return .cancelled
        case .committed: return .alreadyCommitted
        case .cancelled: return .alreadyCancelled
        }
    }
}

final class NativeVaultEnrollmentOperation {
    let id: UUID
    let generation: String
    let commitGuard = NativeVaultOperationCommitGuard()
    private var verifier: String?
    private var state: String?
    init(verifier: String, state: String, generation: String) {
        id = UUID(); self.verifier = verifier; self.state = state; self.generation = generation
    }
    func transaction() -> (verifier: String, state: String)? {
        guard let verifier, let state else { return nil }
        return (verifier, state)
    }
    func consumeTransaction() -> String? {
        defer { verifier = nil; state = nil }
        return verifier
    }
}

enum NativeVaultEnrollmentLifecycle {
    static func begin(verifier: String, state: String, generation: String, active: NativeVaultEnrollmentOperation?) -> NativeVaultEnrollmentOperation? {
        guard active == nil else { return nil }
        return NativeVaultEnrollmentOperation(verifier: verifier, state: state, generation: generation)
    }

    static func callbackCode(_ url: URL, for operation: NativeVaultEnrollmentOperation, callback: URL) throws -> (code: String, verifier: String) {
        guard url.scheme == callback.scheme, url.host == callback.host, url.path == callback.path,
              url.port == nil, url.fragment == nil, url.user == nil, url.password == nil else {
            throw EnrollmentError.message("The account callback was rejected. Start connection again.")
        }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        guard items.count == 2, Set(items.map(\.name)).count == 2,
              let returnedState = items.first(where: { $0.name == "state" })?.value,
              let transaction = operation.transaction(),
              lifecycleConstantTimeEqual(returnedState, transaction.state),
              let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
            throw EnrollmentError.message("The account callback was rejected. Start connection again.")
        }
        guard let verifier = operation.consumeTransaction() else {
            throw EnrollmentError.message("The account callback was rejected. Start connection again.")
        }
        return (code, verifier)
    }

    static func pkceChallenge(verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).nativeVaultURLSafeBase64()
    }

    static func canCommitRefresh(current: PublicState, expectedSubject: String, expectedGeneration: String, identity: Identity) -> Bool {
        current.generation == expectedGeneration && current.provider_subject == expectedSubject && identity.sub == expectedSubject
    }

    static func mayCompleteConfiguration(active: NativeVaultEnrollmentOperation?, operationID: UUID) -> Bool {
        active?.id == operationID
    }
}
