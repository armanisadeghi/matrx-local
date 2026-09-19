import AppKit
import AuthenticationServices
import Foundation
@preconcurrency import LocalAuthentication

private let nativePasskeyResponseLimit = 96 * 1024

protocol NativeVaultPasskeyTransporting: AnyObject {
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void)
    func cancel()
}

final class NativeVaultPasskeyTransport: NativeVaultPasskeyTransporting {
    private var active: BoundedTransport?
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let transport = BoundedTransport(limit: nativePasskeyResponseLimit) { [weak self] result in
            self?.active = nil; completion(result)
        }
        active = transport; transport.start(request)
    }
    func cancel() { active?.cancel(); active = nil }
}

@MainActor
final class NativePasskeyOperation {
    let id = UUID(); let requestGeneration: UInt64
    var terminal = false
    var bridgeOperation: NativeOperation?
    var task: Task<Void, Never>?
    var context: LAContext?
    init(generation: UInt64) { requestGeneration = generation }
    func cancel() { terminal = true; context?.invalidate(); bridgeOperation?.cancel(); task?.cancel() }
}

/// Requests stay inside the provider: only fixed endpoint envelopes cross the
/// network and the source is passed straight from the materialize response to
/// the generated native bridge.
@MainActor
final class NativeVaultPasskeyCoordinator {
    private var active: NativePasskeyOperation?
    private var generation: UInt64 = 0
    private let sessionAccess: NativeVaultSessionAccess
    private let transport: NativeVaultPasskeyTransporting
    private let key: () -> String?
    private let cancel: (String) -> Void
    private let completeRegistration: (ASPasskeyRegistrationCredential) -> Void
    private let completeAssertion: (ASPasskeyAssertionCredential) -> Void

    init(sessionAccess: NativeVaultSessionAccess, transport: NativeVaultPasskeyTransporting = NativeVaultPasskeyTransport(), key: @escaping () -> String?, cancel: @escaping (String) -> Void, completeRegistration: @escaping (ASPasskeyRegistrationCredential) -> Void, completeAssertion: @escaping (ASPasskeyAssertionCredential) -> Void) {
        self.sessionAccess = sessionAccess; self.transport = transport; self.key = key; self.cancel = cancel
        self.completeRegistration = completeRegistration; self.completeAssertion = completeAssertion
    }
    private func begin() -> NativePasskeyOperation {
        active?.cancel(); transport.cancel(); generation &+= 1
        let next = NativePasskeyOperation(generation: generation); active = next; return next
    }
    private func current(_ operation: NativePasskeyOperation) -> Bool { active === operation && !operation.terminal && operation.requestGeneration == generation }
    private func stop(_ operation: NativePasskeyOperation, _ message: String) { guard current(operation) else { return }; operation.cancel(); active = nil; cancel(message) }
    private func request(_ path: String, method: String = "GET", body: Data? = nil, grant: NativeVaultSessionAccess.Grant, organization: NativeOrganization) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: nativeAPIOrigin.appendingPathComponent(path)); request.httpMethod = method; request.timeoutInterval = 10; request.httpBody = body
        request.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization"); request.setValue(organization.id, forHTTPHeaderField: "X-Organization-Id")
        request.setValue("application/json", forHTTPHeaderField: "Accept"); if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return try await withCheckedThrowingContinuation { continuation in transport.send(request) { continuation.resume(with: $0) } }
    }
    private func acquire(_ operation: NativePasskeyOperation) async throws -> NativeVaultSessionAccess.Grant {
        guard let key = key(), key.validToken else { throw EnrollmentError.message("This build has no public Vault configuration. Install an updated AI Matrx build.") }
        let context = try NativeVaultPrivateSession().authenticatedContext(reason: "Unlock AI Matrx Vault to use a passkey")
        operation.context = context
        let allowed: Bool = await withCheckedContinuation { continuation in context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock AI Matrx Vault to use a passkey") { allowed, _ in continuation.resume(returning: allowed) } }
        guard allowed, current(operation) else { throw EnrollmentError.message("Unlock Vault protection to continue.") }
        return try await withCheckedThrowingContinuation { continuation in sessionAccess.acquire(key: key, context: context) { continuation.resume(with: $0) } }
    }
    private func organization(_ grant: NativeVaultSessionAccess.Grant, operation: NativePasskeyOperation, label: String?) async throws -> NativeOrganization {
        let (data, response) = try await request("api/auth/organizations", grant: grant, organization: NativeOrganization(id: "00000000-0000-4000-8000-000000000000", name: "", isPersonal: true))
        guard response.statusCode == 200 else { throw EnrollmentError.message("Vault organizations are unavailable. Try again.") }
        let values = try NativePasswordCodec.organizations(data, subject: grant.subject).organizations
        guard current(operation), !values.isEmpty else { throw EnrollmentError.message("Your account has no available organization.") }
        let alert = NSAlert(); alert.messageText = label == nil ? "Choose the Vault organization" : "Save this passkey"
        alert.informativeText = "Choose where this passkey is stored."
        let picker = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 340, height: 28)); values.forEach { picker.addItem(withTitle: $0.name) }
        if let label { let field = NSTextField(string: label); field.placeholderString = "Passkey label"; let stack = NSStackView(views: [picker, field]); stack.orientation = .vertical; stack.spacing = 8; alert.accessoryView = stack
            alert.addButton(withTitle: "Save passkey"); alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn, current(operation), !field.stringValue.isEmpty, field.stringValue.utf8.count <= 1024 else { throw EnrollmentError.message("Passkey registration was cancelled.") }
            operationLabel[operation.id] = field.stringValue
        } else { alert.accessoryView = picker; alert.addButton(withTitle: "Use passkey"); alert.addButton(withTitle: "Cancel"); guard alert.runModal() == .alertFirstButtonReturn, current(operation) else { throw EnrollmentError.message("Passkey selection was cancelled.") } }
        return values[picker.indexOfSelectedItem]
    }
    private var operationLabel: [UUID: String] = [:]
    private func capabilities(_ grant: NativeVaultSessionAccess.Grant, _ org: NativeOrganization) async throws -> NativePasskeyCapabilities {
        let (data, response) = try await request("api/vault/native/passkeys/capabilities", grant: grant, organization: org)
        guard response.statusCode == 200 else { throw EnrollmentError.message("Passkeys are temporarily unavailable. Try again.") }; return try NativeVaultPasskeyCodec.capabilities(data)
    }
    private func exactBody(mutation: String, source: Data, label: String, principal: String, excluded: [Data]) throws -> Data {
        let object: [String: Any] = ["mutation_id": mutation, "source": NativeVaultPasskeyCodec.base64url(source), "label": label, "principal_type": principal, "excluded_credential_ids": excluded.map(NativeVaultPasskeyCodec.base64url)]
        return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }
    func register(_ raw: any ASCredentialRequest) {
        guard let request = raw as? ASPasskeyCredentialRequest, let identity = request.credentialIdentity as? ASPasskeyCredentialIdentity, request.supportedAlgorithms.contains(where: { $0.rawValue == -7 }), request.clientDataHash.count == 32 else { cancel("This passkey request is not supported."); return }
        let operation = begin()
        operation.task = Task { [weak self] in
            guard let self else { return }
            do {
                let grant = try await self.acquire(operation); guard self.current(operation) else { return }
                let defaultLabel = identity.userName.isEmpty ? identity.relyingPartyIdentifier : identity.userName
                let org = try await self.organization(grant, operation: operation, label: defaultLabel)
                let caps = try await self.capabilities(grant, org); guard self.current(operation), caps.maxSourceBytes > 0 else { return }
                let excluded = request.excludedCredentials?.map(\.credentialID) ?? []
                guard excluded.count <= caps.maxCredentialIDs else { throw EnrollmentError.message("This passkey request has too many excluded credentials.") }
                let label = self.operationLabel.removeValue(forKey: operation.id) ?? defaultLabel
                let mutation = UUID().uuidString.lowercased(); let empty = try self.exactBody(mutation: mutation, source: Data([0]), label: label, principal: org.isPersonal ? "user" : "organization", excluded: excluded)
                let usable = min(caps.maxSourceBytes, 3 * max(0, (caps.maxRequestBodyBytes - empty.count) / 4)); guard usable > 0 else { throw EnrollmentError.message("Passkey storage limit is unavailable. Try again.") }
                let bridge = NativeOperation(); operation.bridgeOperation = bridge
                let ceremony = NativeVaultAppleCeremony(operation: operation, coordinator: self, grant: grant, organization: org, mutation: mutation, label: label, principal: org.isPersonal ? "user" : "organization", excluded: excluded, maximum: usable, bodyLimit: caps.maxRequestBodyBytes)
                let result = try await bridge.register(input: NativeRegistrationInput(rpId: identity.relyingPartyIdentifier, userHandle: identity.userHandle, username: identity.userName, displayName: nil, clientDataHash: request.clientDataHash, supportedAlgorithms: [-7], excludedCredentialIds: excluded), existingSources: [], maxSourceBytes: UInt32(usable), ceremony: ceremony)
                guard self.current(operation) else { return }; operation.terminal = true
                self.completeRegistration(ASPasskeyRegistrationCredential(relyingParty: identity.relyingPartyIdentifier, clientDataHash: request.clientDataHash, credentialID: result.credentialId, attestationObject: result.attestationObject, extensionOutput: nil))
            } catch { self.stop(operation, "Passkey registration could not be completed. Try again.") }
        }
    }
    func assertList(_ parameters: ASPasskeyCredentialRequestParameters) {
        assert(nil, parameters: parameters)
    }
    func assert(_ request: ASPasskeyCredentialRequest?, parameters: ASPasskeyCredentialRequestParameters? = nil) {
        let identity = request?.credentialIdentity as? ASPasskeyCredentialIdentity
        let rp = parameters?.relyingPartyIdentifier ?? identity?.relyingPartyIdentifier
        guard let hash = parameters?.clientDataHash ?? request?.clientDataHash else { cancel("This passkey request is not supported."); return }
        let allowed = parameters?.allowedCredentials ?? (identity.map { [$0.credentialID] } ?? [])
        guard let rp, hash.count == 32, !allowed.contains(where: { $0.count < 16 || $0.count > 1023 }) else { cancel("This passkey request is not supported."); return }
        let operation = begin(); operation.task = Task { [weak self] in guard let self else { return }; do {
            let grant = try await self.acquire(operation); let org = try await self.organization(grant, operation: operation, label: nil); let caps = try await self.capabilities(grant, org)
            let body = try JSONSerialization.data(withJSONObject: ["rp_id": rp, "allowed_credential_ids": allowed.map(NativeVaultPasskeyCodec.base64url)], options: [.sortedKeys])
            let (matchesData, matchesHTTP) = try await self.request("api/vault/native/passkeys/matches", method: "POST", body: body, grant: grant, organization: org); guard matchesHTTP.statusCode == 200 else { throw EnrollmentError.message("No saved passkey matches this website.") }
            let matches = try NativeVaultPasskeyCodec.matches(matchesData).matches; guard let selected = matches.first, self.current(operation) else { throw EnrollmentError.message("No saved passkey matches this website.") }
            if let identity, (selected.credentialID != identity.credentialID || selected.userHandle != identity.userHandle || identity.relyingPartyIdentifier != rp) { throw EnrollmentError.message("The selected passkey no longer matches this request.") }
            let (sourceData, sourceHTTP) = try await self.request("api/vault/native/passkeys/\(selected.itemID)/materialize", method: "POST", body: body, grant: grant, organization: org); guard sourceHTTP.statusCode == 200 else { throw EnrollmentError.message("The selected passkey is no longer available.") }
            let source = try NativeVaultPasskeyCodec.materialize(sourceData, maxSourceBytes: caps.maxSourceBytes); let bridge = NativeOperation(); operation.bridgeOperation = bridge
            let result = try await bridge.authenticate(input: NativeAssertionInput(rpId: rp, clientDataHash: hash, allowedCredentialIds: allowed), canonicalSource: source, maxSourceBytes: UInt32(caps.maxSourceBytes), ceremony: NativeVaultAppleCeremony(operation: operation, coordinator: self, grant: grant, organization: org, mutation: "", label: "", principal: "user", excluded: [], maximum: caps.maxSourceBytes, bodyLimit: caps.maxRequestBodyBytes))
            guard self.current(operation), result.credentialId == selected.credentialID, result.userHandle == selected.userHandle else { throw EnrollmentError.message("The selected passkey no longer matches this request.") }; operation.terminal = true
            self.completeAssertion(ASPasskeyAssertionCredential(userHandle: result.userHandle, relyingParty: rp, signature: result.signature, clientDataHash: hash, authenticatorData: result.authenticatorData, credentialID: result.credentialId, extensionOutput: nil))
        } catch { self.stop(operation, "Passkey authentication could not be completed. Try again.") } }
    }
    fileprivate func verify(_ operation: NativePasskeyOperation) async throws {
        guard current(operation), let context = operation.context else { throw VerificationCallbackError.Denied }
        let allowed: Bool = await withCheckedContinuation { continuation in context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Verify your identity to use this passkey") { allowed, _ in continuation.resume(returning: allowed) } }
        guard allowed, current(operation) else { throw VerificationCallbackError.Denied }
    }
    fileprivate func persist(_ source: Data, operation: NativePasskeyOperation, grant: NativeVaultSessionAccess.Grant, organization: NativeOrganization, mutation: String, label: String, principal: String, excluded: [Data], maximum: Int, bodyLimit: Int) async throws {
        guard current(operation), source.count <= maximum else { throw PersistenceCallbackError.Refused }
        let body = try exactBody(mutation: mutation, source: source, label: label, principal: principal, excluded: excluded); guard body.count <= bodyLimit else { throw PersistenceCallbackError.Refused }
        func valid(_ result: (Data, HTTPURLResponse)) -> Bool { result.1.statusCode == 200 && (try? NativeVaultPasskeyCodec.receipt(result.0, mutationID: mutation, source: source)) != nil }
        func receipt() async -> Bool {
            guard let result = try? await request("api/vault/native/passkeys/receipts/\(mutation)", grant: grant, organization: organization) else { return false }
            return valid(result)
        }
        let first = try? await request("api/vault/native/passkeys", method: "POST", body: body, grant: grant, organization: organization)
        if let first, valid(first), current(operation) { return }
        // Only a transport error or 5xx is ambiguous. A receipt lookup binds a
        // possible committed write to this exact mutation and source before any
        // byte-identical replay; definitive client failures never replay.
        guard first == nil || (first?.1.statusCode ?? 0) >= 500 else { throw PersistenceCallbackError.Refused }
        if await receipt(), current(operation) { return }
        let replay = try? await request("api/vault/native/passkeys", method: "POST", body: body, grant: grant, organization: organization)
        if let replay, valid(replay), current(operation) { return }
        guard replay == nil || (replay?.1.statusCode ?? 0) >= 500 else { throw PersistenceCallbackError.Refused }
        if await receipt(), current(operation) { return }
        throw PersistenceCallbackError.Refused
    }
}

final class NativeVaultAppleCeremony: NativeCeremony, @unchecked Sendable {
    private weak var coordinator: NativeVaultPasskeyCoordinator?; private let operation: NativePasskeyOperation; private let grant: NativeVaultSessionAccess.Grant; private let organization: NativeOrganization; private let mutation: String; private let label: String; private let principal: String; private let excluded: [Data]; private let maximum: Int; private let bodyLimit: Int
    init(operation: NativePasskeyOperation, coordinator: NativeVaultPasskeyCoordinator, grant: NativeVaultSessionAccess.Grant, organization: NativeOrganization, mutation: String, label: String, principal: String, excluded: [Data], maximum: Int, bodyLimit: Int) { self.operation = operation; self.coordinator = coordinator; self.grant = grant; self.organization = organization; self.mutation = mutation; self.label = label; self.principal = principal; self.excluded = excluded; self.maximum = maximum; self.bodyLimit = bodyLimit }
    func verifyUser() async throws { guard let coordinator else { throw VerificationCallbackError.Denied }; try await coordinator.verify(operation) }
    func persistRegistration(canonicalSource: Data) async throws { guard let coordinator else { throw PersistenceCallbackError.Refused }; try await coordinator.persist(canonicalSource, operation: operation, grant: grant, organization: organization, mutation: mutation, label: label, principal: principal, excluded: excluded, maximum: maximum, bodyLimit: bodyLimit) }
}
