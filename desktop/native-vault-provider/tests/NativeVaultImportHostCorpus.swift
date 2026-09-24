import AppKit
import CryptoKit
import Foundation

@available(macOS 26.0, *)
private final class HostWire: NativeVaultPasskeyTransporting {
    let organization: UUID
    init(organization: UUID) { self.organization = organization }
    func cancel() {}
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let path = request.url?.path ?? ""
        let body: [String: Any]
        if path == "/api/auth/organizations" {
            // org-default-exempt: the strict synthetic server envelope names this inert field; host never decodes it as scope authority.
            body = ["authenticated": true, "user_id": "host-subject", "organizations": [["id": organization.uuidString.lowercased(), "name": "Host corpus", "is_personal": false, "abbreviation": "HC"]], "default_organization_id": NSNull(), "default_preference_status": "unset", "warnings": [], "missing_organization_count": 0]
        } else if path == "/api/vault/native/passkeys/import/capabilities" {
            body = ["protocol_version": 1, "activation_revision": 1, "max_source_bytes": 65_536, "max_request_body_bytes": 98_304, "max_transfer_items": 200, "max_file_bytes": 16_777_216]
        } else {
            completion(.failure(URLError(.badURL))); return
        }
        let data = try! JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        completion(.success((data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)))
    }
}

@available(macOS 26.0, *)
private final class HostTransport: NativeVaultImportTransporting {
    enum Outcome { case success, timeoutAfterWrite }
    var outcome: Outcome = .success
    var holdWrite = false
    var cancelled = false
    var receiptAvailable = false
    var writes: [(UUID, Data, String, UUID)] = []
    var receiptCalls = 0
    func cancel() { cancelled = true }
    func importPasskey(mutationID: UUID, source: Data, label: String, organizationID: UUID) async throws -> NativeVaultImportTransportResult {
        writes.append((mutationID, source, label, organizationID))
        while holdWrite { try await Task.sleep(nanoseconds: 1_000_000) }
        if cancelled { throw NativeVaultImportTransportFailure.cancelled }
        if outcome == .timeoutAfterWrite { throw URLError(.timedOut) }
        return .init(mutationID: mutationID)
    }
    func receipt(mutationID: UUID, organizationID: UUID) async throws -> NativeVaultImportTransportResult? {
        receiptCalls += 1
        return receiptAvailable ? .init(mutationID: mutationID) : nil
    }
}

@available(macOS 26.0, *)
private final class HostTransportStore {
    var transports: [HostTransport] = []
    var nextOutcome: HostTransport.Outcome = .success
    var nextHoldWrite = false
    var receiptAvailable = false
    func make() -> HostTransport {
        let transport = HostTransport()
        transport.outcome = nextOutcome; transport.holdWrite = nextHoldWrite; transport.receiptAvailable = receiptAvailable
        transports.append(transport)
        return transport
    }
    var writes: [(UUID, Data, String, UUID)] { transports.flatMap(\.writes) }
}

/// Drives the production import transport through the same host cancellation
/// path. Cancelling its admitted write releases an ambiguous response; exact
/// receipt lookup remains available and contains no source material.
@available(macOS 26.0, *)
private final class HostProductionImportWire: NativeVaultPasskeyTransporting {
    private var pendingWrite: ((Result<(Data, HTTPURLResponse), Error>) -> Void)?
    var writes = 0
    var receiptCalls = 0
    var receiptAdmission: (() -> Bool)?
    var receiptWasCurrent = false
    func cancel() {
        let completion = pendingWrite
        pendingWrite = nil
        completion?(.failure(URLError(.cancelled)))
    }
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let path = request.url?.path ?? ""
        if path == "/api/vault/native/passkeys/import" {
            writes += 1
            pendingWrite = completion
            return
        }
        guard path.contains("/api/vault/native/passkeys/import/receipts/"),
              let mutation = path.split(separator: "/").last,
              let mutationID = UUID(uuidString: String(mutation)) else {
            completion(.failure(URLError(.badURL))); return
        }
        receiptCalls += 1
        receiptWasCurrent = receiptAdmission?() ?? false
        let value: [String: Any] = [
            "mutation_id": mutationID.uuidString.lowercased(),
            "item_id": UUID().uuidString.lowercased(),
            "field_id": UUID().uuidString.lowercased(),
            "passkey_id": UUID().uuidString.lowercased(),
            "source_sha256": Data(repeating: 7, count: 32).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: ""),
            "status": "saved_waiting_for_site"
        ]
        let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        completion(.success((data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)))
    }
}

@available(macOS 26.0, *)
@MainActor private func waitFor(_ label: String, _ condition: @escaping () -> Bool) async {
    for _ in 0..<400 {
        if condition() { return }
        try? await Task.sleep(nanoseconds: 2_000_000)
    }
    preconditionFailure("timed out waiting for \(label)")
}

@available(macOS 26.0, *)
private func cxfFile(at url: URL, titles: [String]) throws {
    func b64(_ value: Data) -> String { value.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
    let accounts: [[String: Any]] = try titles.enumerated().map { index, title in
        let key = try P256.Signing.PrivateKey(rawRepresentation: Data(repeating: UInt8(index + 1), count: 32))
        let credential: [String: Any] = ["type": "passkey", "credentialId": b64(Data(repeating: UInt8(index + 11), count: 16)), "rpId": "example.com", "username": "host-corpus-\(index)", "userDisplayName": "Host corpus", "userHandle": b64(Data("host-\(index)".utf8)), "key": b64(key.derRepresentation)]
        return ["id": b64(Data("account-\(index)".utf8)), "username": "", "email": "", "collections": [], "items": [["id": b64(Data("item-\(index)".utf8)), "title": title, "credentials": [credential]]]]
    }
    let fixture: [String: Any] = ["version": ["major": 1, "minor": 0], "exporterRpId": "example.com", "exporterDisplayName": "Host corpus", "timestamp": 0, "accounts": accounts]
    try JSONSerialization.data(withJSONObject: fixture).write(to: url, options: .atomic)
}

@available(macOS 26.0, *)
@main struct NativeVaultImportHostCorpus {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("native-vault-import-host-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let organization = UUID()
        let file = root.appendingPathComponent("valid.cxf")
        let twoFile = root.appendingPathComponent("two-valid.cxf")
        try cxfFile(at: file, titles: ["Host import passkey"])
        try cxfFile(at: twoFile, titles: ["Cancelled first", "Cancelled second"])
        let grant = NativeVaultSessionAccess.Grant(accessToken: "host-token", subject: "host-subject", generation: "host-generation")
        let anchor = NSWindow(contentRect: .init(x: 0, y: 0, width: 1, height: 1), styleMask: [.titled], backing: .buffered, defer: false)

        func host(file selected: URL, journal: URL, store: HostTransportStore, current: @escaping () -> Bool = { true }) -> NativeVaultImportHost {
            NativeVaultImportHost(presentationAnchor: anchor, key: { "host-key" }, choose: { selected }, authentication: { _, _ in grant }, currentGrant: { _ in current() }, wire: HostWire(organization: organization), transportFactory: { _, _, _, _, _ in store.make() }, journalDirectory: journal)
        }

        // A real CXF file must pass OS read + Rust parser before controller preview; wrong scope/digest cannot write.
        do {
            let store = HostTransportStore(); let subject = host(file: file, journal: root.appendingPathComponent("positive"), store: store)
            let id = subject.begin(organizationID: organization)
            await waitFor("real parser preview") { subject.status(id).phase == "preview" }
            guard let preview = subject.preview(id, offset: 0) else { preconditionFailure("missing host preview") }
            precondition(preview.total == 1 && preview.slots.first?.title == "Host import passkey")
            subject.chooseScope(id, organizationID: UUID()); subject.confirm(id, digest: preview.preview_digest)
            precondition(store.writes.isEmpty, "wrong scope wrote before authorization")
            subject.chooseScope(id, organizationID: organization); subject.confirm(id, digest: "wrong-digest")
            precondition(store.writes.isEmpty, "wrong digest wrote before confirmation")
            subject.confirm(id, digest: preview.preview_digest)
            await waitFor("completed import") { subject.status(id).phase == "completed" }
            let status = subject.status(id)
            precondition(status.committed == 1 && status.total == 1 && store.writes.count == 1 && store.writes[0].3 == organization)
        }

        // Cancellation after one admitted write must account for the in-flight item and terminalize untouched items.
        do {
            let store = HostTransportStore(); store.nextHoldWrite = true
            let subject = host(file: twoFile, journal: root.appendingPathComponent("cancel"), store: store)
            let id = subject.begin(organizationID: organization)
            await waitFor("cancellation preview") { subject.status(id).phase == "preview" }
            let digest = subject.preview(id, offset: 0)!.preview_digest
            subject.chooseScope(id, organizationID: organization); subject.confirm(id, digest: digest)
            await waitFor("admitted write") { store.writes.count == 1 }
            subject.cancel(id); store.transports[0].holdWrite = false
            await waitFor("cancelled accounting") { ["partial", "cancelled", "failed"].contains(subject.status(id).phase) }
            let status = subject.status(id)
            precondition(status.phase == "partial" && status.uncertain == 1 && status.not_attempted == 1 && status.total == status.committed + status.already_present + status.unsupported + status.failed + status.uncertain + status.not_attempted, "cancellation accounting was \(status.phase): uncertain=\(status.uncertain), not_attempted=\(status.not_attempted)")
        }

        // Host cancellation must retain the production transport's operation
        // lifetime for one same-account receipt-only reconciliation. A second
        // candidate must never be replayed after the admitted first write.
        do {
            let productionWire = HostProductionImportWire()
            var productionLifetime: NativeVaultRequestLifetime?
            let subject = NativeVaultImportHost(
                presentationAnchor: anchor,
                key: { "host-key" },
                choose: { twoFile },
                authentication: { _, _ in grant },
                currentGrant: { _ in true },
                wire: HostWire(organization: organization),
                transportFactory: { grant, lifetime, access, scopes, current in
                    productionLifetime = lifetime
                    productionWire.receiptAdmission = { productionLifetime?.isCurrent == true }
                    return NativeVaultImportTransport(grant: grant, lifetime: lifetime, sessionAccess: access, principalByScope: scopes, transport: productionWire, current: current)
                },
                journalDirectory: root.appendingPathComponent("production-cancel"))
            let id = subject.begin(organizationID: organization)
            await waitFor("production cancellation preview") { subject.status(id).phase == "preview" }
            let digest = subject.preview(id, offset: 0)!.preview_digest
            subject.chooseScope(id, organizationID: organization); subject.confirm(id, digest: digest)
            await waitFor("production admitted write") { productionWire.writes == 1 }
            subject.cancel(id)
            await waitFor("production receipt reconciliation") {
                let status = subject.status(id)
                return status.phase == "cancelled" && status.committed == 1 && status.not_attempted == 1
            }
            precondition(productionWire.writes == 1 && productionWire.receiptCalls == 1 && productionWire.receiptWasCurrent && productionLifetime?.isCurrent == false, "host cancellation replayed a write, blocked its same-operation receipt, or retained its terminal lifetime")
        }

        // Recovery must read the original mutation receipt and never reissue its write.
        do {
            let journal = root.appendingPathComponent("recovery")
            let store = HostTransportStore(); store.nextOutcome = .timeoutAfterWrite
            let subject = host(file: file, journal: journal, store: store)
            let id = subject.begin(organizationID: organization)
            await waitFor("uncertain preview") { subject.status(id).phase == "preview" }
            let digest = subject.preview(id, offset: 0)!.preview_digest
            subject.chooseScope(id, organizationID: organization); subject.confirm(id, digest: digest)
            await waitFor("uncertain terminal") { subject.status(id).phase == "partial" }
            precondition(store.writes.count == 1 && subject.blocksReplacement(), "actual uncertain controller did not block replacement")
            store.receiptAvailable = true
            let recovered = subject.recover()
            await waitFor("receipt recovery") { subject.status(recovered).phase == "completed" }
            precondition(store.writes.count == 1 && store.transports.last!.receiptCalls == 1 && !subject.blocksReplacement(), "recovery issued a replacement write or did not settle")
        }

        // A generation change after preview is refused by the controller binding before its first mutation.
        do {
            var current = true; let store = HostTransportStore()
            let subject = host(file: file, journal: root.appendingPathComponent("stale"), store: store, current: { current })
            let id = subject.begin(organizationID: organization)
            await waitFor("stale preview") { subject.status(id).phase == "preview" }
            let digest = subject.preview(id, offset: 0)!.preview_digest
            subject.chooseScope(id, organizationID: organization); current = false; subject.confirm(id, digest: digest)
            try? await Task.sleep(nanoseconds: 10_000_000)
            precondition(store.writes.isEmpty && subject.status(id).phase == "awaiting_confirmation", "stale grant admitted a write")
        }

        // blocksReplacement follows the real controller while importing, then clears once the write settles.
        do {
            let store = HostTransportStore(); store.nextHoldWrite = true
            let subject = host(file: file, journal: root.appendingPathComponent("blocking"), store: store)
            let id = subject.begin(organizationID: organization)
            await waitFor("blocking preview") { subject.status(id).phase == "preview" }
            let digest = subject.preview(id, offset: 0)!.preview_digest
            subject.chooseScope(id, organizationID: organization); subject.confirm(id, digest: digest)
            await waitFor("blocking admitted write") { store.writes.count == 1 }
            precondition(subject.blocksReplacement(), "host did not report live controller importing state")
            store.transports[0].holdWrite = false
            await waitFor("blocking completion") { subject.status(id).phase == "completed" }
            precondition(!subject.blocksReplacement(), "host kept replacement blocked after completed controller")
        }
        print("PASS native import host: real CXF parser/file/journal lifecycle, recovery, generation fence and replacement accounting")
    }
}
