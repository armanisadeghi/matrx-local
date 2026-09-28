import AuthenticationServices
import CryptoKit
import Foundation

// LIVE harness (access ladder T-30): drives the actual password-provider
// controller and a real Secure Enclave key against
// a LOCAL aidream, as admin@admin.com. Only the Keychain store is in memory
// (an unsigned harness has no keychain-access-group entitlement) and the Apple
// completion/cancel callbacks are sinks. Never prints a token or a password.
//
// Env: FILL_LIVE_ORIGIN, FILL_LIVE_ORG, FILL_LIVE_DOMAIN, FILL_LIVE_GRANT1,
// FILL_LIVE_GRANT2 (JSON files from native-vault-mint-test-session.py),
// FILL_LIVE_PASSWORD (the test account password, for the step-up prompt).

/// A local aidream talks to the one remote database from this Mac, so a
/// native match takes ~10 s here (well under 1 s next to the DB in AWS). The
/// shipped 10 s BoundedTransport would time out, so the harness sends with a
/// 60 s ephemeral session; everything above the wire is the shipped code.
final class LoggingTransport: NativeVaultPasswordTransporting {
    let session: URLSession = { let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 60; c.urlCache = nil; return URLSession(configuration: c) }()
    var log: [(URLRequest, Int)] = []
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        session.dataTask(with: request) { data, response, error in
            let result: Result<(Data, HTTPURLResponse), Error> = { if let http = response as? HTTPURLResponse { return .success((data ?? Data(), http)) }; return .failure(error ?? URLError(.badServerResponse)) }()
            DispatchQueue.main.async { self.log.append((request, (response as? HTTPURLResponse)?.statusCode ?? -1)); completion(result) }
        }.resume()
    }
}

@main
struct NativeVaultFillDeviceLive {
    static func env(_ key: String) -> String { guard let v = ProcessInfo.processInfo.environment[key], !v.isEmpty else { fatalError("missing \(key)") }; return v }
    static func grant(_ path: String) -> NativeVaultSessionAccess.Grant {
        let o = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String: Any]
        return NativeVaultSessionAccess.Grant(accessToken: o["access_token"] as! String, subject: o["user_id"] as! String, generation: "live-harness")
    }
    static func check(_ ok: Bool, _ message: String) { print((ok ? "PASS " : "FAIL ") + message); if !ok { exit(1) } }
    @MainActor static func waitUntil(_ condition: () -> Bool) {
        let deadline = Date().addingTimeInterval(90)
        while !condition() && Date() < deadline { _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02)) }
    }
    struct Outcome { var completed: (String, String)?; var cancel: String?; var prompts: [String?]; var log: [(URLRequest, Int)] }

    @MainActor static func fill(_ grant: NativeVaultSessionAccess.Grant, device: NativeVaultFillDevice, password: String) -> Outcome {
        let transport = LoggingTransport()
        let c = CredentialProviderViewController()
        c.nativePasswordAPIOrigin = URL(string: env("FILL_LIVE_ORIGIN"))!
        c.nativePasswordKeyOverride = "public-build-key"; c.nativePasswordTransport = transport
        c.nativePasswordAuthorize = { $0(true) }; c.nativePasswordAcquire = { $0(.success(grant)) }
        c.nativePasswordCurrentState = { NativePasswordCurrentState(generation: grant.generation, subject: grant.subject) }
        let org = env("FILL_LIVE_ORG")
        c.nativePasswordOrganizationChoice = { orgs in orgs.firstIndex { $0.id == org } }
        c.nativePasswordMatchChoice = { _ in 0 }
        c.nativeFillDevice = device
        var outcome = Outcome(completed: nil, cancel: nil, prompts: [], log: [])
        c.nativeFillPasswordPrompt = { notice in outcome.prompts.append(notice); return password }
        c.nativePasswordCompleteSink = { credential, done in outcome.completed = credential; done() }
        c.nativePasswordCancelSink = { outcome.cancel = $0.localizedDescription }
        c.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: env("FILL_LIVE_DOMAIN"), type: .domain)])
        waitUntil { outcome.completed != nil || outcome.cancel != nil }
        outcome.log = transport.log
        for (request, status) in outcome.log { print("   \(request.httpMethod ?? "GET") \(request.url!.path) -> \(status)\(request.value(forHTTPHeaderField: "X-Matrx-Fill-Signature") == nil ? "" : " [signed]")") }
        if let cancel = outcome.cancel { print("   cancelled: \(cancel)") }
        if let done = outcome.completed { print("   filled: username \(done.0.count) chars, password \(done.1.count) chars") }
        return outcome
    }
    static func raw(_ request: URLRequest) -> (Int, String) {
        let sem = DispatchSemaphore(value: 0); var out = (-1, "")
        URLSession.shared.dataTask(with: request) { data, response, _ in out = ((response as? HTTPURLResponse)?.statusCode ?? -1, String(decoding: data ?? Data(), as: UTF8.self)); sem.signal() }.resume()
        sem.wait(); return out
    }

    @MainActor static func main() {
        let password = env("FILL_LIVE_PASSWORD")
        let g1 = grant(env("FILL_LIVE_GRANT1")), g2 = grant(env("FILL_LIVE_GRANT2"))
        check(SecureEnclave.isAvailable, "this Mac has a Secure Enclave (the real key factory is used)")
        let device = NativeVaultFillDevice(store: NativeVaultFillDeviceMemoryStore(), keys: .secureEnclave)

        print("1. first use: register the enclave key with the password step-up, then a signed fill")
        let first = fill(g1, device: device, password: password)
        check(first.completed != nil && first.prompts == [nil], "first use asks once, registers, fills")
        let paths = first.log.map { $0.0.url!.path }
        check(paths.count == 4 && paths[2] == "/api/vault/fill-devices" && paths[3].hasSuffix("/materialize") && first.log[2].1 == 200 && first.log[3].1 == 200, "register (200) then signed materialize (200)")
        let deviceID = try! device.current(subject: g1.subject).record.device_id!

        print("2. second fill: no prompt, fresh nonce")
        let second = fill(g1, device: device, password: password)
        check(second.completed != nil && second.prompts.isEmpty && second.log.count == 3, "registered Mac fills without asking")
        let signed = second.log.last!.0

        print("3. replaying that exact signed request")
        let replay = raw(signed)
        check(replay.0 == 403 && replay.1.contains("fill_device_required"), "a replayed nonce is refused (\(replay.0))")

        print("4. the same request without a signature")
        var unsigned = signed; ["X-Matrx-Fill-Device", "X-Matrx-Fill-Timestamp", "X-Matrx-Fill-Nonce", "X-Matrx-Fill-Signature"].forEach { unsigned.setValue(nil, forHTTPHeaderField: $0) }
        let bare = raw(unsigned)
        check(bare.0 == 403, "an unsigned native materialize is refused (\(bare.0))")

        print("5. turn this Mac off (the web Vault's revoke), then try to fill")
        var revoke = URLRequest(url: URL(string: env("FILL_LIVE_ORIGIN"))!.appendingPathComponent("api/vault/fill-devices/\(deviceID)/revoke")); revoke.httpMethod = "POST"
        revoke.setValue("Bearer \(g2.accessToken)", forHTTPHeaderField: "Authorization"); revoke.setValue(env("FILL_LIVE_ORG"), forHTTPHeaderField: "X-Organization-Id")
        let revoked = raw(revoke)
        check(revoked.0 == 200 && revoked.1.contains("revoked_by_owner"), "revoke answered \(revoked.0)")
        let afterRevoke = fill(g1, device: device, password: password)
        check(afterRevoke.completed == nil && afterRevoke.cancel != nil, "a turned-off Mac does not fill and says why")

        print("6. reconnect (new provider sign-in) and re-approve with the password")
        let reapproved = fill(g2, device: device, password: password)
        check(reapproved.completed != nil && reapproved.prompts.count == 1, "one password prompt re-approves")
        let registrations = reapproved.log.filter { $0.0.url!.path == "/api/vault/fill-devices" }.map { $0.1 }
        check(registrations == [403, 200], "the revoked key is refused and a NEW enclave key is registered (\(registrations))")
        check(try! device.current(subject: g2.subject).record.device_id != deviceID, "new device id after re-approval")
        // Leave the test account clean: turn off the device this run registered
        // (which also ends that provider sign-in).
        let finalID = try! device.current(subject: g2.subject).record.device_id!
        var cleanup = URLRequest(url: URL(string: env("FILL_LIVE_ORIGIN"))!.appendingPathComponent("api/vault/fill-devices/\(finalID)/revoke")); cleanup.httpMethod = "POST"
        cleanup.setValue("Bearer \(g2.accessToken)", forHTTPHeaderField: "Authorization"); cleanup.setValue(env("FILL_LIVE_ORG"), forHTTPHeaderField: "X-Organization-Id")
        check(raw(cleanup).0 == 200, "cleanup: this run's device is turned off")
        print("LIVE native fill-device harness passed")
    }
}
