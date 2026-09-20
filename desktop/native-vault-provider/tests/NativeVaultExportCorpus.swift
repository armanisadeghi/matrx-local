import AppKit
import AuthenticationServices
import Foundation
@preconcurrency import LocalAuthentication

@available(macOS 26.0, *)
private actor MemoryCeremony: NativeCeremony {
    var source: Data?
    func verifyUser() async throws {}
    func persistRegistration(canonicalSource: Data) async throws { source = canonicalSource }
    func value() -> Data { source! }
}

@available(macOS 26.0, *)
private final class Apple: NativeVaultExportAppleManaging {
    var events: [String] = []
    var held: CheckedContinuation<ASCredentialExportManager.ExportOptions, Error>?
    var holdChooser = false
    var handed = 0

    private func v1() throws -> ASCredentialExportManager.ExportOptions {
        try JSONDecoder().decode(
            ASCredentialExportManager.ExportOptions.self,
            from: Data(#"{"formatVersion":{"major":1,"minor":0}}"#.utf8)
        )
    }

    func requestExport(providerID: String) async throws -> ASCredentialExportManager.ExportOptions {
        events.append("chooser:\(providerID)")
        if holdChooser { return try await withCheckedThrowingContinuation { held = $0 } }
        return try v1()
    }

    func export(_ data: ASExportedCredentialData) async throws {
        handed = data.accounts.flatMap(\.items).count
        events.append("handoff")
    }

    func release() { held?.resume(returning: try! v1()); held = nil }
}

@available(macOS 26.0, *)
private final class Server: NativeVaultExportTransporting {
    let source: Data
    var unavailablePreflight: Set<UUID> = []
    var unavailableMaterialize: Set<UUID> = []
    var requests: [String] = []
    var materialize = 0

    init(source: Data) { self.source = source }
    func cancel() {}

    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let path = request.url!.path
        requests.append(path)
        let components = path.split(separator: "/")
        guard let passkeys = components.lastIndex(of: "passkeys"), components.indices.contains(passkeys + 1), let item = UUID(uuidString: String(components[passkeys + 1])) else {
            preconditionFailure("unexpected export path")
        }
        let status: Int
        let object: [String: Any]
        if path.hasSuffix("/preflight") {
            status = unavailablePreflight.contains(item) ? 409 : 200
            object = status == 200
                ? ["item_id": item.uuidString.lowercased(), "selection_revision": String(repeating: "a", count: 64)]
                : ["error": "policy_unavailable"]
        } else if path.hasSuffix("/export") {
            materialize += 1
            status = unavailableMaterialize.contains(item) ? 409 : 200
            object = status == 200
                ? ["source": source.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")]
                : ["error": "native_unavailable"]
        } else {
            preconditionFailure("unexpected export path")
        }
        completion(.success((try! JSONSerialization.data(withJSONObject: object), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)))
    }
}

@available(macOS 26.0, *)
@MainActor private func wait(_ predicate: @escaping () -> Bool) async {
    for _ in 0..<200 {
        if predicate() { return }
        try? await Task.sleep(nanoseconds: 5_000_000)
    }
    preconditionFailure("controller did not settle")
}

@available(macOS 26.0, *)
@main struct NativeVaultExportCorpus {
    static func source() async throws -> Data {
        let ceremony = MemoryCeremony()
        _ = try await NativeOperation().register(
            input: NativeRegistrationInput(rpId: "example.com", userHandle: Data("export-user".utf8), username: "export-user", displayName: nil, clientDataHash: Data(repeating: 1, count: 32), supportedAlgorithms: [-7], excludedCredentialIds: []),
            existingSources: [], maxSourceBytes: 65_536, ceremony: ceremony
        )
        return await ceremony.value()
    }

    @MainActor fileprivate static func controller(apple: Apple, server: Server, current: @escaping () -> Bool) -> NativeVaultExportController {
        NativeVaultExportController(
            presentationAnchor: NSWindow(), transport: server, apple: apple,
            key: { "test" }, evaluate: { _, done in done(true) }, contextFactory: { LAContext() },
            acquire: { _, _ in .init(accessToken: "token", subject: "subject", generation: "generation") },
            current: { _ in current() }
        )
    }

    @MainActor static func awaitConfirmation(_ controller: NativeVaultExportController, _ operation: UUID) async {
        await wait { controller.status(operationID: operation).phase == .awaiting_confirmation }
        let status = controller.status(operationID: operation)
        precondition(status.total == 1 && status.eligible == 1 && status.unsupported == 0 && status.handed_off == 0)
    }

    @MainActor static func main() async {
        let canonicalSource = try! await source()
        let item = UUID(); let organization = UUID()

        // Metadata preflights complete before confirmation and before Apple sees a chooser.
        let successApple = Apple(); let successServer = Server(source: canonicalSource)
        let success = controller(apple: successApple, server: successServer, current: { true })
        let successID = success.beginExport(itemIDs: [item], organizationID: organization)
        await awaitConfirmation(success, successID)
        precondition(successApple.events.isEmpty && successServer.materialize == 0)
        precondition(successServer.requests == ["/api/vault/native/passkeys/\(item.uuidString.lowercased())/export/preflight"])
        success.confirm(operationID: successID)
        // A duplicate confirmation before the task receives its next turn is idempotent.
        success.confirm(operationID: successID)
        await wait { success.status(operationID: successID).phase == .handed_to_destination }
        let successStatus = success.status(operationID: successID)
        precondition(successStatus.total == 1 && successStatus.eligible == 1 && successStatus.unsupported == 0 && successStatus.handed_off == 1)
        precondition(successApple.events == ["chooser:com.aimatrx.desktop.vault-provider", "handoff"])
        precondition(successServer.requests == [
            "/api/vault/native/passkeys/\(item.uuidString.lowercased())/export/preflight",
            "/api/vault/native/passkeys/\(item.uuidString.lowercased())/export"
        ])

        // Cancellation while the UI is still previewing must issue no chooser or private read.
        let beforeConfirmApple = Apple(); let beforeConfirmServer = Server(source: canonicalSource)
        let beforeConfirm = controller(apple: beforeConfirmApple, server: beforeConfirmServer, current: { true })
        let beforeConfirmID = beforeConfirm.beginExport(itemIDs: [item], organizationID: organization)
        await awaitConfirmation(beforeConfirm, beforeConfirmID)
        beforeConfirm.cancel(operationID: beforeConfirmID)
        precondition(beforeConfirm.status(operationID: beforeConfirmID).phase == .cancelled)
        precondition(beforeConfirmApple.events.isEmpty && beforeConfirmServer.materialize == 0)

        // A generation change after metadata preview prevents chooser, source read, and handoff.
        var isCurrent = true
        let staleApple = Apple(); let staleServer = Server(source: canonicalSource)
        let stale = controller(apple: staleApple, server: staleServer, current: { isCurrent })
        let staleID = stale.beginExport(itemIDs: [item], organizationID: organization)
        await awaitConfirmation(stale, staleID)
        isCurrent = false
        stale.confirm(operationID: staleID)
        await wait { stale.status(operationID: staleID).phase == .cancelled }
        precondition(staleApple.events.isEmpty && staleServer.materialize == 0 && staleApple.handed == 0)

        // A second item that fails metadata preflight blocks the chooser and all private reads.
        let blocked = UUID(); let unavailableApple = Apple(); let unavailableServer = Server(source: canonicalSource)
        unavailableServer.unavailablePreflight = [blocked]
        let unavailable = controller(apple: unavailableApple, server: unavailableServer, current: { true })
        let unavailableID = unavailable.beginExport(itemIDs: [item, blocked], organizationID: organization)
        await wait { unavailable.status(operationID: unavailableID).phase == .failed }
        let unavailableStatus = unavailable.status(operationID: unavailableID)
        precondition(unavailableStatus.total == 2 && unavailableStatus.eligible == 0 && unavailableStatus.unsupported == 0)
        precondition(unavailableApple.events.isEmpty && unavailableServer.materialize == 0)

        // Cancellation after the Apple chooser opens cannot materialize or hand off a source.
        let cancelApple = Apple(); cancelApple.holdChooser = true; let cancelServer = Server(source: canonicalSource)
        let cancelled = controller(apple: cancelApple, server: cancelServer, current: { true })
        let cancelledID = cancelled.beginExport(itemIDs: [item], organizationID: organization)
        await awaitConfirmation(cancelled, cancelledID)
        cancelled.confirm(operationID: cancelledID)
        await wait { cancelApple.held != nil }
        // A late duplicate while the OS chooser is active cannot overwrite the running status.
        cancelled.confirm(operationID: cancelledID)
        precondition(cancelled.status(operationID: cancelledID).phase == .choosing_destination)
        cancelled.cancel(operationID: cancelledID); cancelApple.release()
        await wait { cancelled.status(operationID: cancelledID).phase == .cancelled }
        precondition(cancelServer.materialize == 0 && cancelApple.handed == 0)

        // A post-chooser failure on a later private read is all-or-nothing: no partial handoff.
        let later = UUID(); let materializeApple = Apple(); let materializeServer = Server(source: canonicalSource)
        materializeServer.unavailableMaterialize = [later]
        let materializeFailure = controller(apple: materializeApple, server: materializeServer, current: { true })
        let materializeID = materializeFailure.beginExport(itemIDs: [item, later], organizationID: organization)
        await wait { materializeFailure.status(operationID: materializeID).phase == .awaiting_confirmation }
        materializeFailure.confirm(operationID: materializeID)
        await wait { materializeFailure.status(operationID: materializeID).phase == .failed }
        precondition(materializeApple.events == ["chooser:com.aimatrx.desktop.vault-provider"] && materializeApple.handed == 0)
        precondition(materializeServer.materialize == 2, "the failed second read must prevent partial handoff")

        // Replacing an operation makes historical status IDs unavailable instead
        // of retaining an unbounded status dictionary.
        let replacementApple = Apple(); let replacementServer = Server(source: canonicalSource)
        let replacement = controller(apple: replacementApple, server: replacementServer, current: { true })
        let first = replacement.beginExport(itemIDs: [item], organizationID: organization)
        await awaitConfirmation(replacement, first)
        let second = replacement.beginExport(itemIDs: [item], organizationID: organization)
        precondition(replacement.status(operationID: first).phase == .unavailable)
        await awaitConfirmation(replacement, second)
        replacement.cancel(operationID: second)

        print("PASS native export controller corpus")
    }
}
