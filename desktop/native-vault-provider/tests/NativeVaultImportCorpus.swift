import Foundation

@available(macOS 26.0, *)
private struct Parser: NativeVaultImportParsing {
    let inventory: NativeVaultImportInventory
    func parseCXFv1(_ bytes: Data) throws -> NativeVaultImportInventory {
        precondition(bytes == Data("private-file".utf8))
        return inventory
    }
}

@available(macOS 26.0, *)
private struct RejectingParser: NativeVaultImportParsing {
    func parseCXFv1(_ bytes: Data) throws -> NativeVaultImportInventory { throw CocoaError(.fileReadCorruptFile) }
}

@available(macOS 26.0, *)
private final class Server: NativeVaultImportTransporting {
    enum WriteOutcome { case success, timeoutAfterWrite, refused }
    var writes: [(UUID, Data, String, UUID)] = []
    var receipts: Set<UUID> = []
    var writeOutcome: WriteOutcome = .success
    var wrongMutation = false
    var holdResponse = false
    var holdReceipt = false
    var receiptCalls = 0
    func cancel() {}
    func importPasskey(mutationID: UUID, source: Data, label: String, organizationID: UUID) async throws -> NativeVaultImportTransportResult {
        writes.append((mutationID, source, label, organizationID))
        while holdResponse { try await Task.sleep(nanoseconds: 1_000_000) }
        switch writeOutcome {
        case .success: break
        case .timeoutAfterWrite: receipts.insert(mutationID); throw URLError(.timedOut)
        case .refused: throw NativeVaultImportTransportFailure.refused(reason: .import_unavailable)
        }
        return NativeVaultImportTransportResult(mutationID: wrongMutation ? UUID() : mutationID)
    }
    func receipt(mutationID: UUID, organizationID: UUID) async throws -> NativeVaultImportTransportResult? {
        receiptCalls += 1
        while holdReceipt { try await Task.sleep(nanoseconds: 1_000_000) }
        return receipts.contains(mutationID) ? NativeVaultImportTransportResult(mutationID: mutationID) : nil
    }
}

@available(macOS 26.0, *)
@MainActor private func wait(_ predicate: @escaping () -> Bool) async {
    for _ in 0..<100 { if predicate() { return }; try? await Task.sleep(nanoseconds: 5_000_000) }
    preconditionFailure("import controller did not settle")
}

@available(macOS 26.0, *)
@main struct NativeVaultImportCorpus {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let journal = root.appendingPathComponent("import-recovery.json")
        let boundJournal = NativeVaultImportJournal.url(base: journal, subject: "subject", generation: "generation")
        let one = NativeVaultImportInventory.Candidate(title: "Example passkey", canonicalSource: Data("private-source-one".utf8))
        let two = NativeVaultImportInventory.Candidate(title: "Unsupported only counts", canonicalSource: Data("private-source-two".utf8))
        let unsupported = NativeVaultImportInventory.Unsupported(index: 1, title: "Unsupported item", reason: .unsupported_credential)
        let parser = Parser(inventory: .init(total: 3, candidates: [one, two], unsupported: [unsupported]))
        let binding = (subject: "subject", generation: "generation")
        let server = Server()
        let controller = NativeVaultImportController(parser: parser, transport: server, journalURL: journal, currentBinding: { binding })

        let operation = controller.beginInventory(parser.inventory)
        guard let preview = controller.preview(operationID: operation) else { preconditionFailure("missing native preview: \(controller.status(operationID: operation))") }
        precondition(preview.slots.count == 3 && preview.slots[1].title == "Unsupported item" && preview.slots[1].reason == "unsupported_credential" && preview.slots[1].disposition == .unsupported)
        // A parsing failure returns the newly-created failed operation, never a prior preview ID.
        let rejected = NativeVaultImportController(parser: RejectingParser(), transport: server, journalURL: root.appendingPathComponent("rejected.json"), currentBinding: { binding })
        let rejectedID = rejected.beginFileImport { Data("private-file".utf8) }
        precondition(rejected.status(operationID: rejectedID).phase == .failed)
        // The configured product cap covers every supplied item, including
        // unsupported/mixed items that will never be written.
        let overLimitUnsupported = (0...200).map {
            NativeVaultImportInventory.Unsupported(index: $0, title: "Unsupported \($0)", reason: .mixed_or_multiple_credentials)
        }
        let overLimit = NativeVaultImportController(parser: Parser(inventory: .init(total: 201, candidates: [], unsupported: overLimitUnsupported)), transport: Server(), journalURL: root.appendingPathComponent("over-limit.json"), maximumItems: 200, currentBinding: { binding })
        let overLimitID = overLimit.beginInventory(.init(total: 201, candidates: [], unsupported: overLimitUnsupported))
        precondition(overLimit.status(operationID: overLimitID).phase == .failed && overLimit.preview(operationID: overLimitID) == nil, "unsupported items bypassed configured import limit")
        let selectedOrganization = UUID()
        controller.chooseScope(operationID: operation, organizationID: selectedOrganization)
        precondition(controller.status(operationID: operation).phase == .awaiting_confirmation)
        // Wrong renderer digest cannot start an import or journal it.
        controller.confirm(operationID: operation, previewDigest: "replacement")
        precondition(server.writes.isEmpty && !FileManager.default.fileExists(atPath: journal.path))
        controller.confirm(operationID: operation, previewDigest: preview.digest)
        controller.chooseScope(operationID: operation, organizationID: UUID())
        await wait { controller.status(operationID: operation).phase == .completed }
        let complete = controller.status(operationID: operation)
        precondition(complete.total == complete.committed + complete.alreadyPresent + complete.unsupported + complete.failed + complete.uncertain + complete.notAttempted)
        precondition(complete.committed == 2 && complete.unsupported == 1 && server.writes.count == 2)
        precondition(server.writes.map(\.2) == ["Example passkey", "Unsupported only counts"], "unsupported input index changed eligible write order")
        precondition(server.writes.allSatisfy { $0.3 == selectedOrganization }, "scope changed after confirmation")
        let saved = try NativeVaultImportJournal.load(from: boundJournal)
        precondition(saved?.entries.count == 3 && saved?.entries.filter { $0.state == .committed }.count == 2 && saved?.entries[1].reason == "unsupported_credential")
        let rawJournal = try String(contentsOf: boundJournal, encoding: .utf8)
        precondition(!rawJournal.contains("private-source") && !rawJournal.contains("private-file") && !rawJournal.contains("token") && !rawJournal.contains("Unsupported item"))
        let restarted = NativeVaultImportController(parser: parser, transport: server, journalURL: journal, currentBinding: { binding })
        let restartStatus = await restarted.recoverJournal()
        precondition(restartStatus?.phase == .completed && restartStatus?.slots[1].title == nil && restartStatus?.slots[1].reason == "unsupported_credential")

        // Cancellation before scope/confirmation cannot write or create a journal.
        let cancelledServer = Server(); let cancelledJournal = root.appendingPathComponent("cancelled.json")
        let cancelled = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: cancelledServer, journalURL: cancelledJournal, currentBinding: { binding })
        let cancelledID = cancelled.beginFileImport { Data("private-file".utf8) }
        cancelled.cancel(operationID: cancelledID)
        precondition(cancelled.status(operationID: cancelledID).phase == .cancelled && cancelledServer.writes.isEmpty)
        precondition(!FileManager.default.fileExists(atPath: NativeVaultImportJournal.url(base: cancelledJournal, subject: binding.subject, generation: binding.generation).path))

        // Scope selection still authorizes no mutation. Cancelling before confirm
        // must not manufacture a journal that later blocks another import.
        let scopedJournal = root.appendingPathComponent("scoped-cancel.json")
        let scoped = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: Server(), journalURL: scopedJournal, currentBinding: { binding })
        let scopedID = scoped.beginFileImport { Data("private-file".utf8) }
        scoped.chooseScope(operationID: scopedID, organizationID: UUID())
        scoped.cancel(operationID: scopedID)
        precondition(scoped.status(operationID: scopedID).phase == .cancelled && !FileManager.default.fileExists(atPath: NativeVaultImportJournal.url(base: scopedJournal, subject: binding.subject, generation: binding.generation).path), "cancel-before-confirm created recovery journal")

        // A response timeout reconciles the original mutation receipt and never creates another UUID.
        let uncertainJournal = root.appendingPathComponent("uncertain.json")
        let uncertainServer = Server(); uncertainServer.writeOutcome = .timeoutAfterWrite
        let uncertainController = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: uncertainServer, journalURL: uncertainJournal, currentBinding: { binding })
        let uncertain = uncertainController.beginFileImport { Data("private-file".utf8) }
        let uncertainPreview = uncertainController.preview(operationID: uncertain)!
        let originalMutation = try! NativeVaultImportJournal.load(from: uncertainJournal) // no journal before confirmation
        precondition(originalMutation == nil)
        uncertainController.chooseScope(operationID: uncertain, organizationID: UUID())
        uncertainController.confirm(operationID: uncertain, previewDigest: uncertainPreview.digest)
        await wait { uncertainServer.writes.count == 1 }
        await wait { uncertainController.status(operationID: uncertain).phase == .partial }
        let recovered = try NativeVaultImportJournal.load(from: NativeVaultImportJournal.url(base: uncertainJournal, subject: binding.subject, generation: binding.generation))!
        precondition(recovered.entries[0].mutationID == uncertainServer.writes[0].0 && recovered.entries[0].state == .committed)

        // A validated refusal has a fixed failed disposition and does not issue
        // a receipt read: the transport, not the controller, owns certainty.
        let refusedServer = Server(); refusedServer.writeOutcome = .refused
        let refusedJournal = root.appendingPathComponent("refused.json")
        let refused = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: refusedServer, journalURL: refusedJournal, currentBinding: { binding })
        let refusedID = refused.beginInventory(.init(total: 1, candidates: [one], unsupported: []))
        let refusedPreview = refused.preview(operationID: refusedID)!
        refused.chooseScope(operationID: refusedID, organizationID: UUID()); refused.confirm(operationID: refusedID, previewDigest: refusedPreview.digest)
        await wait { refused.status(operationID: refusedID).phase == .partial }
        let refusalStatus = refused.status(operationID: refusedID)
        precondition(refusalStatus.failed == 1 && refusalStatus.uncertain == 0 && refusalStatus.slots[0].reason == "import_unavailable" && refusedServer.receiptCalls == 0, "definite refusal became uncertain")

        // A malformed receipt/write acknowledgement cannot mark a different mutation committed.
        let mismatchServer = Server(); mismatchServer.wrongMutation = true
        let mismatch = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: mismatchServer, journalURL: root.appendingPathComponent("mismatch.json"), currentBinding: { binding })
        let mismatchID = mismatch.beginFileImport { Data("private-file".utf8) }
        let mismatchPreview = mismatch.preview(operationID: mismatchID)!
        mismatch.chooseScope(operationID: mismatchID, organizationID: UUID()); mismatch.confirm(operationID: mismatchID, previewDigest: mismatchPreview.digest)
        await wait { mismatch.status(operationID: mismatchID).phase == .partial }
        precondition(mismatch.status(operationID: mismatchID).committed == 0 && mismatch.status(operationID: mismatchID).uncertain == 1)

        // An unresolved durable receipt blocks a replacement before file parsing or journal overwrite.
        let blockedJournal = root.appendingPathComponent("blocked.json")
        let blockedMutation = UUID()
        try NativeVaultImportJournal(operationID: UUID(), subject: binding.subject, generation: binding.generation, organizationID: UUID(), previewDigest: "digest", entries: [.init(slotID: UUID(), mutationID: blockedMutation, state: .uncertain, reason: nil)]).save(to: NativeVaultImportJournal.url(base: blockedJournal, subject: binding.subject, generation: binding.generation))
        let blockedServer = Server()
        let blocked = NativeVaultImportController(parser: parser, transport: blockedServer, journalURL: blockedJournal, currentBinding: { binding })
        let blockedID = blocked.beginFileImport { preconditionFailure("blocked recovery must not read another file") }
        precondition(blocked.status(operationID: blockedID).phase == .unavailable && blockedServer.writes.isEmpty)

        // Unreadable recovery state is never treated as absent and cannot be
        // overwritten under the same account binding.
        let corruptJournal = root.appendingPathComponent("corrupt.json")
        let corruptBound = NativeVaultImportJournal.url(base: corruptJournal, subject: binding.subject, generation: binding.generation)
        try Data("not-json".utf8).write(to: corruptBound)
        let corrupt = NativeVaultImportController(parser: parser, transport: Server(), journalURL: corruptJournal, currentBinding: { binding })
        let corruptID = corrupt.beginFileImport { preconditionFailure("corrupt recovery journal failed open") }
        precondition(corrupt.status(operationID: corruptID).phase == .unavailable)

        // A durable uncertainty belongs to its original account, but it must
        // not indefinitely block an unrelated replacement account.
        let replacementBinding = (subject: "replacement", generation: "new-generation")
        let crossAccount = NativeVaultImportController(parser: parser, transport: Server(), journalURL: blockedJournal, currentBinding: { replacementBinding })
        let crossAccountID = crossAccount.beginFileImport { Data("private-file".utf8) }
        precondition(crossAccount.status(operationID: crossAccountID).phase == .preview)

        // Account/generation replacement fences the queued write before it can hit the server.
        var mutableBinding = binding
        let changedServer = Server()
        let changed = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: changedServer, journalURL: root.appendingPathComponent("changed.json"), currentBinding: { mutableBinding })
        let changedID = changed.beginFileImport { Data("private-file".utf8) }
        let changedPreview = changed.preview(operationID: changedID)!
        changed.chooseScope(operationID: changedID, organizationID: UUID())
        mutableBinding = (subject: "replacement", generation: "new-generation")
        changed.confirm(operationID: changedID, previewDigest: changedPreview.digest)
        precondition(changedServer.writes.isEmpty)

        // A binding change after a write began releases the old operation. The
        // replacement gets a fresh immutable controller and transport; the old
        // cancelled transport is never reused for the replacement account.
        var delayedBinding = binding
        let delayedServer = Server(); delayedServer.holdResponse = true
        let delayedJournal = root.appendingPathComponent("delayed.json")
        let delayed = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [one], unsupported: [])), transport: delayedServer, journalURL: delayedJournal, currentBinding: { delayedBinding })
        let delayedID = delayed.beginFileImport { Data("private-file".utf8) }
        let delayedPreview = delayed.preview(operationID: delayedID)!
        delayed.chooseScope(operationID: delayedID, organizationID: UUID())
        delayed.confirm(operationID: delayedID, previewDigest: delayedPreview.digest)
        await wait { delayedServer.writes.count == 1 }
        delayedBinding = replacementBinding
        delayed.invalidate()
        let replacementServer = Server()
        let replacementController = NativeVaultImportController(parser: Parser(inventory: .init(total: 1, candidates: [two], unsupported: [])), transport: replacementServer, journalURL: delayedJournal, currentBinding: { delayedBinding })
        let replacementID = replacementController.beginInventory(.init(total: 1, candidates: [two], unsupported: []))
        let replacementPreview = replacementController.preview(operationID: replacementID)!
        let replacementScope = UUID()
        replacementController.chooseScope(operationID: replacementID, organizationID: replacementScope)
        replacementController.confirm(operationID: replacementID, previewDigest: replacementPreview.digest)
        await wait { replacementController.status(operationID: replacementID).phase == .completed }
        precondition(replacementServer.writes.count == 1 && replacementServer.writes[0].1 == two.canonicalSource && replacementServer.writes[0].3 == replacementScope, "replacement reused old transport or source")
        delayedServer.holdResponse = false

        // Cancelling an admitted write starts exactly one receipt reconciliation
        // epoch and terminalizes untouched candidates as not-attempted.
        let overlapServer = Server(); overlapServer.holdResponse = true; overlapServer.holdReceipt = true
        let overlap = NativeVaultImportController(parser: Parser(inventory: .init(total: 2, candidates: [one, two], unsupported: [])), transport: overlapServer, journalURL: root.appendingPathComponent("overlap.json"), currentBinding: { binding })
        let overlapID = overlap.beginInventory(.init(total: 2, candidates: [one, two], unsupported: []))
        let overlapPreview = overlap.preview(operationID: overlapID)!
        overlap.chooseScope(operationID: overlapID, organizationID: UUID()); overlap.confirm(operationID: overlapID, previewDigest: overlapPreview.digest)
        await wait { overlapServer.writes.count == 1 }
        overlap.cancel(operationID: overlapID)
        await wait { overlapServer.receiptCalls == 1 }
        overlapServer.holdResponse = false; overlapServer.holdReceipt = false
        await wait { overlap.status(operationID: overlapID).phase == .partial }
        let overlapStatus = overlap.status(operationID: overlapID)
        precondition(overlapServer.receiptCalls == 1 && overlapStatus.uncertain == 1 && overlapStatus.notAttempted == 1 && overlapStatus.slots[1].disposition == .not_attempted, "terminal cancel must have one reconciler and no eligible slots")

        print("PASS native import lifecycle: immutable preview, value-free journal, sequential receipt recovery and generation fence")
    }
}
