import Foundation
import CryptoKit

/// The Swift/Rust adapter must retain candidate source bytes only in memory and
/// return fixed unsupported reasons. It receives bytes from NativeVaultImportFile,
/// never a host-provided path or JSON object.
protocol NativeVaultImportParsing {
    func parseCXFv1(_ bytes: Data) throws -> NativeVaultImportInventory
}

struct NativeVaultImportInventory {
    enum UnsupportedReason: String { case scoped, mixed_or_multiple_credentials, unsupported_credential }
    struct Candidate {
        let title: String
        let canonicalSource: Data
    }
    struct Unsupported {
        let index: Int
        let title: String
        let reason: UnsupportedReason
    }
    let total: Int
    let candidates: [Candidate]
    let unsupported: [Unsupported]
}

/// Dedicated native-import wire. Implementations must use the native OAuth
/// session and endpoint; its result and receipt contain no source material.
protocol NativeVaultImportTransporting {
    func importPasskey(mutationID: UUID, source: Data, label: String, organizationID: UUID) async throws -> NativeVaultImportTransportResult
    func receipt(mutationID: UUID, organizationID: UUID) async throws -> NativeVaultImportTransportResult?
    func cancel()
}

struct NativeVaultImportTransportResult { let mutationID: UUID }

/// Only the native transport may classify a failed write.  Callers must not
/// infer whether a request reached the server from an arbitrary Error.
enum NativeVaultImportTransportFailure: Error, Equatable {
    enum Reason: String, Equatable { case import_unavailable }
    case refused(reason: Reason)
    case unavailable(reason: Reason)
    case cancelled
    case uncertain
}

@available(macOS 26.0, *)
@MainActor
final class NativeVaultImportController {
    enum Phase: String { case idle, preview, awaiting_confirmation, importing, partial, completed, cancelled, failed, unavailable }
    enum Disposition: String { case eligible, unsupported, committed, failed, uncertain, not_attempted }
    struct SlotStatus: Equatable { let slotID: UUID; let title: String?; let disposition: Disposition; let reason: String? }
    struct Status: Equatable {
        let operationID: UUID; let phase: Phase; let total: Int; let committed: Int; let alreadyPresent: Int
        let unsupported: Int; let failed: Int; let uncertain: Int; let notAttempted: Int; let message: String; let slots: [SlotStatus]
    }

    private final class Operation {
        let id: UUID; let subject: String; let generation: String; let digest: String
        var organization: UUID?; var slots: [Slot]; var task: Task<Void, Never>?; var cancelled = false; var confirmed = false
        var reconciliationStarted = false
        init(id: UUID, subject: String, generation: String, digest: String, slots: [Slot]) { self.id = id; self.subject = subject; self.generation = generation; self.digest = digest; self.slots = slots }
    }
    private struct Slot { let id: UUID; let mutation: UUID?; let title: String?; var reason: String?; var source: Data?; var disposition: Disposition }

    private let parser: NativeVaultImportParsing
    private let transport: NativeVaultImportTransporting
    private let journalURL: URL
    private let currentBinding: () -> (subject: String, generation: String)?
    private let maximumItems: Int
    private var active: Operation?
    private var statuses: [UUID: Status] = [:]

    /// Recovery state is scoped to the account-generation that created it.  A
    /// different account must neither overwrite nor be blocked by an unresolved
    /// receipt belonging to the previous account.
    private func journalURL(for subject: String, generation: String) -> URL {
        NativeVaultImportJournal.url(base: journalURL, subject: subject, generation: generation)
    }

    private enum JournalAdmission {
        case none, blocked, corrupt
    }

    private func journalAdmission(for binding: (subject: String, generation: String)) -> JournalAdmission {
        let url = journalURL(for: binding.subject, generation: binding.generation)
        guard FileManager.default.fileExists(atPath: url.path) else { return .none }
        do {
            guard let journal = try NativeVaultImportJournal.load(from: url),
                  journal.subject == binding.subject, journal.generation == binding.generation
            else { return .corrupt }
            return journal.entries.contains(where: { $0.state == .uncertain || $0.state == .started }) ? .blocked : .none
        } catch { return .corrupt }
    }

    /// `transport` is bound to one native grant/lifetime. The host replaces this
    /// controller after terminal invalidation rather than reusing a cancelled
    /// transport for another account.
    init(parser: NativeVaultImportParsing, transport: NativeVaultImportTransporting, journalURL: URL, maximumItems: Int = 200, currentBinding: @escaping () -> (subject: String, generation: String)?) {
        self.parser = parser; self.transport = transport; self.journalURL = journalURL; self.currentBinding = currentBinding
        self.maximumItems = min(max(1, maximumItems), 2_000)
    }

    /// Admit a pre-parsed inventory created by the native run token. The host
    /// performs I/O and parsing off-main, then calls this only if that token,
    /// subject, and generation are still current.
    func beginInventory(_ inventory: NativeVaultImportInventory) -> UUID {
        let id = UUID()
        guard let binding = admitNewOperation(id) else { return id }
        return beginInventory(inventory, admittedID: id, binding: binding)
    }

    /// The caller owns the native chooser and passes only its in-memory reader.
    /// Production hosts use `beginInventory` after their cancellable off-main
    /// parser completes; this remains a small test-only convenience path.
    func beginFileImport(read: () throws -> Data) -> UUID {
        let id = UUID()
        guard let binding = admitNewOperation(id) else { return id }
        do {
            let inventory = try parser.parseCXFv1(read())
            return beginInventory(inventory, admittedID: id, binding: binding)
        } catch {
            statuses[id] = Status(operationID: id, phase: .failed, total: 0, committed: 0, alreadyPresent: 0, unsupported: 0, failed: 0, uncertain: 0, notAttempted: 0, message: "Passkey file could not be read.", slots: [])
            return id
        }
    }

    private func admitNewOperation(_ id: UUID) -> (subject: String, generation: String)? {
        if let active {
            if active.slots.contains(where: { $0.disposition == .uncertain }) {
                statuses[id] = unavailable(id, "Passkey receipt recovery must finish before another import.")
                return nil
            }
            cancel(operationID: active.id)
            guard self.active == nil else { statuses[id] = unavailable(id, "Passkey import is still cancelling."); return nil }
        }
        guard let binding = currentBinding() else { statuses[id] = unavailable(id, "Passkey import is unavailable."); return nil }
        switch journalAdmission(for: binding) {
        case .blocked: statuses[id] = unavailable(id, "Passkey receipt recovery must finish before another import."); return nil
        case .corrupt: statuses[id] = unavailable(id, "Passkey receipt recovery is unreadable and must be repaired before another import."); return nil
        case .none: return binding
        }
    }

    private func beginInventory(_ inventory: NativeVaultImportInventory, admittedID id: UUID, binding: (subject: String, generation: String)) -> UUID {
        // Re-run the public method's validation without admitting/replacing a
        // second operation. `beginFileImport` has already fenced file I/O.
        do {
            guard inventory.total == inventory.candidates.count + inventory.unsupported.count,
                  inventory.total <= maximumItems else { throw ImportFailure.refused }
            var unsupported: [Int: NativeVaultImportInventory.Unsupported] = [:]
            for item in inventory.unsupported {
                guard item.index >= 0, item.index < inventory.total, unsupported[item.index] == nil else { throw ImportFailure.refused }
                unsupported[item.index] = item
            }
            var candidate = 0
            let slots: [Slot] = try (0..<inventory.total).map { index in
                if let unsupported = unsupported[index] {
                    return Slot(id: UUID(), mutation: nil, title: unsupported.title, reason: unsupported.reason.rawValue, source: nil, disposition: .unsupported)
                }
                guard candidate < inventory.candidates.count else { throw ImportFailure.refused }
                defer { candidate += 1 }
                let item = inventory.candidates[candidate]
                return Slot(id: UUID(), mutation: UUID(), title: item.title, reason: nil, source: item.canonicalSource, disposition: .eligible)
            }
            guard candidate == inventory.candidates.count else { throw ImportFailure.refused }
            let operation = Operation(id: id, subject: binding.subject, generation: binding.generation, digest: digest(slots), slots: slots)
            active = operation
            set(operation, phase: .preview, message: "Review imported passkeys.")
        } catch {
            statuses[id] = Status(operationID: id, phase: .failed, total: 0, committed: 0, alreadyPresent: 0, unsupported: 0, failed: 0, uncertain: 0, notAttempted: 0, message: "Passkey file could not be read.", slots: [])
        }
        return id
    }

    func chooseScope(operationID: UUID, organizationID: UUID) {
        guard let operation = active, operation.id == operationID, isCurrent(operation), !operation.confirmed,
              statuses[operationID]?.phase == .preview || statuses[operationID]?.phase == .awaiting_confirmation else { return }
        operation.organization = organizationID
        set(operation, phase: .awaiting_confirmation, message: "Confirm passkey import.")
    }

    /// Confirmation accepts only the immutable native preview digest and its original binding.
    func confirm(operationID: UUID, previewDigest: String) {
        guard let operation = active, operation.id == operationID, operation.digest == previewDigest,
              operation.organization != nil, isCurrent(operation), statuses[operationID]?.phase == .awaiting_confirmation else { return }
        do {
            operation.confirmed = true
            try persist(operation)
            set(operation, phase: .importing, message: "Importing passkeys.")
            operation.task = Task { [weak self, weak operation] in
                guard let self, let operation else { return }
                await self.execute(operation)
            }
        } catch { finish(operation, phase: .failed, message: "Passkey import could not start.") }
    }

    func cancel(operationID: UUID) {
        guard let operation = active, operation.id == operationID else { return }
        operation.cancelled = true; operation.task?.cancel(); transport.cancel()
        // Nothing is durable until confirmation. Scope selection is only a
        // preview choice and cancelling it must not manufacture recovery work.
        guard operation.confirmed else {
            completeTerminal(operation, phase: .cancelled, message: "Passkey import cancelled.", persistJournal: false)
            return
        }
        startReconciliation(operation, terminal: .cancelled)
    }

    func invalidate() {
        guard let operation = active else { statuses.removeAll(); return }
        operation.cancelled = true
        operation.task?.cancel()
        transport.cancel()
        // A confirmed operation already persisted every possible write with its
        // original receipt UUID. Do not await account-bound recovery after the
        // binding has changed: retain that account's journal and release source
        // bytes synchronously so the replacement account can proceed.
        finish(operation, phase: .cancelled, message: "Passkey import cancelled.")
        statuses.removeAll()
    }
    func status(operationID: UUID) -> Status { statuses[operationID] ?? unavailable(operationID, "Passkey import is unavailable.") }
    func preview(operationID: UUID) -> (digest: String, slots: [SlotStatus])? {
        guard let operation = active, operation.id == operationID else { return nil }
        return (operation.digest, operation.slots.map { SlotStatus(slotID: $0.id, title: $0.title, disposition: $0.disposition, reason: $0.reason) })
    }

    /// Restart recovery never has candidate sources. It can only settle exact
    /// receipt UUIDs for the still-current account and scope; pending slots
    /// remain not-attempted and require the user to select the file again.
    func recoverJournal() async -> Status? {
        guard let binding = currentBinding() else { return nil }
        let recoveryURL = journalURL(for: binding.subject, generation: binding.generation)
        guard FileManager.default.fileExists(atPath: recoveryURL.path) else { return nil }
        let journal: NativeVaultImportJournal
        do { guard let loaded = try NativeVaultImportJournal.load(from: recoveryURL) else { return nil }; journal = loaded }
        catch {
            let id = UUID()
            let status = unavailable(id, "Passkey receipt recovery is unreadable and must be repaired before another import.")
            statuses.removeAll(keepingCapacity: true); statuses[id] = status
            return status
        }
        guard binding.subject == journal.subject, binding.generation == journal.generation else { return nil }
        var settled = journal
        var committed = 0, uncertain = 0, notAttempted = 0, failed = 0, unsupported = 0
        for index in settled.entries.indices {
            guard let fresh = currentBinding(), fresh.subject == journal.subject, fresh.generation == journal.generation else { return nil }
            let entry = settled.entries[index]
            switch entry.state {
            case .committed: committed += 1
            case .pending, .not_attempted: notAttempted += 1
            case .unsupported: unsupported += 1
            case .failed: failed += 1
            case .started, .uncertain:
                do {
                    guard let mutation = entry.mutationID else { return nil }
                    let receipt = try await transport.receipt(mutationID: mutation, organizationID: journal.organizationID)
                    guard let fresh = currentBinding(), fresh.subject == journal.subject, fresh.generation == journal.generation else { return nil }
                    if let receipt, receipt.mutationID == mutation { settled.entries[index].state = .committed; committed += 1 } else { uncertain += 1 }
                }
                catch { uncertain += 1 }
            }
        }
        do { try settled.save(to: recoveryURL) } catch { return nil }
        let phase: Phase = uncertain > 0 || notAttempted > 0 || failed > 0 ? .partial : .completed
        let message = phase == .completed ? "Passkey import recovery complete." : uncertain > 0 ? "Some passkeys need receipt recovery." : "Import recovery needs the original file for unattempted passkeys."
        let slots = settled.entries.map { entry in
            let disposition: Disposition
            switch entry.state { case .pending, .not_attempted: disposition = .not_attempted; case .started, .uncertain: disposition = .uncertain; case .committed: disposition = .committed; case .unsupported: disposition = .unsupported; case .failed: disposition = .failed }
            return SlotStatus(slotID: entry.slotID, title: nil, disposition: disposition, reason: entry.reason ?? (disposition == .uncertain ? "receipt_recovery" : nil))
        }
        let status = Status(operationID: journal.operationID, phase: phase, total: journal.entries.count, committed: committed, alreadyPresent: 0, unsupported: unsupported, failed: failed, uncertain: uncertain, notAttempted: notAttempted, message: message, slots: slots)
        statuses.removeAll(keepingCapacity: true); statuses[journal.operationID] = status
        return status
    }

    private func execute(_ operation: Operation) async {
        guard let organization = operation.organization, isCurrent(operation) else { teardownInactive(operation); return }
        for index in operation.slots.indices {
            guard isCurrent(operation) else { teardownInactive(operation); return }
            guard let source = operation.slots[index].source, let mutation = operation.slots[index].mutation, let title = operation.slots[index].title else { continue }
            operation.slots[index].disposition = .uncertain
            do { try persist(operation) } catch { finish(operation, phase: .failed, message: "Passkey import could not continue."); return }
            do {
                let result = try await transport.importPasskey(mutationID: mutation, source: source, label: title, organizationID: organization)
                guard isCurrent(operation) else { teardownInactive(operation); return }
                guard result.mutationID == mutation else { throw ImportFailure.refused }
                operation.slots[index].disposition = .committed; try persist(operation)
            } catch let failure as NativeVaultImportTransportFailure {
                switch failure {
                case .refused(let reason), .unavailable(let reason):
                    operation.slots[index].disposition = .failed
                    operation.slots[index].reason = reason.rawValue
                    do { try persist(operation) } catch { finish(operation, phase: .failed, message: "Passkey import could not continue."); return }
                    completeTerminal(operation, phase: .partial, message: "Some passkeys could not be imported.")
                    return
                case .cancelled, .uncertain:
                    // A request may have committed; reconciliation is receipt-only and retains the UUID.
                    startReconciliation(operation, terminal: .partial)
                    return
                }
            } catch {
                // Unknown errors never establish that the server did not commit.
                startReconciliation(operation, terminal: .partial)
                return
            }
        }
        finish(operation, phase: .completed, message: "Passkey import complete.")
    }

    private func startReconciliation(_ operation: Operation, terminal: Phase) {
        guard active === operation, !operation.reconciliationStarted else { return }
        operation.reconciliationStarted = true
        operation.task = Task { [weak self, weak operation] in
            guard let self, let operation else { return }
            await self.reconcile(operation, terminal: terminal)
        }
    }

    private func reconcile(_ operation: Operation, terminal: Phase) async {
        guard let organization = operation.organization, isCurrentOrCancelled(operation) else { teardownInactive(operation); return }
        for index in operation.slots.indices where operation.slots[index].disposition == .uncertain {
            do {
                guard let mutation = operation.slots[index].mutation else { continue }
                let receipt = try await transport.receipt(mutationID: mutation, organizationID: organization)
                guard isCurrentOrCancelled(operation) else { teardownInactive(operation); return }
                if let receipt, receipt.mutationID == mutation { operation.slots[index].disposition = .committed }
            } catch { /* Preserve uncertain; never create a replacement mutation. */ }
        }
        do { try persist(operation) } catch { finish(operation, phase: .failed, message: "Passkey receipt recovery could not be saved."); return }
        let hasUncertain = operation.slots.contains { $0.disposition == .uncertain }
        completeTerminal(operation, phase: hasUncertain ? .partial : terminal, message: hasUncertain ? "Some passkeys need receipt recovery." : terminal == .cancelled ? "Passkey import cancelled." : "Passkey import complete.")
    }

    /// Terminal statuses partition every slot. An unstarted candidate becomes
    /// explicitly not-attempted; `eligible` exists only while an operation is live.
    private func completeTerminal(_ operation: Operation, phase: Phase, message: String, persistJournal: Bool = true) {
        for index in operation.slots.indices where operation.slots[index].disposition == .eligible {
            operation.slots[index].disposition = .not_attempted
        }
        if persistJournal {
            do { try persist(operation) }
            catch { finish(operation, phase: .failed, message: "Passkey import could not continue."); return }
        }
        finish(operation, phase: phase, message: message)
    }

    private enum ImportFailure: Error { case refused, cancelled }
    private func isCurrent(_ operation: Operation) -> Bool {
        guard active === operation, !operation.cancelled, !Task.isCancelled, let binding = currentBinding() else { return false }
        return binding.subject == operation.subject && binding.generation == operation.generation
    }
    private func isCurrentOrCancelled(_ operation: Operation) -> Bool {
        guard active === operation, let binding = currentBinding() else { return false }
        return binding.subject == operation.subject && binding.generation == operation.generation
    }
    private func persist(_ operation: Operation) throws {
        guard let organization = operation.organization else { throw ImportFailure.refused }
        let entries = operation.slots.map { NativeVaultImportJournal.Entry(slotID: $0.id, mutationID: $0.mutation, state: journalState($0.disposition), reason: $0.reason) }
        try NativeVaultImportJournal(operationID: operation.id, subject: operation.subject, generation: operation.generation, organizationID: organization, previewDigest: operation.digest, entries: entries).save(to: journalURL(for: operation.subject, generation: operation.generation))
    }
    private func journalState(_ disposition: Disposition) -> NativeVaultImportJournal.Entry.State {
        switch disposition { case .eligible: return .pending; case .unsupported: return .unsupported; case .committed: return .committed; case .failed: return .failed; case .uncertain: return .uncertain; case .not_attempted: return .not_attempted }
    }
    private func digest(_ slots: [Slot]) -> String {
        var input = Data()
        for slot in slots {
            input.append(Data(slot.id.uuidString.lowercased().utf8)); input.append(0)
            input.append(Data((slot.mutation?.uuidString.lowercased() ?? "").utf8)); input.append(0)
            input.append(Data((slot.title ?? "").utf8)); input.append(0)
            input.append(Data((slot.reason ?? "").utf8)); input.append(0)
            input.append(contentsOf: SHA256.hash(data: slot.source ?? Data()))
        }
        return SHA256.hash(data: input).map { String(format: "%02x", $0) }.joined()
    }
    private func set(_ operation: Operation, phase: Phase, message: String) {
        statuses.removeAll(keepingCapacity: true)
        statuses[operation.id] = summary(operation, phase: phase, message: message)
    }
    private func finish(_ operation: Operation, phase: Phase, message: String) {
        guard active === operation else { return }
        statuses.removeAll(keepingCapacity: true); statuses[operation.id] = summary(operation, phase: phase, message: message)
        // Release retained references; this does not claim to scrub Foundation copies.
        for index in operation.slots.indices { operation.slots[index].source = nil }
        active = nil
    }
    private func teardownInactive(_ operation: Operation) {
        guard active === operation else { return }
        operation.cancelled = true
        operation.task?.cancel()
        transport.cancel()
        completeTerminal(operation, phase: .cancelled, message: "Passkey import cancelled.", persistJournal: false)
    }
    private func summary(_ operation: Operation, phase: Phase, message: String) -> Status {
        let slots = operation.slots
        return Status(operationID: operation.id, phase: phase, total: slots.count, committed: slots.filter { $0.disposition == .committed }.count, alreadyPresent: 0, unsupported: slots.filter { $0.disposition == .unsupported }.count, failed: slots.filter { $0.disposition == .failed }.count, uncertain: slots.filter { $0.disposition == .uncertain }.count, notAttempted: slots.filter { $0.disposition == .not_attempted || $0.disposition == .eligible }.count, message: message, slots: slots.map { SlotStatus(slotID: $0.id, title: $0.title, disposition: $0.disposition, reason: $0.reason) })
    }
    private func unavailable(_ id: UUID, _ message: String) -> Status { Status(operationID: id, phase: .unavailable, total: 0, committed: 0, alreadyPresent: 0, unsupported: 0, failed: 0, uncertain: 0, notAttempted: 0, message: message, slots: []) }
}
