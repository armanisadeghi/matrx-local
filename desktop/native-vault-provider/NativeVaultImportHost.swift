import AppKit
import Foundation
@preconcurrency import LocalAuthentication

/// Containing-process coordinator for file import. The WebView supplies only an
/// explicit scope UUID and opaque operation identifiers; the chooser, session,
/// capability snapshot, and source bytes never cross this boundary.
@available(macOS 26.0, *)
@MainActor
final class NativeVaultImportHost {
    struct PublicStatus: Codable {
        let operation_id: String; let phase: String; let total: Int; let committed: Int; let already_present: Int
        let unsupported: Int; let failed: Int; let uncertain: Int; let not_attempted: Int; let message: String
    }
    struct PublicSlot: Codable { let slot_id: String; let title: String?; let disposition: String; let reason: String? }
    struct PublicPreview: Codable { let operation_id: String; let preview_digest: String; let offset: Int; let total: Int; let slots: [PublicSlot] }
    private struct Capability { let maxSource: Int; let maxBody: Int; let maxItems: Int; let maxFile: Int }
    private struct Membership { let id: String; let isPersonal: Bool }
    private final class Operation {
        let id = UUID(); let organization: UUID; let lifetime = NativeVaultRequestLifetime()
        var task: Task<Void, Never>?; var context: LAContext?; var parser: NativeVaultImportParser?; var controller: NativeVaultImportController?; var innerID: UUID?
        var status: PublicStatus
        init(organization: UUID) { self.organization = organization; status = .init(operation_id: id.uuidString.lowercased(), phase: "authorizing", total: 0, committed: 0, already_present: 0, unsupported: 0, failed: 0, uncertain: 0, not_attempted: 0, message: "Authorizing passkey import.") }
    }
    private let anchor: NSWindow
    typealias Grant = NativeVaultSessionAccess.Grant
    typealias TransportFactory = (Grant, NativeVaultRequestLifetime, NativeVaultSessionAccess, [UUID: String], @escaping () -> Bool) -> NativeVaultImportTransporting
    private let wire: NativeVaultPasskeyTransporting
    private let key: () -> String?
    private let choose: @MainActor () -> URL?
    private let authentication: ((String, NativeVaultRequestLifetime) async throws -> Grant)?
    private let currentGrant: ((Grant) -> Bool)?
    private let transportFactory: TransportFactory
    private let journalDirectory: URL?
    private var active: Operation?
    init(presentationAnchor: NSWindow, key: @escaping () -> String? = nativeVaultProviderKey, choose: @escaping @MainActor () -> URL? = {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
        return panel.runModal() == .OK ? panel.url : nil
    }, authentication: ((String, NativeVaultRequestLifetime) async throws -> Grant)? = nil,
       currentGrant: ((Grant) -> Bool)? = nil,
       wire: NativeVaultPasskeyTransporting = NativeVaultPasskeyTransport(),
       transportFactory: @escaping TransportFactory = { grant, lifetime, access, scopes, current in
           NativeVaultImportTransport(grant: grant, lifetime: lifetime, sessionAccess: access, principalByScope: scopes, current: current)
       }, journalDirectory: URL? = nil) {
        anchor = presentationAnchor; self.key = key; self.choose = choose
        self.authentication = authentication; self.currentGrant = currentGrant
        self.wire = wire; self.transportFactory = transportFactory; self.journalDirectory = journalDirectory
    }

    func blocksReplacement() -> Bool {
        guard let active else { return false }
        sync(active.id)
        return active.status.phase == "importing" || active.status.uncertain > 0
    }
    func blocksRecovery() -> Bool {
        guard let active else { return false }
        sync(active.id)
        return active.status.phase == "importing"
    }
    func begin(organizationID: UUID) -> UUID {
        if let active, active.controller == nil || active.status.phase == "preview" || active.status.phase == "awaiting_confirmation" { cancel(active.id) }
        let operation = Operation(organization: organizationID); active = operation
        operation.task = Task { [weak self, weak operation] in guard let self, let operation else { return }; await self.admit(operation) }
        return operation.id
    }
    func status(_ id: UUID) -> PublicStatus { sync(id); return active?.id == id ? active!.status : .init(operation_id: id.uuidString.lowercased(), phase: "unavailable", total: 0, committed: 0, already_present: 0, unsupported: 0, failed: 0, uncertain: 0, not_attempted: 0, message: "Passkey import is unavailable.") }
    func cancel(_ id: UUID) { guard let op = active, op.id == id else { return }; op.lifetime.cancel(); op.context?.invalidate(); op.parser?.cancel(); op.task?.cancel(); if let controller = op.controller, let inner = op.innerID { controller.cancel(operationID: inner); sync(id) } else { set(op, phase: "cancelled", message: "Passkey import cancelled.") } }
    func chooseScope(_ id: UUID, organizationID: UUID) { guard let op = active, op.id == id, op.organization == organizationID, let controller = op.controller, let inner = op.innerID else { return }; controller.chooseScope(operationID: inner, organizationID: organizationID); sync(id) }
    func confirm(_ id: UUID, digest: String) { guard let op = active, op.id == id, let controller = op.controller, let inner = op.innerID else { return }; controller.confirm(operationID: inner, previewDigest: digest); sync(id) }
    func preview(_ id: UUID, offset: Int) -> PublicPreview? { guard offset >= 0, offset <= 2_000, let op = active, op.id == id, let controller = op.controller, let inner = op.innerID, let value = controller.preview(operationID: inner) else { return nil }; let page = Array(value.slots.dropFirst(offset).prefix(8)); return .init(operation_id: id.uuidString.lowercased(), preview_digest: value.digest, offset: offset, total: value.slots.count, slots: page.map { .init(slot_id: $0.slotID.uuidString.lowercased(), title: $0.title, disposition: $0.disposition.rawValue, reason: $0.reason) }) }
    func recover() -> UUID {
        let operation = Operation(organization: UUID())
        active = operation
        operation.task = Task { [weak self, weak operation] in guard let self, let operation else { return }; await self.recover(operation) }
        return operation.id
    }
    func invalidate() { if let active { cancel(active.id) }; active = nil }

    private func recover(_ op: Operation) async {
        do {
            let access = NativeVaultSessionAccess()
            let grant = try await authenticate(op, access: access)
            let url = NativeVaultImportJournal.url(base: journalBase(), subject: grant.subject, generation: grant.generation)
            guard let journal = try NativeVaultImportJournal.load(from: url), journal.subject == grant.subject, journal.generation == grant.generation, current(op), grantCurrent(grant) else { throw CancellationError() }
            let membership = try await membership(journal.organizationID, grant: grant)
            let limits = try await capabilities(journal.organizationID, grant: grant)
            guard journal.entries.count <= limits.maxItems, current(op), grantCurrent(grant) else { throw CancellationError() }
            let transport = transportFactory(grant, op.lifetime, access, [journal.organizationID: membership.isPersonal ? "user" : "organization"], { [weak self, weak op] in (self?.current(op) ?? false) && (self?.grantCurrent(grant) ?? false) })
            let controller = NativeVaultImportController(parser: NativeVaultImportParser(), transport: transport, journalURL: journalBase(), maximumItems: limits.maxItems, currentBinding: { [weak self, weak op] in guard (self?.current(op) ?? false) && (self?.grantCurrent(grant) ?? false) else { return nil }; return (grant.subject, grant.generation) })
            op.controller = controller; op.innerID = journal.operationID
            _ = await controller.recoverJournal()
            sync(op.id)
        } catch { if current(op) { set(op, phase: "unavailable", message: "Passkey import recovery is unavailable.") } }
    }
    private func admit(_ op: Operation) async {
        do {
            let access = NativeVaultSessionAccess()
            let grant = try await authenticate(op, access: access)
            guard current(op), grantCurrent(grant) else { throw CancellationError() }
            let organization = try await membership(op.organization, grant: grant)
            let limits = try await capabilities(op.organization, grant: grant)
            guard current(op), grantCurrent(grant), organization.id == op.organization.uuidString.lowercased() else { throw CancellationError() }
            guard let url = choose(), current(op), grantCurrent(grant) else { throw CancellationError() }
            let parser = NativeVaultImportParser(); op.parser = parser
            let inventory = try await Task.detached(priority: .userInitiated) {
                let bytes = try NativeVaultImportFile.read(url)
                guard !Task.isCancelled, bytes.count <= limits.maxFile else { throw CancellationError() }
                return try parser.parseCXFv1(bytes)
            }.value
            op.parser = nil
            guard current(op), grantCurrent(grant), inventory.total <= limits.maxItems,
                  inventory.candidates.allSatisfy({ candidate in
                      candidate.canonicalSource.count <= limits.maxSource
                          && candidate.title.utf8.count <= 1_024
                          && (try? JSONSerialization.data(withJSONObject: [
                              "mutation_id": UUID().uuidString.lowercased(),
                              "source": NativeVaultPasskeyCodec.base64url(candidate.canonicalSource),
                              "label": candidate.title,
                              "principal_type": organization.isPersonal ? "user" : "organization",
                              "format_version": "cxf1.0"
                          ], options: [.sortedKeys]).count <= limits.maxBody) == true
                  }) else { throw CancellationError() }
            let transport = transportFactory(grant, op.lifetime, access, [op.organization: organization.isPersonal ? "user" : "organization"], { [weak self, weak op] in (self?.current(op) ?? false) && (self?.grantCurrent(grant) ?? false) })
            let controller = NativeVaultImportController(parser: NativeVaultImportParser(), transport: transport, journalURL: journalBase(), maximumItems: limits.maxItems, currentBinding: { [weak self, weak op] in guard (self?.current(op) ?? false) && (self?.grantCurrent(grant) ?? false) else { return nil }; return (grant.subject, grant.generation) })
            op.controller = controller; op.innerID = controller.beginInventory(inventory); sync(op.id)
        } catch is CancellationError { if current(op) { set(op, phase: "cancelled", message: "Passkey import cancelled.") } }
        catch { if current(op) { set(op, phase: "failed", message: "Passkey import could not start.") } }
    }
    private func membership(_ id: UUID, grant: NativeVaultSessionAccess.Grant) async throws -> Membership {
        let data = try await get("api/auth/organizations", grant: grant, organization: nil)
        let organizations = try NativeOrganizationCodec.organizations(data, subject: grant.subject)
        guard let selected = organizations.first(where: { $0.id == id.uuidString.lowercased() }) else { throw CancellationError() }
        return .init(id: selected.id, isPersonal: selected.isPersonal)
    }

    private func capabilities(_ id: UUID, grant: NativeVaultSessionAccess.Grant) async throws -> Capability { let data = try await get("api/vault/native/passkeys/import/capabilities", grant: grant, organization: id); let object = try StrictEnvelope.object(data, required: ["protocol_version", "activation_revision", "max_source_bytes", "max_request_body_bytes", "max_transfer_items", "max_file_bytes"], optional: []); func integer(_ key: String, _ range: ClosedRange<Int>) -> Int? { guard case let .number(value)? = object[key], let result = Int(value), range.contains(result) else { return nil }; return result }; guard integer("protocol_version", 1...1) != nil, integer("activation_revision", 1...Int.max) != nil, let source = integer("max_source_bytes", 1...65_536), let body = integer("max_request_body_bytes", 1...98_304), let items = integer("max_transfer_items", 1...2_000), let file = integer("max_file_bytes", 1...16_777_216) else { throw CancellationError() }; return .init(maxSource: source, maxBody: body, maxItems: items, maxFile: file) }
    private func get(_ path: String, grant: NativeVaultSessionAccess.Grant, organization: UUID?) async throws -> Data { var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent(path)); request.timeoutInterval = 10; request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue("application/json", forHTTPHeaderField: "Accept"); if let organization { request.setValue(organization.uuidString.lowercased(), forHTTPHeaderField: "X-Organization-Id") }; let result: Result<(Data, HTTPURLResponse), Error> = await withCheckedContinuation { done in wire.send(request) { done.resume(returning: $0) } }; let (data, response) = try result.get(); guard response.statusCode == 200 else { throw CancellationError() }; return data }
    private func authenticate(_ op: Operation, access: NativeVaultSessionAccess) async throws -> Grant {
        guard current(op), let key = key(), !key.isEmpty else { throw CancellationError() }
        if let authentication { return try await authentication(key, op.lifetime) }
        let context = try NativeVaultPrivateSession().authenticatedContext(reason: "Import passkeys")
        op.context = context
        guard await verified(context), current(op) else { throw CancellationError() }
        return try await withCheckedThrowingContinuation { done in
            access.acquire(key: key, context: context, lifetime: op.lifetime) { done.resume(with: $0) }
        }
    }
    private func verified(_ context: LAContext) async -> Bool { await withCheckedContinuation { done in context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Import passkeys") { ok, _ in done.resume(returning: ok) } } }
    private func current(_ op: Operation?) -> Bool { guard let op else { return false }; return active === op && op.lifetime.isCurrent && !Task.isCancelled }
    private func grantCurrent(_ grant: NativeVaultSessionAccess.Grant) -> Bool { if let currentGrant { return currentGrant(grant) }; return (try? ProviderStore(mode: .providerAccess).locked { $0.generation == grant.generation && $0.provider_subject == grant.subject }) ?? false }
    private func journalBase() -> URL { journalDirectory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!.appendingPathComponent("AI Matrx/Vault Import", isDirectory: true) }
    private func set(_ op: Operation, phase: String, message: String) { op.status = .init(operation_id: op.id.uuidString.lowercased(), phase: phase, total: 0, committed: 0, already_present: 0, unsupported: 0, failed: 0, uncertain: 0, not_attempted: 0, message: message) }
    private func sync(_ id: UUID) { guard let op = active, op.id == id, let controller = op.controller, let inner = op.innerID else { return }; let value = controller.status(operationID: inner); op.status = .init(operation_id: id.uuidString.lowercased(), phase: value.phase.rawValue, total: value.total, committed: value.committed, already_present: value.alreadyPresent, unsupported: value.unsupported, failed: value.failed, uncertain: value.uncertain, not_attempted: value.notAttempted, message: value.message) }
}
