import Foundation
@preconcurrency import LocalAuthentication

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
