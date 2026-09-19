import AppKit
import AuthenticationServices
import Foundation
@preconcurrency import LocalAuthentication

let nativeAPIOrigin = URL(string: "https://server.app.matrxserver.com")!

/// A value-free match from the native password endpoint. It deliberately keeps
/// the Apple request index so a credential cannot be materialized for a
/// different requested service identifier.
struct NativePasswordMatch {
    let itemID: String
    let displayName: String
    let identifierIndex: Int
}

struct NativeOrganization {
    let id: String
    let name: String
    let isPersonal: Bool
}

struct NativePasswordCurrentState { let generation: String; let subject: String? }

/// Shared production stage decisions. Runtime and corpus both use these exact
/// decisions; dependencies supply Apple input and current App Group state.
enum NativePasswordStage {
    static func identifiers(_ values: [ASCredentialServiceIdentifier]) throws -> [(String, String)] {
        var result: [(String, String)] = []
        for value in values {
            let type = value.type == .domain ? "domain" : (value.type == .URL ? "url" : "")
            guard !type.isEmpty, value.identifier.utf8.count <= 2048 else { throw EnrollmentError.message("This website request is not supported.") }
            result.append((type, value.identifier))
        }
        return result
    }
    static func grantIsCurrent(_ state: NativePasswordCurrentState, _ grant: NativeVaultSessionAccess.Grant) -> Bool {
        state.generation == grant.generation && state.subject == grant.subject
    }
    static var interactionRequiredCode: Int { ASExtensionError.userInteractionRequired.rawValue }
}

enum NativePasswordCodec {
    private static func rejected() -> Error { EnrollmentError.message("Vault response was rejected. Try again.") }

    static func organizations(_ data: Data, subject: String) throws -> (organizations: [NativeOrganization], selected: String?) {
        let object = try StrictEnvelope.object(data, required: ["authenticated", "user_id", "organizations", "default_organization_id", "default_preference_status", "warnings", "missing_organization_count"], optional: [])
        guard case .bool(true)? = object["authenticated"], case .string(subject)? = object["user_id"], case let .array(rows)? = object["organizations"], rows.count <= 128 else { throw rejected() }
        var organizations: [NativeOrganization] = []
        var seen = Set<String>()
        for row in rows {
            guard case let .object(value) = row else { throw rejected() }
            let abbreviationIsValid: Bool
            switch value["abbreviation"] { case .null?, .string?: abbreviationIsValid = true; default: abbreviationIsValid = false }
            guard case let .object(value) = row,
                  Set(value.keys) == Set(["id", "name", "is_personal", "abbreviation"]),
                  case let .string(id)? = value["id"], id.canonicalUUID,
                  case let .string(name)? = value["name"], !name.isEmpty, name.unicodeScalars.count <= 256,
                  case let .bool(personal)? = value["is_personal"],
                  abbreviationIsValid,
                  seen.insert(id).inserted else { throw rejected() }
            organizations.append(NativeOrganization(id: id, name: name, isPersonal: personal))
        }
        let preferred: String?
        switch object["default_organization_id"] {
        case .null?: preferred = nil
        case let .string(id)?: guard id.canonicalUUID else { throw rejected() }; preferred = id
        default: throw rejected()
        }
        guard case let .string(status)? = object["default_preference_status"], ["valid", "unset", "stale", "malformed", "unavailable"].contains(status), case .array? = object["warnings"], case .number? = object["missing_organization_count"] else { throw rejected() }
        return (organizations, preferred)
    }

    static func matches(_ data: Data) throws -> (matches: [NativePasswordMatch], truncated: Bool, reason: String?) {
        let object = try StrictEnvelope.object(data, required: ["matches", "truncated", "reason"], optional: [])
        guard case let .array(rows)? = object["matches"], rows.count <= 200,
              case let .bool(truncated)? = object["truncated"] else { throw rejected() }
        let reason: String?
        switch object["reason"] { case .null?: reason = nil; case .string("no_service_identifiers")?: reason = "no_service_identifiers"; default: throw rejected() }
        var result: [NativePasswordMatch] = []
        var seen = Set<String>()
        for row in rows {
            guard case let .object(value) = row, Set(value.keys) == Set(["item_id", "display_name", "request_identifier_index"]),
                  case let .string(id)? = value["item_id"], id.canonicalUUID,
                  case let .string(name)? = value["display_name"], !name.isEmpty, name.unicodeScalars.count <= 256,
                  case let .number(indexString)? = value["request_identifier_index"], let index = Int(indexString), index >= 0,
                  seen.insert("\(id):\(index)").inserted else { throw rejected() }
            result.append(NativePasswordMatch(itemID: id, displayName: name, identifierIndex: index))
        }
        return (result, truncated, reason)
    }

    static func materialized(_ data: Data) throws -> (username: String, password: String) {
        let object = try StrictEnvelope.object(data, required: ["username", "password"], optional: [])
        guard case let .string(username)? = object["username"], username.utf8.count <= 16 * 1024,
              case let .string(password)? = object["password"], !password.isEmpty, password.utf8.count <= 16 * 1024 else { throw rejected() }
        return (username, password)
    }
    static func errorMessage(_ data: Data, status: Int) -> String {
        let code = (try? StrictEnvelope.object(data, required: ["detail"], optional: [])).flatMap { detail -> String? in guard case let .object(value)? = detail["detail"], Set(value.keys) == Set(["code"]), case let .string(code)? = value["code"] else { return nil }; return code }
        switch code {
        case "item_unavailable": return "The selected password is no longer available."
        case "credential_unavailable": return "The saved password cannot be used right now."
        case "native_session_required": return "Vault connection needs reconnect."
        case "organization_required": return "Choose a Vault organization and try again."
        case "native_unavailable": return "Vault is temporarily unavailable. Try again."
        default: return status == 503 ? "Vault is temporarily unavailable. Try again." : "Vault request was rejected. Try again."
        }
    }
}

protocol NativeVaultPasswordTransporting {
    func cancel()
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void)
}

extension NativeVaultPasswordTransporting { func cancel() {} }

/// Production transport is injectable so the wire/operation corpus can force
/// redirects, malformed envelopes, and stale callback ordering without a live
/// extension or Keychain. The production instance is the bounded no-redirect
/// URLSession delegate used by enrollment.
final class NativeVaultPasswordTransport: NativeVaultPasswordTransporting {
    private let lock = NSLock()
    private var requests: [UUID: BoundedTransport] = [:]
    func cancel() {
        lock.lock(); let pending = Array(requests.values); requests.removeAll(); lock.unlock()
        pending.forEach { $0.cancel() }
    }
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let id = UUID()
        let transport = BoundedTransport { [weak self] result in
            if let self { self.lock.lock(); self.requests.removeValue(forKey: id); self.lock.unlock() }
            completion(result)
        }
        lock.lock(); requests[id] = transport; lock.unlock()
        transport.start(request)
    }
}

@MainActor
final class NativePasswordOperation {
    enum Phase { case authorizing, selectingOrganization, loadingMatches, selectingCredential, materializing, completed, cancelled }
    let id = UUID()
    let lifetime: NativeVaultRequestLifetime
    let identifiers: [(type: String, identifier: String)]
    var phase: Phase = .authorizing
    var generation: String?
    var subject: String?
    var terminal = false
    init(_ identifiers: [(String, String)], lifetime: NativeVaultRequestLifetime) { self.lifetime = lifetime; self.identifiers = identifiers.map { (type: $0.0, identifier: $0.1) } }
}

/// Sole owner for request identity and terminal state. A replacement marks its
/// predecessor terminal but never cancels the shared extension context.
@MainActor
final class NativePasswordOperationCoordinator {
    private(set) var active: NativePasswordOperation?
    func begin(_ identifiers: [(String, String)], lifetime: NativeVaultRequestLifetime = NativeVaultRequestLifetime()) -> NativePasswordOperation {
        if let old = active, !old.terminal { old.terminal = true; old.phase = .cancelled; old.lifetime.cancel() }
        let next = NativePasswordOperation(identifiers, lifetime: lifetime); active = next; return next
    }
    func current(_ operation: NativePasswordOperation) -> Bool { active === operation && !operation.terminal }
    func cancel(_ operation: NativePasswordOperation) -> Bool {
        guard current(operation) else { return false }; operation.terminal = true; operation.phase = .cancelled; operation.lifetime.cancel(); active = nil; return true
    }
    func prepareCompletion(_ operation: NativePasswordOperation) -> Bool {
        guard current(operation) else { return false }; operation.terminal = true; operation.phase = .completed; return true
    }
    func clearCompleted(_ operation: NativePasswordOperation) { if active === operation { active = nil } }
}

/// The one provider-owned authorized session primitive. Both configuration and
/// password use must enter through this boundary so refresh_pending, Keychain
/// access, subject validation, and generation fencing cannot drift.
final class NativeVaultSessionAccess {
    struct Grant {
        let accessToken: String; let subject: String; let generation: String
        let suggestionState: PublicState?
        init(accessToken: String, subject: String, generation: String, suggestionState: PublicState? = nil) {
            self.accessToken = accessToken; self.subject = subject; self.generation = generation; self.suggestionState = suggestionState
        }
    }
    private let transport: NativeVaultPasswordTransporting
    private let store: () throws -> ProviderStore
    private let privateSession: () -> NativeVaultPrivateSession
    private let identityIndex: ProviderIdentityIndex
    init(transport: NativeVaultPasswordTransporting = NativeVaultPasswordTransport(), store: @escaping () throws -> ProviderStore = { try ProviderStore(mode: .providerAccess) }, privateSession: @escaping () -> NativeVaultPrivateSession = { NativeVaultPrivateSession() }, identityIndex: ProviderIdentityIndex = .system) {
        self.transport = transport; self.store = store; self.privateSession = privateSession; self.identityIndex = identityIndex
    }

    func cancel() { transport.cancel() }

    func acquire(key: String, context: LAContext, lifetime: NativeVaultRequestLifetime, completion: @escaping (Result<Grant, Error>) -> Void) {
        // Claim completion once, before reconciliation. The caller cannot close
        // its extension request until durable state and Apple's index agree.
        let completionLock = NSLock()
        var finished = false
        func finish(_ result: Result<Grant, Error>, expected: PublicState?) {
            completionLock.lock()
            guard !finished else { completionLock.unlock(); return }
            finished = true; completionLock.unlock()
            DispatchQueue.global(qos: .userInitiated).async {
                guard lifetime.isCurrent else { completion(.failure(URLError(.cancelled))); return }
                do {
                    if case let .failure(error) = result, let expected {
                        try self.reconcile(error, expected: expected, lifetime: lifetime)
                    }
                    completion(lifetime.isCurrent ? result : .failure(URLError(.cancelled)))
                } catch { completion(.failure(error)) }
            }
        }
        DispatchQueue.global(qos: .userInitiated).async {
            guard lifetime.isCurrent else { finish(.failure(URLError(.cancelled)), expected: nil); return }
            var expected: PublicState?
            let result: Result<(PrivateSession, PublicState, Bool), Error> = Result {
                try self.store().locked { state in
                    guard lifetime.isCurrent else { throw URLError(.cancelled) }
                    expected = state
                    let session: PrivateSession
                    do {
                        guard let active = try self.privateSession().readActive(context: context, matching: state) else {
                            throw NativeVaultSessionFailure.providerDisconnected(generation: state.generation, subject: state.provider_subject)
                        }
                        session = active
                    } catch {
                        if case .localSessionCleanupFailed = error as? NativeVaultSessionFailure {
                            throw NativeVaultSessionFailure.corruptCleanupBound(generation: state.generation, subject: state.provider_subject)
                        }
                        if case .localSessionCorrupt = error as? NativeVaultSessionFailure {
                            throw NativeVaultSessionFailure.corruptBound(generation: state.generation, subject: state.provider_subject)
                        }
                        throw error
                    }
                    if session.expires_at_ms > Int64(Date().timeIntervalSince1970 * 1000) + 10_000 { return (session, state, false) }
                    guard lifetime.isCurrent else { throw URLError(.cancelled) }
                    _ = try self.privateSession().beginRefresh(session, context: context)
                    return (session, state, true)
                }
            }
            switch result {
            case let .failure(error): finish(.failure(error), expected: expected)
            case let .success((session, state, refresh)):
                guard lifetime.isCurrent else { finish(.failure(URLError(.cancelled)), expected: state); return }
                if refresh {
                    self.refresh(session, state: state, key: key, context: context, lifetime: lifetime) { finish($0, expected: state) }
                } else {
                    self.validate(token: session.access_token, subject: session.subject, generation: state.generation, key: key) { result in
                        switch result {
                        case let .failure(error): finish(.failure(error), expected: state)
                        case let .success(identity): self.recheck(generation: state.generation, subject: identity.sub) { valid in
                            finish(valid ? .success(Grant(accessToken: session.access_token, subject: identity.sub, generation: state.generation, suggestionState: state)) : .failure(EnrollmentError.message("A host account change cancelled Vault connection. Reconnect the provider.")), expected: state)
                        }
                        }
                    }
                }
            }
        }
    }

    /// Ambiguous protected API failures preserve Apple's entries but settle the
    /// captured revision as stale before the caller can close its request.
    /// Receipt-not-found and explicit business refusals remain caller-owned.
    func reconcileResponse(_ result: Result<(Data, HTTPURLResponse), Error>, grant: Grant, lifetime: NativeVaultRequestLifetime, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let stale: Bool
        switch result {
        case .failure: stale = true
        case let .success((_, response)): stale = [401, 403, 429].contains(response.statusCode) || response.statusCode >= 500
        }
        guard stale, let expected = grant.suggestionState else { completion(result); return }
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try self.reconcile(EnrollmentError.message("Vault suggestions need refresh."), expected: expected, lifetime: lifetime)
                completion(lifetime.isCurrent ? result : .failure(URLError(.cancelled)))
            } catch { completion(.failure(error)) }
        }
    }

    private func reconcile(_ error: Error, expected: PublicState, lifetime: NativeVaultRequestLifetime) throws {
        let disk = try store()
        try disk.locked { current in
            guard lifetime.isCurrent, current.generation == expected.generation,
                  current.provider_subject == expected.provider_subject else { return }
            if let binding = (error as? NativeVaultSessionFailure)?.terminalBinding {
                guard binding.generation == current.generation, binding.subject == current.provider_subject else { return }
                // Invalidate authority before clearing metadata. Hold the file
                // lock through Apple's callback so no newer enrollment is erased.
                try disk.write(PublicState(version: 2, generation: UUID().canonical, host_subject: current.host_subject, provider_subject: nil))
                try identityIndex.clearWhileLocked()
            } else if current.version == 2, current.suggestions.revision == expected.suggestions.revision, current.suggestions.status == "ready" {
                let old = current.suggestions
                try disk.write(PublicState(version: 2, generation: current.generation, host_subject: current.host_subject, provider_subject: current.provider_subject, suggestions: NativeSuggestions(organization_id: old.organization_id, revision: old.revision, status: "stale", refreshed_at_ms: old.refreshed_at_ms, count: old.count, unsupported_count: old.unsupported_count)))
            }
        }
    }
    private func refresh(_ session: PrivateSession, state: PublicState, key: String, context: LAContext, lifetime: NativeVaultRequestLifetime, completion: @escaping (Result<Grant, Error>) -> Void) {
        guard lifetime.isCurrent else { completion(.failure(URLError(.cancelled))); return }
        var request = URLRequest(url: tokenURL); request.httpMethod = "POST"; request.timeoutInterval = 10
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept"); request.setValue(key, forHTTPHeaderField: "apikey")
        request.httpBody = formBody([("grant_type", "refresh_token"), ("client_id", clientID), ("refresh_token", session.refresh_token)])
        transport.send(request) { result in
            do {
                guard lifetime.isCurrent else { throw URLError(.cancelled) }
                let (data, response) = try result.get()
                guard response.statusCode == 200 else {
                    if response.statusCode == 400,
                       let object = try? StrictEnvelope.object(data, required: ["error"], optional: ["error_description", "error_uri"]),
                       case .string("invalid_grant")? = object["error"] {
                        throw NativeVaultSessionFailure.refreshRevoked(generation: state.generation, subject: session.subject)
                    }
                    throw connectionResponseError(response.statusCode)
                }
                let token = try VaultEnvelopeCodec.token(data)
                self.validate(token: token.access_token, subject: session.subject, generation: state.generation, key: key) { identity in
                    do {
                        let identity = try identity.get()
                        try self.store().locked { current in
                            guard lifetime.isCurrent, current.generation == state.generation, current.provider_subject == identity.sub else { throw EnrollmentError.message("A host account change cancelled Vault connection. Reconnect the provider.") }
                            let expires = Int64(Date().timeIntervalSince1970 * 1000) + Int64(token.expires_in) * 1000
                            try self.privateSession().save(PrivateSession(version: 1, phase: "active", subject: identity.sub, generation: state.generation, access_token: token.access_token, refresh_token: token.refresh_token, expires_at_ms: expires), context: context)
                        }
                        completion(.success(Grant(accessToken: token.access_token, subject: identity.sub, generation: state.generation, suggestionState: state)))
                    } catch { completion(.failure(error)) }
                }
            } catch { completion(.failure(error)) }
        }
    }
    private func validate(token: String, subject: String, generation: String, key: String, completion: @escaping (Result<Identity, Error>) -> Void) {
        var request = URLRequest(url: userinfoURL); request.timeoutInterval = 10; request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization"); request.setValue(key, forHTTPHeaderField: "apikey"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        transport.send(request) { result in
            do { let (data, response) = try result.get(); guard response.statusCode == 200 else { throw connectionResponseError(response.statusCode) }; let identity = try VaultEnvelopeCodec.userinfo(data); guard identity.sub == subject else { throw EnrollmentError.message("Vault connection needs reconnect.") }; completion(.success(identity)) } catch { completion(.failure(error)) }
        }
    }
    private func recheck(generation: String, subject: String, completion: @escaping (Bool) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            completion((try? self.store().locked { $0.generation == generation && $0.provider_subject == subject }) ?? false)
        }
    }
}

extension CredentialProviderViewController {
    private var nativePasswordKey: String? { nativePasswordKeyOverride ?? (Bundle.main.object(forInfoDictionaryKey: "MatrxVaultSupabasePublishableKey") as? String) }
    func beginPasswordRequest(_ serviceIdentifiers: [ASCredentialServiceIdentifier]) {
        replaceNativeRequest()
        beginPasswordRequestAfterReplacement(serviceIdentifiers)
    }
    func beginPasswordRequestAfterReplacement(_ serviceIdentifiers: [ASCredentialServiceIdentifier]) {
        let lifetime = nativeRequest
        let identifiers: [(String, String)]
        do { identifiers = try NativePasswordStage.identifiers(serviceIdentifiers) }
        catch { let rejected = nativePasswordCoordinator.begin([]); cancelPassword(rejected, "This website request is not supported."); return }
        let operation = nativePasswordCoordinator.begin(identifiers, lifetime: lifetime)
        guard let key = nativePasswordKey, key.validToken else { return cancelPassword(operation, "This build has no public Vault configuration. Install an updated AI Matrx build.") }
        if let authorize = nativePasswordAuthorize, let acquire = nativePasswordAcquire {
            authorize { [weak self] allowed in Task { @MainActor in guard let self, self.current(operation) else { return }; guard allowed else { self.cancelPassword(operation, "Unlock Vault protection to continue."); return }; acquire { result in Task { @MainActor in self.receivedGrant(result, operation: operation) } } } }
            return
        }
        let privateSession = NativeVaultPrivateSession(); let context: LAContext
        do { context = try privateSession.authenticatedContext(reason: "Unlock AI Matrx Vault to choose a password") } catch { return cancelPassword(operation, "Vault protection is unavailable on this Mac.") }
        ownNativeContext(context)
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock AI Matrx Vault to choose a password") { [weak self] allowed, _ in
            Task { @MainActor in
                guard let self, self.current(operation) else { return }
                guard allowed else { self.cancelPassword(operation, "Unlock Vault protection to continue."); return }
                self.sessionAccess.acquire(key: key, context: context, lifetime: lifetime) { result in Task { @MainActor in self.receivedGrant(result, operation: operation) } }
            }
        }
    }
    private func receivedGrant(_ result: Result<NativeVaultSessionAccess.Grant, Error>, operation: NativePasswordOperation) {
        guard current(operation) else { return }
        guard case let .success(grant) = result else {
            return cancelPassword(operation, "Vault connection needs reconnect.")
        }
        operation.generation = grant.generation; operation.subject = grant.subject; operation.phase = .selectingOrganization
        // Direct selected credentials are already bound to a live record and
        // must never be delayed by inventory work. Generic list callbacks may
        // refresh their existing selected scope in parallel.
        if nativePasswordSelectedBinding == nil { refreshSuggestionsForCredentialList(grant: grant, lifetime: nativeRequest) }
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent("api/auth/organizations")); request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        sendPasswordRequest(request, grant: grant, operation: operation) { [weak self] result in Task { @MainActor in self?.receivedOrganizations(result, grant: grant, operation: operation) } }
    }
    private func sendPasswordRequest(_ request: URLRequest, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let access = sessionAccess; let lifetime = operation.lifetime
        nativePasswordTransport.send(request) { result in
            access.reconcileResponse(result, grant: grant, lifetime: lifetime, completion: completion)
        }
    }
    private func receivedOrganizations(_ result: Result<(Data, HTTPURLResponse), Error>, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        withLiveGrant(operation, grant) { [weak self] in self?.processOrganizations(result, grant: grant, operation: operation) }
    }
    private func processOrganizations(_ result: Result<(Data, HTTPURLResponse), Error>, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        do {
            let (data, response) = try result.get(); guard response.statusCode == 200 else { throw EnrollmentError.message(NativePasswordCodec.errorMessage(data, status: response.statusCode)) }
            let report = try NativePasswordCodec.organizations(data, subject: grant.subject)
            let selected: NativeOrganization? = nativePasswordSelectedBinding.flatMap { bound in report.organizations.first(where: { $0.id == bound.organization }) } ?? selectOrganization(report.organizations, preferred: report.selected, operation: operation)
            if let selected { loadMatches(selected, grant: grant, operation: operation) }
        } catch { cancelPassword(operation, "Vault organizations are unavailable. Try again.") }
    }
    private func selectOrganization(_ organizations: [NativeOrganization], preferred: String?, operation: NativePasswordOperation) -> NativeOrganization? {
        if let choice = nativePasswordOrganizationChoice { guard let index = choice(organizations), organizations.indices.contains(index) else { cancelPassword(operation, "Password selection was cancelled."); return nil }; return organizations[index] }
        if let subject = operation.subject, let stored = UserDefaults.standard.string(forKey: "native-vault-last-organization-\(subject)"), let match = organizations.first(where: { $0.id == stored }) { return match }
        if let preferred, let match = organizations.first(where: { $0.id == preferred }) { return match }
        if organizations.count == 1 { return organizations[0] }
        guard !organizations.isEmpty else { cancelPassword(operation, "Your account has no available organization."); return nil }
        let alert = NSAlert(); alert.messageText = "Choose the Vault organization"; alert.informativeText = "Choose where to look for a matching saved password."
        let picker = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 340, height: 28)); organizations.forEach { picker.addItem(withTitle: $0.name) }; alert.accessoryView = picker; alert.addButton(withTitle: "Continue"); alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { cancelPassword(operation, "Password selection was cancelled."); return nil }
        let selection = organizations[picker.indexOfSelectedItem]
        if let subject = operation.subject { UserDefaults.standard.set(selection.id, forKey: "native-vault-last-organization-\(subject)") }
        return selection
    }
    private func loadMatches(_ organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        withLiveGrant(operation, grant) { [weak self] in self?.sendMatches(organization, grant: grant, operation: operation) }
    }
    private func sendMatches(_ organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        operation.phase = .loadingMatches
        let rows = operation.identifiers.map { ["type": $0.type, "identifier": $0.identifier] }
        guard JSONSerialization.isValidJSONObject(["identifiers": rows]), let body = try? JSONSerialization.data(withJSONObject: ["identifiers": rows]) else { return cancelPassword(operation, "The requested website is not supported.") }
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent("api/vault/native/passwords/matches")); request.httpMethod = "POST"; request.httpBody = body; request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue(organization.id, forHTTPHeaderField: "X-Organization-Id"); request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        sendPasswordRequest(request, grant: grant, operation: operation) { [weak self] result in Task { @MainActor in self?.receivedMatches(result, organization: organization, grant: grant, operation: operation) } }
    }
    private func receivedMatches(_ result: Result<(Data, HTTPURLResponse), Error>, organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        withLiveGrant(operation, grant) { [weak self] in self?.processMatches(result, organization: organization, grant: grant, operation: operation) }
    }
    private func processMatches(_ result: Result<(Data, HTTPURLResponse), Error>, organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        do {
            let (data, response) = try result.get(); guard response.statusCode == 200 else { throw EnrollmentError.message(NativePasswordCodec.errorMessage(data, status: response.statusCode)) }
            let decoded = try NativePasswordCodec.matches(data)
            let result: (matches: [NativePasswordMatch], truncated: Bool, reason: String?)
            if let expected = nativePasswordSelectedBinding {
                guard operation.identifiers.count == 1, operation.identifiers[0].identifier == expected.service.identifier else { return cancelPassword(operation, "The selected password is no longer available.") }
                result = (decoded.matches.filter { $0.itemID == expected.item && $0.identifierIndex == 0 }, false, decoded.reason)
            } else { result = decoded }
            guard !result.matches.isEmpty else { return cancelPassword(operation, nativePasswordSelectedBinding == nil ? "No saved password matches this website." : "The selected password is no longer available.") }
            if nativePasswordSelectedBinding != nil { guard result.matches.count == 1 else { return cancelPassword(operation, "The selected password is no longer available.") }; return materialize(result.matches[0], organization: organization, grant: grant, operation: operation) }
            operation.phase = .selectingCredential
            let alert = NSAlert(); alert.messageText = "Choose a saved password"; alert.informativeText = result.truncated ? "Only the first matching passwords in \(organization.name) are shown; more may be available." : "Only matching passwords in \(organization.name) are shown."
            if let choice = nativePasswordMatchChoice { guard let index = choice(result.matches), result.matches.indices.contains(index) else { return cancelPassword(operation, "Password selection was cancelled.") }; return materialize(result.matches[index], organization: organization, grant: grant, operation: operation) }
            let picker = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 340, height: 28)); result.matches.forEach { picker.addItem(withTitle: $0.displayName) }; alert.accessoryView = picker; alert.addButton(withTitle: "Use password"); alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return cancelPassword(operation, "Password selection was cancelled.") }
            materialize(result.matches[picker.indexOfSelectedItem], organization: organization, grant: grant, operation: operation)
        } catch { cancelPassword(operation, (error as? LocalizedError)?.errorDescription ?? "Vault is temporarily unavailable. Try again.") }
    }
    private func materialize(_ match: NativePasswordMatch, organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        guard current(operation), match.identifierIndex < operation.identifiers.count else { return cancelPassword(operation, "The selected password is no longer available.") }
        withLiveGrant(operation, grant) { [weak self] in self?.sendMaterialize(match, organization: organization, grant: grant, operation: operation) }
    }
    private func sendMaterialize(_ match: NativePasswordMatch, organization: NativeOrganization, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        operation.phase = .materializing
        let rows = operation.identifiers.map { ["type": $0.type, "identifier": $0.identifier] }
        guard let body = try? JSONSerialization.data(withJSONObject: ["identifiers": rows, "request_identifier_index": match.identifierIndex]) else { return cancelPassword(operation, "The selected password is no longer available.") }
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent("api/vault/native/passwords/\(match.itemID)/materialize")); request.httpMethod = "POST"; request.httpBody = body; request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue(organization.id, forHTTPHeaderField: "X-Organization-Id"); request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept")
        sendPasswordRequest(request, grant: grant, operation: operation) { [weak self] result in Task { @MainActor in self?.receivedMaterial(result, grant: grant, operation: operation) } }
    }
    private func receivedMaterial(_ result: Result<(Data, HTTPURLResponse), Error>, grant: NativeVaultSessionAccess.Grant, operation: NativePasswordOperation) {
        guard current(operation) else { return }
        do { let (data, response) = try result.get(); guard response.statusCode == 200 else { throw EnrollmentError.message(NativePasswordCodec.errorMessage(data, status: response.statusCode)) }; let credential = try NativePasswordCodec.materialized(data); linearizedComplete(operation, grant: grant, credential: credential) } catch { cancelPassword(operation, (error as? LocalizedError)?.errorDescription ?? "Vault is temporarily unavailable. Try again.") }
    }
    private func withLiveGrant(_ operation: NativePasswordOperation, _ grant: NativeVaultSessionAccess.Grant, then: @escaping () -> Void) {
        guard current(operation) else { return }
        let injectedState = nativePasswordCurrentState
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let valid = injectedState.map { NativePasswordStage.grantIsCurrent($0(), grant) } ?? ((try? ProviderStore(mode: .providerAccess).locked { state in NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: state.generation, subject: state.provider_subject), grant) }) ?? false)
            DispatchQueue.main.async { guard let self, self.current(operation) else { return }; guard valid else { self.cancelPassword(operation, "Your Vault account changed. Start again."); return }; then() }
        }
    }
    private func linearizedComplete(_ operation: NativePasswordOperation, grant: NativeVaultSessionAccess.Grant, credential: (username: String, password: String)) {
        let injectedState = nativePasswordCurrentState
        let injectedLock = nativePasswordCompletionLock
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let semaphore = DispatchSemaphore(value: 0)
            let completeUnderLock: (NativePasswordCurrentState) throws -> Void = { state in
                guard NativePasswordStage.grantIsCurrent(state, grant) else { throw EnrollmentError.message("Your Vault account changed. Start again.") }
                DispatchQueue.main.async {
                    guard let self, self.nativePasswordCoordinator.prepareCompletion(operation) else { semaphore.signal(); return }
                    let finished = { [weak self] in Task { @MainActor in self?.nativePasswordCoordinator.clearCompleted(operation) }; return () }
                    if let sink = self.nativePasswordCompleteSink {
                        sink(credential, finished)
                    } else {
                        self.extensionContext.completeRequest(withSelectedCredential: ASPasswordCredential(user: credential.username, password: credential.password), completionHandler: { _ in finished() })
                    }
                    // The invocation is the linearization point. Apple may never
                    // call its completion handler after terminating the extension.
                    semaphore.signal()
                }
                semaphore.wait()
            }
            do {
                if let injectedLock { try injectedLock(completeUnderLock) }
                else if let injectedState { try completeUnderLock(injectedState()) }
                else { try ProviderStore(mode: .providerAccess).locked { state in
                    try completeUnderLock(NativePasswordCurrentState(generation: state.generation, subject: state.provider_subject))
                } }
            } catch { DispatchQueue.main.async { self?.cancelPassword(operation, "Your Vault account changed. Start again.") } }
        }
    }
    private func current(_ operation: NativePasswordOperation) -> Bool { nativePasswordCoordinator.current(operation) }
    private func cancelPassword(_ operation: NativePasswordOperation, _ message: String) { guard nativePasswordCoordinator.cancel(operation) else { return }; let error = NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userCanceled.rawValue, userInfo: [NSLocalizedDescriptionKey: message]); if let sink = nativePasswordCancelSink { sink(error) } else { extensionContext.cancelRequest(withError: error) } }
}


extension CredentialProviderViewController {
    private func cancelSelectedPassword() {
        let error = NSError(domain: ASExtensionErrorDomain, code: ASExtensionError.userCanceled.rawValue, userInfo: [NSLocalizedDescriptionKey: "The selected password is no longer available."])
        if let sink = nativePasswordCancelSink { sink(error) } else { extensionContext.cancelRequest(withError: error) }
    }
    func beginSelectedPassword(_ identity: ASPasswordCredentialIdentity) {
        let lifetime = self.nativeRequest; let service = identity.serviceIdentifier
        let bindingOverride = nativeIdentityBindingOverride
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let record = identity.recordIdentifier ?? ""
            let binding: NativeVaultIdentityBinding? = bindingOverride?(record) ?? ((try? ProviderStore(mode: .providerAccess).locked { NativeVaultIdentityRecord.parse(record, state: $0) }) ?? nil)
            DispatchQueue.main.async {
                guard let self, lifetime.isCurrent, let binding, binding.kind == .password else { self?.cancelSelectedPassword(); return }
                guard let digest = binding.serviceDigest, NativeVaultIdentityRecord.serviceDigest(type: service.type == .URL ? "url" : "domain", identifier: service.identifier) == digest else { self.cancelSelectedPassword(); return }; self.nativePasswordSelectedBinding = (binding.item, binding.organization, service, digest)
                self.beginPasswordRequestAfterReplacement([service])
            }
        }
    }
}
