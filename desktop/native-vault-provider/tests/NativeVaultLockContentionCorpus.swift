import Foundation

@main
struct NativeVaultLockContentionCorpus {
    static func main() throws {
        guard CommandLine.arguments.count == 3, let root = URL(string: CommandLine.arguments[2]) else { throw Failure.arguments }
        let mode = CommandLine.arguments[1]
        if mode == "hold" {
            let store = try ProviderStore(testRoot: root, mode: .explicitConnect)
            _ = try store.initializeExplicitConnect(invalidatePrivate: {})
            try store.locked { _ in
                print("READY"); fflush(stdout)
                Thread.sleep(forTimeInterval: 3)
            }
        } else if mode == "contend" {
            let store = try ProviderStore(testRoot: root, mode: .providerAccess)
            do {
                try store.locked { _ in () }
                throw Failure.acquired
            } catch let error as LocalizedError {
                guard error.errorDescription == "Vault setup is busy. Try again." else { throw error }
                print("real process lock contention refused")
            }
        } else { throw Failure.arguments }
    }
    enum Failure: Error { case arguments, acquired }
}
