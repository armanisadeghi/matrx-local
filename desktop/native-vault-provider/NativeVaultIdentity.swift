import AuthenticationServices
import CryptoKit
import Foundation
@preconcurrency import LocalAuthentication

/// Closed, metadata-only native inventory.  It never receives a password value
/// or passkey source; the only user-facing fields become Apple identity labels.
enum NativeVaultIdentityKind: String { case password, passkey }
struct NativeVaultIndexedIdentity: Equatable {
    let itemID: String; let kind: NativeVaultIdentityKind; let serviceType: String?; let serviceIdentifier: String?; let passkeyID: String?; let rpID: String?; let credentialID: Data?; let userHandle: Data?; let user: String
}
struct NativeVaultIdentityPage { let snapshotID: String; let identities: [NativeVaultIndexedIdentity]; let nextAfter: String?; let complete: Bool; let unsupportedCount: Int }

/// The refresh boundary only exposes this closed, value-free failure set to
/// configuration UI.  In particular, it never renders an HTTP body or a
/// transport/Apple diagnostic, which may contain account or service details.
enum NativeVaultSuggestionRefreshFailure: Error, LocalizedError, Equatable {
    case reconnect, organizationAccess, changed, wait, serviceUpdate, unavailable, rejected, malformedMetadata, limitExceeded, connectivity

    static func http(_ status: Int) -> Self {
        switch status {
        case 401: return .reconnect
        case 403: return .organizationAccess
        case 404: return .serviceUpdate
        case 409: return .changed
        case 429: return .wait
        case 500...599: return .unavailable
        default: return .rejected
        }
    }

    static func transport(_ error: Error) -> Self {
        // Deliberately classify all URL and transport diagnostics together;
        // neither their localized text nor an underlying server error is safe
        // to show in this privileged extension.
        .connectivity
    }

    static func presentationMessage(for error: Error) -> String {
        if let failure = error as? NativeVaultSuggestionRefreshFailure { return failure.errorDescription! }
        if let failure = error as? NativeIdentityStoreFailure { return failure.errorDescription! }
        return "Vault suggestions could not be refreshed. Try again."
    }

    var errorDescription: String? {
        switch self {
        case .reconnect: return "Vault connection needs reconnect."
        case .organizationAccess: return "Your account cannot use this organization. Choose another organization."
        case .changed: return "Vault suggestions changed while refreshing. Try again."
        case .wait: return "Vault suggestions are busy. Wait a moment and try again."
        case .serviceUpdate: return "Vault suggestion service needs an update. Try again later."
        case .unavailable: return "Vault suggestions are temporarily unavailable. Try again."
        case .rejected: return "Vault suggestions were rejected. Try again."
        case .malformedMetadata: return "Vault suggestion metadata was rejected. Refresh suggestions."
        case .limitExceeded: return "Vault suggestion refresh exceeded its limit. Try again."
        case .connectivity: return "Can't reach AI Matrx Vault. Check your connection and try again."
        }
    }
}

enum NativeVaultIdentityCodec {
    private static func rejected() -> Error { NativeVaultSuggestionRefreshFailure.malformedMetadata }
    private static func uuid(_ value: JSONValue?) -> String? { guard case let .string(value)? = value, value.canonicalUUID else { return nil }; return value }
    private static func text(_ value: JSONValue?, max: Int = 1024) -> String? { guard case let .string(value)? = value, !value.isEmpty, value.utf8.count <= max else { return nil }; return value }
    static func page(_ data: Data) throws -> NativeVaultIdentityPage {
        guard data.count <= 96 * 1024 else { throw rejected() }
        let object = try StrictEnvelope.object(data, required: ["snapshot_id", "identities", "next_after", "complete", "unsupported_count"], optional: [], maxBytes: 96 * 1024)
        guard let snapshot = text(object["snapshot_id"], max: 64), snapshot.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil, case let .array(rows)? = object["identities"], rows.count <= 200, case let .bool(complete)? = object["complete"], case let .number(unsupportedText)? = object["unsupported_count"], let unsupported = Int(unsupportedText), (0...2000).contains(unsupported) else { throw rejected() }
        let after: String?
        switch object["next_after"] { case .null?: after = nil; case let .string(value)?: guard !value.isEmpty, value.utf8.count <= 512 else { throw rejected() }; after = value; default: throw rejected() }
        guard (complete && after == nil) || (!complete && after != nil) else { throw rejected() }
        guard complete || !rows.isEmpty else { throw rejected() }
        var values: [NativeVaultIndexedIdentity] = []; var seen = Set<String>()
        for row in rows {
            guard case let .object(value) = row, let item = uuid(value["item_id"]), case let .string(kindText)? = value["kind"], let kind = NativeVaultIdentityKind(rawValue: kindText), let user = text(value["user"], max: 256) else { throw rejected() }
            switch kind {
            case .password:
                guard Set(value.keys) == Set(["item_id", "kind", "service", "user"]), case let .object(service)? = value["service"], Set(service.keys) == Set(["type", "identifier"]), case let .string(type)? = service["type"], type == "url", let identifier = text(service["identifier"], max: 2048) else { throw rejected() }
                guard seen.insert("password|\(item)|\(type)|\(identifier)").inserted else { throw rejected() }
                values.append(.init(itemID: item, kind: kind, serviceType: type, serviceIdentifier: identifier, passkeyID: nil, rpID: nil, credentialID: nil, userHandle: nil, user: user))
            case .passkey:
                guard Set(value.keys) == Set(["item_id", "kind", "passkey_id", "rp_id", "credential_id", "user_handle", "user"]), let passkey = uuid(value["passkey_id"]), let rp = text(value["rp_id"], max: 253), case let .string(credentialText)? = value["credential_id"], let credential = Data(base64URLEncoded: credentialText), credential.urlSafeBase64() == credentialText, (1...1024).contains(credential.count), case let .string(handleText)? = value["user_handle"], let handle = Data(base64URLEncoded: handleText), handle.urlSafeBase64() == handleText, (1...64).contains(handle.count) else { throw rejected() }
                guard seen.insert("passkey|\(item)|\(passkey)").inserted else { throw rejected() }
                values.append(.init(itemID: item, kind: kind, serviceType: nil, serviceIdentifier: nil, passkeyID: passkey, rpID: rp, credentialID: credential, userHandle: handle, user: user))
            }
        }
        return .init(snapshotID: snapshot, identities: values, nextAfter: after, complete: complete, unsupportedCount: unsupported)
    }
}

private extension Data { func urlSafeBase64() -> String { base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }; init?(base64URLEncoded value: String) { guard !value.isEmpty, value.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { return nil }; var padded = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/"); padded += String(repeating: "=", count: (4 - padded.count % 4) % 4); self.init(base64Encoded: padded) } }

/// Record IDs are intentionally metadata-only.  Their complete state binding
/// makes a successful replacement unusable after any account/scope revision.
struct NativeVaultIdentityBinding { let item: String; let kind: NativeVaultIdentityKind; let organization: String; let passkey: String?; let serviceDigest: String? }
enum NativeVaultIdentityRecord {
    static func serviceDigest(type: String, identifier: String) -> String { Data(SHA256.hash(data: Data("\(type)|\(identifier)".utf8))).map { String(format: "%02x", $0) }.joined() }
    static func make(_ identity: NativeVaultIndexedIdentity, state: PublicState, organization: String, revision: String) throws -> String {
        guard state.provider_subject != nil, state.generation.canonicalUUID, organization.canonicalUUID, revision.canonicalUUID else { throw EnrollmentError.message("Vault suggestions are no longer current.") }
        var components = ["v1", state.provider_subject!, state.generation, organization, revision, identity.itemID, identity.kind.rawValue]
        if identity.kind == .password {
            guard let type = identity.serviceType, let identifier = identity.serviceIdentifier else { throw EnrollmentError.message("Vault suggestions are no longer current.") }
            components.append(serviceDigest(type: type, identifier: identifier))
        } else { components.append(identity.passkeyID!) }
        let encoded = Data(components.joined(separator: "\u{1f}").utf8).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        guard encoded.utf8.count <= 1024 else { throw EnrollmentError.message("Vault suggestion metadata was rejected. Refresh suggestions.") }; return encoded
    }
    static func parse(_ record: String, state: PublicState) -> NativeVaultIdentityBinding? {
        guard record.utf8.count <= 1024, let data = Data(base64URLEncoded: record), let text = String(data: data, encoding: .utf8) else { return nil }
        let fields = text.split(separator: "\u{1f}").map(String.init)
        guard fields.count >= 7, fields[0] == "v1", fields[1] == state.provider_subject, fields[2] == state.generation, fields[3] == state.suggestions.organization_id, fields[4] == state.suggestions.revision, fields[5].canonicalUUID, let kind = NativeVaultIdentityKind(rawValue: fields[6]), state.version == 2, ["ready", "stale"].contains(state.suggestions.status) else { return nil }
        if kind == .password { guard fields.count == 8, fields[7].range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { return nil }; return NativeVaultIdentityBinding(item: fields[5], kind: kind, organization: fields[3], passkey: nil, serviceDigest: fields[7]) }
        guard fields.count == 8, fields[7].canonicalUUID else { return nil }; return NativeVaultIdentityBinding(item: fields[5], kind: kind, organization: fields[3], passkey: fields[7], serviceDigest: nil)
    }
}

protocol NativeVaultIdentityStoring: AnyObject, Sendable { func state(_ completion: @escaping (Bool, Error?) -> Void); func replace(_ entries: [ASCredentialIdentity], completion: @escaping (Bool, Error?) -> Void) }
final class NativeVaultIdentityStore: NativeVaultIdentityStoring, @unchecked Sendable {
    func state(_ completion: @escaping (Bool, Error?) -> Void) { ASCredentialIdentityStore.shared.getState { completion($0.isEnabled, nil) } }
    func replace(_ entries: [ASCredentialIdentity], completion: @escaping (Bool, Error?) -> Void) { ASCredentialIdentityStore.shared.replaceCredentialIdentities(entries, completion: completion) }
}

final class NativeVaultIdentitySynchronizer {
    private let store: NativeVaultIdentityStoring
    private let stateStore: () throws -> ProviderStore
    private let now: () -> Date
    private let lock = NSLock(); private var active = UUID()
    init(store: NativeVaultIdentityStoring = NativeVaultIdentityStore(), stateStore: @escaping () throws -> ProviderStore = { try ProviderStore(mode: .providerAccess) }, now: @escaping () -> Date = Date.init) { self.store = store; self.stateStore = stateStore; self.now = now }
    func cancel() { lock.lock(); active = UUID(); lock.unlock() }
    private func isActive(_ request: UUID) -> Bool { lock.lock(); defer { lock.unlock() }; return active == request }
    private func begin() -> UUID { lock.lock(); defer { lock.unlock() }; active = UUID(); return active }
    static func records(_ values: [NativeVaultIndexedIdentity], state: PublicState, organization: String, revision: String) throws -> [ASCredentialIdentity] {
        var result: [ASCredentialIdentity] = []; var ids = Set<String>()
        for value in values {
            let record = try NativeVaultIdentityRecord.make(value, state: state, organization: organization, revision: revision)
            guard ids.insert(record).inserted else { throw EnrollmentError.message("Vault suggestion metadata was rejected. Refresh suggestions.") }
            switch value.kind {
            case .password: result.append(ASPasswordCredentialIdentity(serviceIdentifier: ASCredentialServiceIdentifier(identifier: value.serviceIdentifier!, type: value.serviceType == "url" ? .URL : .domain), user: value.user, recordIdentifier: record))
            case .passkey: result.append(ASPasskeyCredentialIdentity(relyingPartyIdentifier: value.rpID!, userName: value.user, credentialID: value.credentialID!, userHandle: value.userHandle!, recordIdentifier: record))
            }
        }; return result
    }
    /// Serializes the Apple callback beneath the provider state lock.  The
    /// caller supplies the already-complete inventory, so no partial page can
    /// ever alter the store.
    /// A v1 record never authorizes a suggestion.  The first authenticated
    /// provider invocation clears Apple entries while retaining the held file
    /// lock, then publishes the v2 empty revision.
    func migrateV1(subject: String, generation: String, completion: @escaping (Result<Void, Error>) -> Void) {
        let request = begin()
        DispatchQueue.global(qos: .userInitiated).async { [store] in
            do { try self.stateStore().locked { current in
                guard self.isActive(request), current.generation == generation, current.provider_subject == subject else { throw EnrollmentError.message("Your Vault account changed. Start again.") }
                guard current.version == 1 else { return }
                let sem = DispatchSemaphore(value: 0); var okay = false; var error: Error?
                store.replace([]) { success, failure in okay = success; error = failure; sem.signal() }; sem.wait()
                guard okay, error == nil, self.isActive(request) else { throw EnrollmentError.message("Vault suggestions are unavailable on this Mac.") }
                try self.stateStore().write(PublicState(version: 2, generation: current.generation, host_subject: current.host_subject, provider_subject: current.provider_subject))
            }; DispatchQueue.main.async { guard self.isActive(request) else { return }; completion(.success(())) }
            } catch { DispatchQueue.main.async { guard self.isActive(request) else { return }; completion(.failure(error)) } }
        }
    }

    func refresh(accessToken: String, subject: String, generation: String, organization: String, send: @escaping (URLRequest, @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) -> Void, isCurrent: @escaping () -> Bool, completion: @escaping (Result<PublicState, Error>) -> Void) {
        let requestID = begin()
        let queue = DispatchQueue(label: "com.aimatrx.vault.identity-refresh")
        let deadline = now().addingTimeInterval(60)
        store.state { enabled, error in
            queue.async {
                guard self.isActive(requestID), isCurrent() else { return }
                do {
                    let expected = try self.stateStore().locked { $0 }
                    guard expected.version == 2, expected.generation == generation, expected.provider_subject == subject else {
                        throw EnrollmentError.message("Your Vault account changed. Start again.")
                    }
                    guard error == nil, enabled else {
                        let failure = self.markStale(expected: expected, request: requestID) ?? NativeIdentityStoreFailure.map(error, disabled: !enabled)
                        DispatchQueue.main.async {
                            guard self.isActive(requestID), isCurrent() else { return }
                            completion(.failure(failure))
                        }
                        return
                    }
                    var collected: [NativeVaultIndexedIdentity] = []
                    var keys = Set<String>(); var cursors = Set<String>()
                    var snapshot: String?; var after: String?; var unsupported: Int?
                    var retries = 0; var requests = 0; var bytes = 0; var pending = UUID()
                    var finished = false
                    func fail(_ error: Error) {
                        guard !finished else { return }; finished = true
                        let failure = self.markStale(expected: expected, request: requestID) ?? error
                        DispatchQueue.main.async {
                            guard self.isActive(requestID), isCurrent() else { return }
                            completion(.failure(failure))
                        }
                    }
                    func next(authorityCheck: Bool = false) {
                        guard self.isActive(requestID), isCurrent() else { finished = true; return }
                        guard !finished else { return }
                        guard self.now() < deadline else { fail(NativeVaultSuggestionRefreshFailure.connectivity); return }
                        guard requests < 4004, bytes <= 16 * 1024 * 1024 else { fail(NativeVaultSuggestionRefreshFailure.limitExceeded); return }
                        requests += 1
                        let nonce = UUID(); pending = nonce
                        do {
                            let body = try JSONSerialization.data(withJSONObject: ["snapshot_id": snapshot as Any? ?? NSNull(), "after": authorityCheck ? NSNull() : (after as Any? ?? NSNull())], options: [.sortedKeys])
                            var request = URLRequest(url: URL(string: "https://server.app.matrxserver.com/api/vault/native/identities")!)
                            request.httpMethod = "POST"; request.httpBody = body; request.timeoutInterval = min(10, max(0.1, deadline.timeIntervalSinceNow))
                            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
                            request.setValue(organization, forHTTPHeaderField: "X-Organization-Id")
                            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                            request.setValue("application/json", forHTTPHeaderField: "Accept")
                            send(request) { result in queue.async {
                                guard !finished, pending == nonce, self.isActive(requestID), isCurrent() else { return }
                                pending = UUID()
                                switch result {
                                case let .failure(error):
                                    fail(NativeVaultSuggestionRefreshFailure.transport(error))
                                case let .success((data, response)):
                                    do {
                                    guard self.now() < deadline else { throw NativeVaultSuggestionRefreshFailure.connectivity }
                                    guard data.count <= 96 * 1024 else { throw NativeVaultSuggestionRefreshFailure.malformedMetadata }
                                    bytes += data.count
                                    guard bytes <= 16 * 1024 * 1024 else { throw NativeVaultSuggestionRefreshFailure.limitExceeded }
                                    if response.statusCode == 409, retries == 0 {
                                        retries = 1; collected.removeAll(); keys.removeAll(); cursors.removeAll()
                                        snapshot = nil; after = nil; unsupported = nil; next(); return
                                    }
                                    guard response.statusCode == 200 else { throw NativeVaultSuggestionRefreshFailure.http(response.statusCode) }
                                    let page = try NativeVaultIdentityCodec.page(data)
                                    guard snapshot == nil || snapshot == page.snapshotID, unsupported == nil || unsupported == page.unsupportedCount else { throw NativeVaultSuggestionRefreshFailure.changed }
                                    if authorityCheck {
                                        finished = true
                                        self.replaceComplete(request: requestID, values: collected, organization: organization, expected: expected, unsupported: page.unsupportedCount, isCurrent: { self.isActive(requestID) && isCurrent() && self.now() < deadline }, completion: completion)
                                        return
                                    }
                                    guard collected.count + page.identities.count + page.unsupportedCount <= 2000 else { throw NativeVaultSuggestionRefreshFailure.malformedMetadata }
                                    for value in page.identities {
                                        let key = value.kind == .password ? "password|\(value.itemID)|\(value.serviceType!)|\(value.serviceIdentifier!)" : "passkey|\(value.itemID)|\(value.passkeyID!)"
                                        guard keys.insert(key).inserted else { throw NativeVaultSuggestionRefreshFailure.malformedMetadata }
                                    }
                                    if let cursor = page.nextAfter { guard cursors.insert(cursor).inserted else { throw NativeVaultSuggestionRefreshFailure.malformedMetadata } }
                                    snapshot = page.snapshotID; unsupported = page.unsupportedCount
                                    collected += page.identities; after = page.nextAfter
                                    next(authorityCheck: page.complete)
                                    } catch { fail(error is NativeVaultSuggestionRefreshFailure ? error : NativeVaultSuggestionRefreshFailure.malformedMetadata) }
                                }
                            } }
                        } catch { fail(error is NativeVaultSuggestionRefreshFailure ? error : NativeVaultSuggestionRefreshFailure.rejected) }
                    }
                    next()
                } catch { DispatchQueue.main.async { completion(.failure(error)) } }
            }
        }
    }

    /// Transport, authorization, and inventory failures retain the last Apple
    /// metadata but make its public authority stale. Only explicit disconnect
    /// and terminal local binding paths clear identities.
    private func markStale(expected: PublicState, request: UUID) -> Error? {
        do { try self.stateStore().locked { current in
            guard self.isActive(request), current.version == 2, current.generation == expected.generation, current.provider_subject == expected.provider_subject, current.suggestions.revision == expected.suggestions.revision, current.suggestions.status == "ready" else { return }
            try self.stateStore().write(PublicState(version: 2, generation: current.generation, host_subject: current.host_subject, provider_subject: current.provider_subject, suggestions: NativeSuggestions(organization_id: current.suggestions.organization_id, revision: current.suggestions.revision, status: "stale", refreshed_at_ms: current.suggestions.refreshed_at_ms, count: current.suggestions.count, unsupported_count: current.suggestions.unsupported_count)))
        }; return nil
        } catch { return EnrollmentError.message("Vault suggestion status could not be saved. Refresh suggestions.") }
    }

    private func replaceComplete(request: UUID, values: [NativeVaultIndexedIdentity], organization: String, expected: PublicState, unsupported: Int, isCurrent: @escaping () -> Bool, completion: @escaping (Result<PublicState, Error>) -> Void) {
        let revision = UUID().canonical
        DispatchQueue.global(qos: .userInitiated).async { [store] in
            do {
                let next = try self.stateStore().locked { current -> PublicState in
                    guard self.isActive(request), isCurrent(), current.generation == expected.generation, current.provider_subject == expected.provider_subject, current.version == 2, current.suggestions.revision == expected.suggestions.revision else { throw EnrollmentError.message("Vault suggestions are no longer current.") }
                    let identities = try Self.records(values, state: current, organization: organization, revision: revision)
                    let sem = DispatchSemaphore(value: 0); var ok = false; var error: Error?
                    store.replace(identities) { success, failure in ok = success; error = failure; sem.signal() }; sem.wait()
                    guard ok, error == nil else { throw NativeIdentityStoreFailure.map(error) }
                    guard self.isActive(request), isCurrent() else { throw EnrollmentError.message("Vault suggestions are no longer current.") }
                    let now = Int64(Date().timeIntervalSince1970 * 1000)
                    let updated = PublicState(version: 2, generation: current.generation, host_subject: current.host_subject, provider_subject: current.provider_subject, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: "ready", refreshed_at_ms: now, count: values.count, unsupported_count: unsupported))
                    try self.stateStore().write(updated); return updated
                }
                DispatchQueue.main.async { guard self.isActive(request) else { return }; completion(.success(next)) }
            } catch {
                let failure = self.markStale(expected: expected, request: request) ?? error
                DispatchQueue.main.async { guard self.isActive(request), isCurrent() else { return }; completion(.failure(failure)) }
            }
        }
    }
}


enum NativeIdentityStoreFailure: Error, LocalizedError {
    case disabled, busy, failed
    static func map(_ error: Error?, disabled: Bool = false) -> Self {
        if disabled { return .disabled }
        if let error = error as NSError?, error.domain == ASCredentialIdentityStoreErrorDomain {
            if error.code == 1 { return .disabled }
            if error.code == 2 { return .busy }
        }
        return .failed
    }
    var errorDescription: String? {
        switch self {
        case .disabled: return "Enable AI Matrx in Password AutoFill settings to show suggestions."
        case .busy: return "Apple's suggestion store is busy. Try again."
        case .failed: return "Apple's suggestion store could not be updated. Try again."
        }
    }
}
