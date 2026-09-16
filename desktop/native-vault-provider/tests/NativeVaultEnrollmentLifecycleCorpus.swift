import Foundation

@main
struct NativeVaultEnrollmentLifecycleCorpus {
    static let callback = URL(string: "matrx-vault-provider://oauth/callback")!
    static let subject = "11111111-1111-4111-8111-111111111111"
    static let generation = "22222222-2222-4222-8222-222222222222"
    static let otherGeneration = "33333333-3333-4333-833333333333"

    static func main() throws {
        try callbackAndPKCEAreStrict()
        try doubleConnectCannotReplaceCallbackOwner()
        try generationChangeCancelsRefreshCommit()
        try cancelBeforePersistenceLeavesNoWrites()
        try currentConnectionAdmissionReturnsToMainActor()
        try configurationCompletionRequiresCurrentOperation()
        print("native Vault enrollment lifecycle corpus passed")
    }

    static func operation() throws -> NativeVaultEnrollmentOperation {
        guard let value = NativeVaultEnrollmentLifecycle.begin(verifier: "verifier-abcdefghijklmnopqrstuvwxyz012345", state: "state-abcdefghijklmnopqrstuvwxyz012345", generation: generation, active: nil) else { throw Failure.bad }
        return value
    }
    static func callbackAndPKCEAreStrict() throws {
        let active = try operation()
        guard NativeVaultEnrollmentLifecycle.pkceChallenge(verifier: "verifier-abcdefghijklmnopqrstuvwxyz012345") == "CGk8HVymQ7dkbDaBljFdKHsCtcHrJRy5N3qiD7z7_LI" else { throw Failure.bad }
        let valid = URL(string: "matrx-vault-provider://oauth/callback?state=state-abcdefghijklmnopqrstuvwxyz012345&code=one")!
        guard try NativeVaultEnrollmentLifecycle.callbackCode(valid, for: active, callback: callback).code == "one" else { throw Failure.bad }
        for invalid in [
            URL(string: "matrx-vault-provider://oauth/callback?state=wrong&code=one")!,
            URL(string: "matrx-vault-provider://oauth/callback?state=state-abcdefghijklmnopqrstuvwxyz012345&code=one&code=two")!,
            URL(string: "matrx-vault-provider://other?state=state-abcdefghijklmnopqrstuvwxyz012345&code=one")!,
        ] { try rejects { _ = try NativeVaultEnrollmentLifecycle.callbackCode(invalid, for: active, callback: callback) } }
    }
    static func doubleConnectCannotReplaceCallbackOwner() throws {
        let first = try operation()
        guard NativeVaultEnrollmentLifecycle.begin(verifier: "second", state: "second", generation: generation, active: first) == nil else { throw Failure.bad }
        let firstCallback = URL(string: "matrx-vault-provider://oauth/callback?state=state-abcdefghijklmnopqrstuvwxyz012345&code=first")!
        guard try NativeVaultEnrollmentLifecycle.callbackCode(firstCallback, for: first, callback: callback).code == "first" else { throw Failure.bad }
    }
    static func generationChangeCancelsRefreshCommit() throws {
        let identity = Identity(sub: subject, email: nil, email_verified: nil)
        let current = PublicState(version: 1, generation: generation, host_subject: nil, provider_subject: subject)
        guard NativeVaultEnrollmentLifecycle.canCommitRefresh(current: current, expectedSubject: subject, expectedGeneration: generation, identity: identity) else { throw Failure.bad }
        let changed = PublicState(version: 1, generation: otherGeneration, host_subject: nil, provider_subject: subject)
        guard !NativeVaultEnrollmentLifecycle.canCommitRefresh(current: changed, expectedSubject: subject, expectedGeneration: generation, identity: identity) else { throw Failure.bad }
    }
    static func configurationCompletionRequiresCurrentOperation() throws {
        let active = try operation()
        guard NativeVaultEnrollmentLifecycle.mayCompleteConfiguration(active: active, operationID: active.id), !NativeVaultEnrollmentLifecycle.mayCompleteConfiguration(active: nil, operationID: active.id), !NativeVaultEnrollmentLifecycle.mayCompleteConfiguration(active: active, operationID: UUID()) else { throw Failure.bad }
    }
    static func cancelBeforePersistenceLeavesNoWrites() throws {
        let commitGuard = NativeVaultOperationCommitGuard()
        let paused = DispatchSemaphore(value: 0), resume = DispatchSemaphore(value: 0), done = DispatchSemaphore(value: 0)
        var writes = 0; var committed = true
        DispatchQueue.global(qos: .userInitiated).async {
            paused.signal()
            _ = resume.wait(timeout: .now() + 2)
            committed = (try? commitGuard.commit { writes += 1 }) ?? true
            done.signal()
        }
        guard paused.wait(timeout: .now() + 1) == .success, commitGuard.cancel() == .cancelled else { throw Failure.bad }
        resume.signal()
        guard done.wait(timeout: .now() + 2) == .success, !committed, writes == 0 else { throw Failure.bad }
    }
    @MainActor static func currentConnectionAdmissionReturnsToMainActor() throws {
        let admission = NativeVaultCurrentConnectionAdmission()
        guard admission.admit(), !admission.admit() else { throw Failure.bad }
        let workerDone = DispatchSemaphore(value: 0)
        var cleanupOnMain = false
        DispatchQueue.global(qos: .userInitiated).async {
            // This is the controller's worker-result handoff shape: the worker
            // never mutates admission/UI state and returns to MainActor once.
            DispatchQueue.main.async {
                admission.finish { cleanupOnMain = Thread.isMainThread }
                workerDone.signal()
            }
        }
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        guard workerDone.wait(timeout: .now()) == .success, cleanupOnMain, admission.admit() else { throw Failure.bad }
    }
    static func rejects(_ body: () throws -> Void) throws {
        do { try body(); throw Failure.bad } catch Failure.bad { throw Failure.bad } catch { }
    }
    enum Failure: Error { case bad }
}
