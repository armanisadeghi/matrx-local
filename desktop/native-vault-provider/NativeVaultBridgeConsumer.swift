import Foundation

// This source is compiled with generated UniFFI bindings in the provider
// target. It deliberately has no controller call site until Apple passkey
// request mapping and durable provider storage are accepted as a later unit.
enum NativeVaultBridgeConsumer {
    static func makeOperation() -> NativeOperation { NativeOperation() }
}
