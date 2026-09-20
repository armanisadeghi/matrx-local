import AppKit
import AuthenticationServices
import Foundation
@preconcurrency import LocalAuthentication


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

    /// THE ORGANIZATION IS WHAT THE USER SET (Arman, 2026-09-19). The report
    /// still CARRIES the account-level saved preference — the wire shape is
    /// the server's — and this decoder deliberately does not hand it back:
    /// nothing that builds a request may read it, and an AutoFill request
    /// built under a saved default is a password read out of the wrong
    /// tenant. The key is named below only to keep the envelope strict.
    static func organizations(_ data: Data, subject: String) throws -> [NativeOrganization] {
        // org-default-exempt: named ONLY to keep the envelope strict; never decoded
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
        guard case let .string(status)? = object["default_preference_status"], ["valid", "unset", "stale", "malformed", "unavailable"].contains(status), case .array? = object["warnings"], case .number? = object["missing_organization_count"] else { throw rejected() }
        return organizations
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
            let organizations = try NativePasswordCodec.organizations(data, subject: grant.subject)
            let selected: NativeOrganization?
            if let bound = nativePasswordSelectedBinding {
                guard let exact = organizations.first(where: { $0.id == bound.organization }) else {
                    throw EnrollmentError.message("The selected account is no longer available. Refresh Vault suggestions.")
                }
                selected = exact
            } else {
                selected = selectOrganization(organizations, operation: operation)
            }
            if let selected { loadMatches(selected, grant: grant, operation: operation) }
        } catch { cancelPassword(operation, "Vault organizations are unavailable. Try again.") }
    }
    /// Three rungs, and the third is a QUESTION: what the user SET on THIS
    /// Mac, then a sole membership (nothing to choose, so choosing it invents
    /// nothing), then ask. The account-level saved default used to sit between
    /// rungs two and three and is gone (Arman, 2026-09-19): a guessed
    /// organization here hands a password out of the wrong tenant.
    private func selectOrganization(_ organizations: [NativeOrganization], operation: NativePasswordOperation) -> NativeOrganization? {
        if let choice = nativePasswordOrganizationChoice { guard let index = choice(organizations), organizations.indices.contains(index) else { cancelPassword(operation, "Password selection was cancelled."); return nil }; return organizations[index] }
        if let subject = operation.subject, let stored = UserDefaults.standard.string(forKey: "native-vault-last-organization-\(subject)"), let match = organizations.first(where: { $0.id == stored }) { return match }
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
