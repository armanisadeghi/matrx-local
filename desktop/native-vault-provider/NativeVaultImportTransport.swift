import Foundation

/// Containing-process-only transport. The grant and scope map come from native
/// authentication, never an IPC request. Every write and recovery read rechecks
/// that binding; cancellation stops writes but permits receipt reconciliation.
final class NativeVaultImportTransport: NativeVaultImportTransporting {
    private let grant: NativeVaultSessionAccess.Grant
    private let lifetime: NativeVaultRequestLifetime
    private let sessionAccess: NativeVaultSessionAccess
    private let transport: NativeVaultPasskeyTransporting
    private let principalByScope: [UUID: String]
    private let current: () -> Bool
    private let lock = NSLock()
    private var writesCancelled = false

    init(grant: NativeVaultSessionAccess.Grant, lifetime: NativeVaultRequestLifetime,
         sessionAccess: NativeVaultSessionAccess, principalByScope: [UUID: String],
         transport: NativeVaultPasskeyTransporting = NativeVaultPasskeyTransport(),
         current: @escaping () -> Bool) {
        self.grant = grant; self.lifetime = lifetime; self.sessionAccess = sessionAccess
        self.principalByScope = principalByScope; self.transport = transport; self.current = current
    }

    func cancel() {
        lock.lock(); writesCancelled = true; lock.unlock()
        transport.cancel()
    }

    private func mayWrite() -> Bool {
        lock.lock(); defer { lock.unlock() }; return !writesCancelled
    }

    func importPasskey(mutationID: UUID, source: Data, label: String, organizationID: UUID) async throws -> NativeVaultImportTransportResult {
        guard mayWrite(), !Task.isCancelled else { throw NativeVaultImportTransportFailure.cancelled }
        guard source.count > 0, source.count <= 65_536,
              !label.isEmpty, label.utf8.count <= 1_024,
              let principal = principalByScope[organizationID], ["user", "organization"].contains(principal)
        else { throw NativeVaultImportTransportFailure.refused(reason: .import_unavailable) }
        let body = try JSONSerialization.data(withJSONObject: [
            "mutation_id": mutationID.uuidString.lowercased(), "source": NativeVaultPasskeyCodec.base64url(source),
            "label": label, "principal_type": principal, "format_version": "cxf1.0"
        ], options: [.sortedKeys])
        guard body.count <= 96 * 1_024 else { throw NativeVaultImportTransportFailure.refused(reason: .import_unavailable) }
        let (data, response) = try await request("api/vault/native/passkeys/import", method: "POST", body: body, organizationID: organizationID, writing: true)
        guard response.statusCode == 200 else { throw importFailure(for: response.statusCode) }
        do { _ = try NativeVaultPasskeyCodec.receipt(data, mutationID: mutationID.uuidString.lowercased(), source: source) }
        catch { throw NativeVaultImportTransportFailure.uncertain }
        return NativeVaultImportTransportResult(mutationID: mutationID)
    }

    func receipt(mutationID: UUID, organizationID: UUID) async throws -> NativeVaultImportTransportResult? {
        let (data, response) = try await request("api/vault/native/passkeys/import/receipts/\(mutationID.uuidString.lowercased())", method: "GET", body: nil, organizationID: organizationID, writing: false)
        if response.statusCode == 404 { return nil }
        guard response.statusCode == 200 else { throw receiptFailure(for: response.statusCode) }
        let value = try StrictEnvelope.object(data, required: ["mutation_id", "item_id", "field_id", "passkey_id", "source_sha256", "status"], optional: [])
        guard case .string(mutationID.uuidString.lowercased())? = value["mutation_id"],
              case .string("saved_waiting_for_site")? = value["status"] else { throw NativeVaultImportTransportFailure.uncertain }
        for key in ["item_id", "field_id", "passkey_id"] {
            guard case let .string(id)? = value[key], id.canonicalUUID else { throw NativeVaultImportTransportFailure.uncertain }
        }
        guard try NativeVaultPasskeyCodec.bytes(value["source_sha256"], minimum: 32, maximum: 32).count == 32 else { throw NativeVaultImportTransportFailure.uncertain }
        return NativeVaultImportTransportResult(mutationID: mutationID)
    }

    private func request(_ path: String, method: String, body: Data?, organizationID: UUID, writing: Bool) async throws -> (Data, HTTPURLResponse) {
        guard current(), lifetime.isCurrent, principalByScope[organizationID] != nil,
              !writing || mayWrite() else { throw NativeVaultImportTransportFailure.cancelled }
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent(path))
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 10
        request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue(organizationID.uuidString.lowercased(), forHTTPHeaderField: "X-Organization-Id")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let raw: Result<(Data, HTTPURLResponse), Error> = await withCheckedContinuation { continuation in
            // Admission and registration with the cancellable transport are one
            // critical section: cancel cannot miss a write admitted just before it.
            lock.lock()
            if writing && writesCancelled {
                lock.unlock(); continuation.resume(returning: .failure(NativeVaultImportTransportFailure.cancelled)); return
            }
            transport.send(request) { continuation.resume(returning: $0) }
            lock.unlock()
        }
        let settled: Result<(Data, HTTPURLResponse), Error> = await withCheckedContinuation { continuation in
            sessionAccess.reconcileResponse(raw, grant: grant, lifetime: lifetime) { continuation.resume(returning: $0) }
        }
        guard current(), lifetime.isCurrent else { throw NativeVaultImportTransportFailure.cancelled }
        do { return try settled.get() }
        catch let failure as NativeVaultImportTransportFailure { throw failure }
        catch { throw NativeVaultImportTransportFailure.uncertain }
    }

    private func importFailure(for status: Int) -> NativeVaultImportTransportFailure {
        switch status {
        case 400, 404, 409, 422: return .refused(reason: .import_unavailable)
        case 401, 403: return .unavailable(reason: .import_unavailable)
        default: return .uncertain
        }
    }

    private func receiptFailure(for status: Int) -> NativeVaultImportTransportFailure {
        // A receipt lookup never writes. Its failure cannot settle a possibly
        // admitted write, so keep the controller's original slot uncertain.
        _ = status
        return .uncertain
    }
}
