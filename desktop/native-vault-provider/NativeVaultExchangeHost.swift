import AppKit
import Foundation

// The containing process owns this bridge. Only UUID selections and closed
// value-free statuses cross the C ABI; private material stays in native Swift.
@MainActor
private enum NativeVaultExchangeHost {
    static var controller: AnyObject?

    static func dispatch(_ data: Data, window: NSWindow?) throws -> Data {
        guard #available(macOS 26.0, *) else { throw CocoaError(.featureUnsupported) }
        let object = try StrictEnvelope.object(data, required: ["action"], optional: ["item_ids", "organization_id", "operation_id"], maxBytes: 96 * 1024)
        guard case let .string(action)? = object["action"] else { throw CocoaError(.coderInvalidValue) }
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
        guard (action == "status" || action == "cancel"), object.count == 2,
              case let .string(raw)? = object["operation_id"], let id = UUID(uuidString: raw),
              let controller = controller as? NativeVaultExportController else { throw CocoaError(.coderInvalidValue) }
        if action == "cancel" { controller.cancel(operationID: id) }
        return try JSONEncoder().encode(controller.status(operationID: id))
    }
}

@_cdecl("matrx_vault_exchange_dispatch")
func matrxVaultExchangeDispatch(_ input: UnsafePointer<UInt8>?, _ length: Int, _ window: UnsafeMutableRawPointer?) -> UnsafeMutablePointer<CChar>? {
    guard Thread.isMainThread, let input, length > 0, length <= 96 * 1024 else { return nil }
    let text: String? = MainActor.assumeIsolated {
        let anchor = window.map { Unmanaged<NSWindow>.fromOpaque($0).takeUnretainedValue() }
        guard let result = try? NativeVaultExchangeHost.dispatch(Data(bytes: input, count: length), window: anchor),
              result.count <= 2_048, let text = String(data: result, encoding: .utf8) else { return nil }
        return text
    }
    return text.flatMap { strdup($0) }
}

@_cdecl("matrx_vault_exchange_free")
func matrxVaultExchangeFree(_ response: UnsafeMutablePointer<CChar>?) { free(response) }
