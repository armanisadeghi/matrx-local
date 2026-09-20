import AppKit
import Foundation

func nativeVaultProviderKey() -> String? {
    guard let plugins = Bundle.main.builtInPlugInsURL,
          let provider = Bundle(url: plugins.appendingPathComponent("AI Matrx Vault Provider.appex")),
          provider.bundleIdentifier == "com.aimatrx.desktop.vault-provider" else { return nil }
    return provider.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String
}

// The containing process owns this bridge. Only UUID selections and closed
// value-free statuses cross the C ABI; private material stays in native Swift.
@MainActor
private enum NativeVaultExchangeHost {
    static var controller: AnyObject?
    @available(macOS 26.0, *) static var importController: NativeVaultImportHost?

    static func dispatch(_ data: Data, window: NSWindow?) throws -> Data {
        let object = try StrictEnvelope.object(data, required: ["action"], optional: ["item_ids", "organization_id", "operation_id", "offset", "preview_digest"], maxBytes: 96 * 1024)
        guard case let .string(action)? = object["action"] else { throw CocoaError(.coderInvalidValue) }
        guard #available(macOS 26.0, *) else {
            if action == "invalidate", object.count == 1 { return Data("{}".utf8) }
            throw CocoaError(.featureUnsupported)
        }
        if action == "invalidate", object.count == 1 {
            (controller as? NativeVaultExportController)?.invalidate()
            importController?.invalidate()
            controller = nil
            importController = nil
            return Data("{}".utf8)
        }
        if action == "import_begin" {
            guard object.count == 2, case let .string(scope)? = object["organization_id"], let organization = UUID(uuidString: scope), let window else { throw CocoaError(.coderInvalidValue) }
            if importController?.blocksReplacement() == true { throw CocoaError(.coderInvalidValue) }
            importController?.invalidate()
            (controller as? NativeVaultExportController)?.invalidate(); controller = nil
            let host = NativeVaultImportHost(presentationAnchor: window); importController = host
            let id = host.begin(organizationID: organization)
            return try JSONEncoder().encode(host.status(id))
        }
        if action == "import_recover", object.count == 1, let window {
            if importController?.blocksRecovery() == true { throw CocoaError(.coderInvalidValue) }
            importController?.invalidate()
            let host = NativeVaultImportHost(presentationAnchor: window); importController = host
            let id = host.recover(); return try JSONEncoder().encode(host.status(id))
        }
        if action == "import_status" || action == "import_cancel" || action == "import_confirm" || action == "import_preview" || action == "import_choose_scope" {
            guard case let .string(raw)? = object["operation_id"], let id = UUID(uuidString: raw), let host = importController else { throw CocoaError(.coderInvalidValue) }
            switch action {
            case "import_cancel": guard object.count == 2 else { throw CocoaError(.coderInvalidValue) }; host.cancel(id); return try JSONEncoder().encode(host.status(id))
            case "import_confirm": guard object.count == 3, case let .string(digest)? = object["preview_digest"] else { throw CocoaError(.coderInvalidValue) }; host.confirm(id, digest: digest); return try JSONEncoder().encode(host.status(id))
            case "import_choose_scope": guard object.count == 3, case let .string(scope)? = object["organization_id"], let organization = UUID(uuidString: scope) else { throw CocoaError(.coderInvalidValue) }; host.chooseScope(id, organizationID: organization); return try JSONEncoder().encode(host.status(id))
            case "import_preview": guard object.count == 3, case let .number(text)? = object["offset"], let offset = Int(text), let preview = host.preview(id, offset: offset) else { throw CocoaError(.coderInvalidValue) }; return try JSONEncoder().encode(preview)
            default: guard object.count == 2 else { throw CocoaError(.coderInvalidValue) }; return try JSONEncoder().encode(host.status(id))
            }
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
            let next = NativeVaultExportController(presentationAnchor: window, key: nativeVaultProviderKey)
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
          let output, capacity >= 64 * 1024 else { return -1 }
    let result: Data? = MainActor.assumeIsolated {
        let anchor = window.map { Unmanaged<NSWindow>.fromOpaque($0).takeUnretainedValue() }
        return try? NativeVaultExchangeHost.dispatch(Data(bytes: input, count: length), window: anchor)
    }
    guard let result, result.count <= 64 * 1024 else { return -1 }
    result.copyBytes(to: output, count: result.count)
    return result.count
}
