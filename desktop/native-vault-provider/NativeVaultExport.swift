import AppKit
import AuthenticationServices
import Foundation
@preconcurrency import LocalAuthentication

@available(macOS 26.0, *)
struct NativeVaultExportStatus: Codable, Equatable {
    enum Phase: String, Codable { case idle, authorizing, preflighting, awaiting_confirmation, choosing_destination, exporting, handed_to_destination, cancelled, failed, unavailable }
    let operation_id: String
    let phase: Phase
    let total: Int
    let eligible: Int
    let unsupported: Int
    let handed_off: Int
    let message: String
}

@available(macOS 26.0, *)
protocol NativeVaultExportAppleManaging: AnyObject {
    func requestExport(providerID: String) async throws -> ASCredentialExportManager.ExportOptions
    func export(_ data: ASExportedCredentialData) async throws
}

@available(macOS 26.0, *)
private final class NativeVaultSystemExportManager: NativeVaultExportAppleManaging {
    private let manager: ASCredentialExportManager
    init(anchor: ASPresentationAnchor) { manager = ASCredentialExportManager(presentationAnchor: anchor) }
    func requestExport(providerID: String) async throws -> ASCredentialExportManager.ExportOptions { try await manager.requestExport(for: providerID) }
    func export(_ data: ASExportedCredentialData) async throws { try await manager.exportCredentials(data) }
}

@available(macOS 26.0, *)
protocol NativeVaultExportTransporting: AnyObject {
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void)
    func cancel()
}

@available(macOS 26.0, *)
private final class NativeVaultExportTransport: NativeVaultExportTransporting {
    private let inner = NativeVaultPasskeyTransport()
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) { inner.send(request, completion: completion) }
    func cancel() { inner.cancel() }
}

/// Native-only Apple export. Its value-free status is the sole host-visible surface.
@available(macOS 26.0, *)
@MainActor
final class NativeVaultExportController {
    private static let providerID = "com.aimatrx.desktop.vault-provider"
    private static let maximumItems = 2_000
    private static let maximumAggregate = 16 * 1024 * 1024
    private let sessionAccess: NativeVaultSessionAccess
    private let transport: NativeVaultExportTransporting
    private let apple: NativeVaultExportAppleManaging
    private let key: () -> String?
    private let evaluate: (LAContext, @escaping (Bool) -> Void) -> Void
    private let contextFactory: () throws -> LAContext
    private let current: (NativeVaultSessionAccess.Grant) -> Bool
    private let acquireOverride: ((LAContext, NativeVaultRequestLifetime) async throws -> NativeVaultSessionAccess.Grant)?
    private var active: Operation?
    private var statuses: [UUID: NativeVaultExportStatus] = [:]

    private final class Operation {
        let id = UUID(); let ids: [UUID]; let organization: UUID; let lifetime = NativeVaultRequestLifetime()
        var task: Task<Void, Never>?; var context: LAContext?; var grant: NativeVaultSessionAccess.Grant?
        var revisions: [UUID: String] = [:]; var eligible = 0; var unsupported = 0
        init(ids: [UUID], organization: UUID) { self.ids = ids; self.organization = organization }
    }

    init(presentationAnchor: ASPresentationAnchor,
         sessionAccess: NativeVaultSessionAccess = NativeVaultSessionAccess(),
         transport: NativeVaultExportTransporting = NativeVaultExportTransport(),
         apple: NativeVaultExportAppleManaging? = nil,
         key: @escaping () -> String? = { Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String },
         evaluate: @escaping (LAContext, @escaping (Bool) -> Void) -> Void = { context, done in context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Export selected passkeys") { ok, _ in done(ok) } },
         contextFactory: @escaping () throws -> LAContext = { try NativeVaultPrivateSession().authenticatedContext(reason: "Export selected passkeys") },
         acquire: ((LAContext, NativeVaultRequestLifetime) async throws -> NativeVaultSessionAccess.Grant)? = nil,
         current: @escaping (NativeVaultSessionAccess.Grant) -> Bool = { grant in
             (try? ProviderStore(mode: .providerAccess).locked { $0.generation == grant.generation && $0.provider_subject == grant.subject }) ?? false
         }) {
        self.sessionAccess = sessionAccess; self.transport = transport
        self.apple = apple ?? NativeVaultSystemExportManager(anchor: presentationAnchor)
        self.key = key; self.evaluate = evaluate; self.contextFactory = contextFactory; self.acquireOverride = acquire; self.current = current
    }

    func beginExport(itemIDs: [UUID], organizationID: UUID) -> UUID {
        if let active { cancel(operationID: active.id) }
        let unique = Array(NSOrderedSet(array: itemIDs)) as? [UUID] ?? []
        let operation = Operation(ids: unique, organization: organizationID); active = operation
        guard !unique.isEmpty, unique.count <= Self.maximumItems else { finish(operation, .failed, message: "Selected passkeys exceed the export limit."); return operation.id }
        set(operation, .authorizing)
        operation.task = Task { [weak self, weak operation] in guard let self, let operation else { return }; await self.run(operation) }
        return operation.id
    }
    func cancel(operationID: UUID) {
        guard let operation = active, operation.id == operationID else { return }
        operation.lifetime.cancel(); operation.context?.invalidate(); operation.task?.cancel(); transport.cancel(); sessionAccess.cancel()
        finish(operation, .cancelled, message: "Export cancelled.")
    }
    /// A host confirmation can only continue a locally authenticated, current
    /// metadata selection. It carries no source bytes or authority material.
    func confirm(operationID: UUID) {
        guard let operation = active, operation.id == operationID,
              statuses[operationID]?.phase == .awaiting_confirmation,
              let grant = operation.grant,
              isCurrent(operation), current(grant) else {
            if let operation = active, operation.id == operationID { finish(operation, .cancelled, message: "Export cancelled.") }
            return
        }
        set(operation, .choosing_destination)
        operation.task = Task { [weak self, weak operation] in
            guard let self, let operation else { return }
            await self.transfer(operation, grant: grant)
        }
    }
    func invalidate() { if let active { cancel(operationID: active.id) }; statuses.removeAll() }
    func status(operationID: UUID) -> NativeVaultExportStatus { statuses[operationID] ?? .init(operation_id: operationID.uuidString.lowercased(), phase: .unavailable, total: 0, eligible: 0, unsupported: 0, handed_off: 0, message: "Export is unavailable.") }

    private func run(_ operation: Operation) async {
        do {
            guard isCurrent(operation) else { throw ExportFailure.cancelled }
            let context = try contextFactory()
            operation.context = context
            guard await verified(context), isCurrent(operation), let key = key(), !key.isEmpty else { throw ExportFailure.cancelled }
            let grant: NativeVaultSessionAccess.Grant
            if let acquireOverride { grant = try await acquireOverride(context, operation.lifetime) }
            else { grant = try await acquire(key: key, context: context, lifetime: operation.lifetime) }
            guard isCurrent(operation), current(grant) else { throw ExportFailure.cancelled }
            for id in operation.ids {
                guard isCurrent(operation), current(grant) else { throw ExportFailure.cancelled }
                set(operation, .preflighting)
                operation.revisions[id] = try await preflight(id, grant: grant, operation: operation)
            }
            guard operation.revisions.count == operation.ids.count, isCurrent(operation), current(grant) else { throw ExportFailure.refused }
            operation.grant = grant; operation.eligible = operation.ids.count
            set(operation, .awaiting_confirmation)
        } catch is CancellationError { finish(operation, .cancelled, message: "Export cancelled.")
        } catch ExportFailure.cancelled { finish(operation, .cancelled, message: "Export cancelled.")
        } catch { finish(operation, .failed, message: "Selected passkeys are unavailable for export.") }
    }

    private func transfer(_ operation: Operation, grant: NativeVaultSessionAccess.Grant) async {
        do {
            guard isCurrent(operation), current(grant), operation.revisions.count == operation.ids.count else { throw ExportFailure.cancelled }
            let options = try await apple.requestExport(providerID: Self.providerID)
            guard options.formatVersion == .v1, isCurrent(operation), current(grant) else { throw ExportFailure.cancelled }
            var entries: [ASImportableItem] = []; var retained = 0
            for id in operation.ids {
                guard isCurrent(operation), current(grant), let revision = operation.revisions[id] else { throw ExportFailure.cancelled }
                set(operation, .exporting)
                let source = try await materialize(id, revision: revision, grant: grant, operation: operation)
                retained += source.count; guard retained <= Self.maximumAggregate else { throw ExportFailure.refused }
                let converted = try nativeExportSourcePkcs8(source: source, maxSourceBytes: 65_536)
                guard valid(converted) else { throw ExportFailure.refused }
                let title = converted.displayName ?? converted.username ?? "Passkey"
                let passkey = ASImportableCredential.Passkey(credentialID: converted.credentialId, relyingPartyIdentifier: converted.rpId, userName: converted.username ?? "", userDisplayName: converted.displayName ?? "", userHandle: converted.userHandle, key: converted.pkcs8Der)
                let now = Date(); entries.append(ASImportableItem(id: id.data, created: now, lastModified: now, title: title, credentials: [.passkey(passkey)]))
            }
            guard entries.count == operation.ids.count, isCurrent(operation), current(grant) else { throw ExportFailure.refused }
            set(operation, .exporting)
            let account = ASImportableAccount(id: Data(grant.subject.utf8), userName: grant.subject, email: "", collections: [], items: entries)
            try await apple.export(ASExportedCredentialData(accounts: [account], formatVersion: .v1, exporterRelyingPartyIdentifier: "com.aimatrx.desktop", exporterDisplayName: "AI Matrx", timestamp: Date()))
            guard isCurrent(operation) else { throw ExportFailure.cancelled }
            finish(operation, .handed_to_destination, handed: entries.count, message: "Passkeys handed to the selected destination.")
        } catch is CancellationError { finish(operation, .cancelled, message: "Export cancelled.")
        } catch ExportFailure.cancelled { finish(operation, .cancelled, message: "Export cancelled.")
        } catch { finish(operation, .failed, message: "Passkey export could not finish.") }
    }

    private enum ExportFailure: Error { case cancelled, refused }
    private func valid(_ value: NativeExportSourcePkcs8) -> Bool {
        value.rpId.utf8.count <= 253 && !value.rpId.isEmpty && value.credentialId.count <= 1024 && !value.credentialId.isEmpty && value.userHandle.count <= 64 && !value.userHandle.isEmpty && value.pkcs8Der.count <= 4096 && !value.pkcs8Der.isEmpty && (value.username?.utf8.count ?? 0) <= 256 && (value.displayName?.utf8.count ?? 0) <= 256
    }
    private func verified(_ context: LAContext) async -> Bool { await withCheckedContinuation { done in evaluate(context) { done.resume(returning: $0) } } }
    private func acquire(key: String, context: LAContext, lifetime: NativeVaultRequestLifetime) async throws -> NativeVaultSessionAccess.Grant { try await withCheckedThrowingContinuation { done in sessionAccess.acquire(key: key, context: context, lifetime: lifetime) { done.resume(with: $0) } } }
    private func request(_ path: String, method: String, body: Data?, grant: NativeVaultSessionAccess.Grant, operation: Operation) async throws -> Data {
        guard isCurrent(operation), current(grant) else { throw ExportFailure.cancelled }
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent(path)); request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 10
        request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue(operation.organization.uuidString.lowercased(), forHTTPHeaderField: "X-Organization-Id"); request.setValue("application/json", forHTTPHeaderField: "Accept"); if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let result: Result<(Data, HTTPURLResponse), Error> = await withTaskCancellationHandler(operation: { await withCheckedContinuation { done in transport.send(request) { done.resume(returning: $0) } } }, onCancel: { [transport] in transport.cancel() })
        let settled: Result<(Data, HTTPURLResponse), Error> = await withCheckedContinuation { done in sessionAccess.reconcileResponse(result, grant: grant, lifetime: operation.lifetime) { done.resume(returning: $0) } }
        let (data, response) = try settled.get(); guard response.statusCode == 200, isCurrent(operation), current(grant) else { throw ExportFailure.refused }; return data
    }
    private func preflight(_ id: UUID, grant: NativeVaultSessionAccess.Grant, operation: Operation) async throws -> String {
        let data = try await request("api/vault/native/passkeys/\(id.uuidString.lowercased())/export/preflight", method: "POST", body: nil, grant: grant, operation: operation)
        let object = try StrictEnvelope.object(data, required: ["item_id", "selection_revision"], optional: [])
        guard case let .string(item)? = object["item_id"], item == id.uuidString.lowercased(), case let .string(revision)? = object["selection_revision"], revision.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { throw ExportFailure.refused }; return revision
    }
    private func materialize(_ id: UUID, revision: String, grant: NativeVaultSessionAccess.Grant, operation: Operation) async throws -> Data {
        let body = try JSONSerialization.data(withJSONObject: ["selection_revision": revision], options: [.sortedKeys])
        return try NativeVaultPasskeyCodec.materialize(await request("api/vault/native/passkeys/\(id.uuidString.lowercased())/export", method: "POST", body: body, grant: grant, operation: operation), maxSourceBytes: 65_536)
    }
    private func isCurrent(_ operation: Operation) -> Bool { active === operation && operation.lifetime.isCurrent && !Task.isCancelled }
    private func set(_ operation: Operation, _ phase: NativeVaultExportStatus.Phase) { statuses[operation.id] = .init(operation_id: operation.id.uuidString.lowercased(), phase: phase, total: operation.ids.count, eligible: operation.eligible, unsupported: operation.unsupported, handed_off: 0, message: "") }
    private func finish(_ operation: Operation, _ phase: NativeVaultExportStatus.Phase, handed: Int = 0, message: String) { guard active === operation else { return }; statuses[operation.id] = .init(operation_id: operation.id.uuidString.lowercased(), phase: phase, total: operation.ids.count, eligible: operation.eligible, unsupported: operation.unsupported, handed_off: handed, message: message); active = nil }
}

@available(macOS 26.0, *)
private extension UUID { var data: Data { withUnsafeBytes(of: uuid) { Data($0) } } }
