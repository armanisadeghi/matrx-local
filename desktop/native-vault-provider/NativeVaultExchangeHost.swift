import AppKit
import Foundation

// The containing process owns this bridge. Only UUID selections and closed
// value-free statuses cross the C ABI; private material stays in native Swift.
@MainActor
private enum NativeVaultExchangeHost {
    static var controller: AnyObject?

    static func dispatch(_ data: Data, window: NSWindow?) throws -> Data {
        let object = try StrictEnvelope.object(data, required: ["action"], optional: ["item_ids", "organization_id", "operation_id"], maxBytes: 96 * 1024)
        guard case let .string(action)? = object["action"] else { throw CocoaError(.coderInvalidValue) }
        guard #available(macOS 26.0, *) else {
            if action == "invalidate", object.count == 1 { return Data("{}".utf8) }
            throw CocoaError(.featureUnsupported)
        }
        if action == "invalidate", object.count == 1 {
            (controller as? NativeVaultExportController)?.invalidate()
            controller = nil
            return Data("{}".utf8)
        }
        if action == "begin_export" {
            guard object.count == 3,
                  case let .array(values)? = object["item_ids"], !values.isEmpty, values.count <= 2_000,
                  case let .string(scope)? = object["organization_id"], let organization = UUID(uuidString: scope),
                  let window else { throw CocoaError(.coderInvalidValue) }
            let ids = try values.map { value -> UUID in
                guard case let .string(raw) = value, let id = UUID(uuidString: raw) else { throw CocoaError(.coderInvalidValue) }
                return id
            }
            guard Set(ids).count == ids.count else { throw CocoaError(.coderInvalidValue) }
            (controller as? NativeVaultExportController)?.invalidate()
            let next = NativeVaultExportController(presentationAnchor: window, key: {
                guard let plugins = Bundle.main.builtInPlugInsURL,
                      let provider = Bundle(url: plugins.appendingPathComponent("AI Matrx Vault Provider.appex")),
                      provider.bundleIdentifier == "com.aimatrx.desktop.vault-provider" else { return nil }
                return provider.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String
            })
            controller = next
            let id = next.beginExport(itemIDs: ids, organizationID: organization)
            return try JSONEncoder().encode(next.status(operationID: id))
        }
        guard (action == "status" || action == "cancel" || action == "confirm"), object.count == 2,
              case let .string(raw)? = object["operation_id"], let id = UUID(uuidString: raw),
              let controller = controller as? NativeVaultExportController else { throw CocoaError(.coderInvalidValue) }
        if action == "cancel" { controller.cancel(operationID: id) }
        if action == "confirm" { controller.confirm(operationID: id) }
        return try JSONEncoder().encode(controller.status(operationID: id))
    }
}

@_cdecl("matrx_vault_exchange_dispatch")
func matrxVaultExchangeDispatch(_ input: UnsafePointer<UInt8>?, _ length: Int, _ window: UnsafeMutableRawPointer?, _ output: UnsafeMutablePointer<UInt8>?, _ capacity: Int) -> Int {
    guard Thread.isMainThread, let input, length > 0, length <= 96 * 1024,
          let output, capacity >= 2_048 else { return -1 }
    let result: Data? = MainActor.assumeIsolated {
        let anchor = window.map { Unmanaged<NSWindow>.fromOpaque($0).takeUnretainedValue() }
        return try? NativeVaultExchangeHost.dispatch(Data(bytes: input, count: length), window: anchor)
    }
    guard let result, result.count <= 2_048 else { return -1 }
    result.copyBytes(to: output, count: result.count)
    return result.count
}
