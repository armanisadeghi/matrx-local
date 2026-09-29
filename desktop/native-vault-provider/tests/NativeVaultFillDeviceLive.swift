import AuthenticationServices
import CryptoKit
import Foundation

// LIVE harness (access ladder T-30 / T-30c): drives the actual password-provider
// controller and real Secure Enclave keys against a LOCAL aidream, as
// admin@admin.com. Only the Keychain store is in memory (an unsigned harness has
// no keychain-access-group entitlement), the Apple completion/cancel callbacks are
// sinks, and the provider's reconnect sheet is replaced by swapping to a freshly
// minted provider session. Never prints a token or a password.
//
// Env: FILL_LIVE_ORIGIN, FILL_LIVE_ORG, FILL_LIVE_DOMAIN, FILL_LIVE_GRANT1..3
// (provider sessions from native-vault-mint-test-session.py), FILL_LIVE_WEB (a
// plain web sign-in token: the attacker's starting point), FILL_LIVE_PASSWORD
// (the test account password, for the step-up prompt), FILL_LIVE_APPROVE (a
// command that records a passkey approval for "<user> <thumbprint>").

/// A local aidream talks to the one remote database from this Mac, so a native
/// match takes ~10 s here (well under 1 s next to the DB in AWS). The shipped
/// 10 s BoundedTransport would time out, so the harness sends with a 60 s
/// ephemeral session; everything above the wire is the shipped code.
final class LoggingTransport: NativeVaultPasswordTransporting {
    let session: URLSession = { let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 60; c.urlCache = nil; return URLSession(configuration: c) }()
    var log: [(URLRequest, Int, Data)] = []
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        session.dataTask(with: request) { data, response, error in
            let result: Result<(Data, HTTPURLResponse), Error> = { if let http = response as? HTTPURLResponse { return .success((data ?? Data(), http)) }; return .failure(error ?? URLError(.badServerResponse)) }()
            DispatchQueue.main.async { self.log.append((request, (response as? HTTPURLResponse)?.statusCode ?? -1, data ?? Data())); completion(result) }
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
        let deadline = Date().addingTimeInterval(180)
        while !condition() && Date() < deadline { _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02)) }
    }
    struct Outcome { var completed: (String, String)?; var cancel: String?; var prompts: [NativeFillStepUpRequest]; var offered: [String]; var log: [(URLRequest, Int, Data)] }
    static var created: [String] = []

    /// One fill through the shipped controller. `grants` is the provider session
    /// in use; a "Reconnect and approve" answer moves to the next one.
    @MainActor static func fill(_ grants: [NativeVaultSessionAccess.Grant], device: NativeVaultFillDevice, reconnect: Bool = true, stepUp: @escaping (NativeFillStepUpRequest) -> NativeFillStepUp) -> Outcome {
        let transport = LoggingTransport()
        var index = 0
        let c = CredentialProviderViewController()
        c.nativePasswordAPIOrigin = URL(string: env("FILL_LIVE_ORIGIN"))!
        c.nativePasswordKeyOverride = "public-build-key"; c.nativePasswordTransport = transport
        c.nativePasswordAuthorize = { $0(true) }; c.nativePasswordAcquire = { $0(.success(grants[index])) }
        c.nativePasswordCurrentState = { NativePasswordCurrentState(generation: grants[index].generation, subject: grants[index].subject) }
        let org = env("FILL_LIVE_ORG")
        c.nativePasswordOrganizationChoice = { orgs in orgs.firstIndex { $0.id == org } }
        c.nativePasswordMatchChoice = { _ in 0 }
        c.nativeFillDevice = device
        var outcome = Outcome(completed: nil, cancel: nil, prompts: [], offered: [], log: [])
        c.nativeFillStepUpPrompt = { request in outcome.prompts.append(request); return stepUp(request) }
        c.nativeFillReapprove = { message, resume in
            outcome.offered.append(message)
            if reconnect, index + 1 < grants.count { index += 1; resume(true) } else { resume(false) }
        }
        c.nativePasswordCompleteSink = { credential, done in outcome.completed = credential; done() }
        c.nativePasswordCancelSink = { outcome.cancel = $0.localizedDescription }
        c.prepareCredentialList(for: [ASCredentialServiceIdentifier(identifier: env("FILL_LIVE_DOMAIN"), type: .domain)])
        waitUntil { outcome.completed != nil || outcome.cancel != nil }
        outcome.log = transport.log
        for (request, status, data) in outcome.log {
            let code = NativePasswordCodec.errorCode(data).map { " \($0)" } ?? ""
            print("   \(request.httpMethod ?? "GET") \(request.url!.path) -> \(status)\(code)\(request.value(forHTTPHeaderField: "X-Matrx-Fill-Signature") == nil ? "" : " [signed]")")
            if request.url!.path == "/api/vault/fill-devices", status == 200, let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let id = o["id"] as? String { created.append(id) }
        }
        if let cancel = outcome.cancel { print("   cancelled: \(cancel)") }
        if let done = outcome.completed { print("   filled: username \(done.0.count) chars, password \(done.1.count) chars") }
        return outcome
    }
    static func raw(_ request: URLRequest) -> (Int, String) {
        let sem = DispatchSemaphore(value: 0); var out = (-1, "")
        let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 90
        URLSession(configuration: c).dataTask(with: request) { data, response, _ in out = ((response as? HTTPURLResponse)?.statusCode ?? -1, String(decoding: data ?? Data(), as: UTF8.self)); sem.signal() }.resume()
        sem.wait(); return out
    }
    static func api(_ path: String, token: String, method: String = "GET", json: [String: Any]? = nil) -> URLRequest {
        var r = URLRequest(url: URL(string: env("FILL_LIVE_ORIGIN"))!.appendingPathComponent(path)); r.httpMethod = method
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization"); r.setValue(env("FILL_LIVE_ORG"), forHTTPHeaderField: "X-Organization-Id")
        if let json { r.httpBody = try! JSONSerialization.data(withJSONObject: json); r.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return r
    }
    /// Re-sign a captured materialize with a chosen key/device and a FRESH nonce.
    static func resigned(_ base: URLRequest, itemID: String, subject: String, deviceID: String, sign: (Data) throws -> Data, grant: NativeVaultSessionAccess.Grant) -> URLRequest {
        var r = base; r.setValue("Bearer \(grant.accessToken)", forHTTPHeaderField: "Authorization")
        let ts = String(Int64(Date().timeIntervalSince1970 * 1000)); let nonce = NativeVaultFillWire.nonce()
        let message = NativeVaultFillWire.message(surface: NativeVaultFillWire.surface, itemID: itemID, timestampMs: ts, nonce: nonce, deviceID: deviceID, userID: subject, body: base.httpBody ?? Data())
        r.setValue(deviceID, forHTTPHeaderField: "X-Matrx-Fill-Device"); r.setValue(ts, forHTTPHeaderField: "X-Matrx-Fill-Timestamp"); r.setValue(nonce, forHTTPHeaderField: "X-Matrx-Fill-Nonce")
        r.setValue(NativeVaultFillWire.base64URL(try! sign(message)), forHTTPHeaderField: "X-Matrx-Fill-Signature")
        return r
    }
    static func shell(_ command: String) -> Int32 {
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/bash"); p.arguments = ["-c", command]
        try! p.run(); p.waitUntilExit(); return p.terminationStatus
    }

    @MainActor static func main() {
        let password = env("FILL_LIVE_PASSWORD")
        let g1 = grant(env("FILL_LIVE_GRANT1")), g2 = grant(env("FILL_LIVE_GRANT2")), g3 = grant(env("FILL_LIVE_GRANT3"))
        let web = (try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: env("FILL_LIVE_WEB")))) as! [String: Any])["access_token"] as! String
        check(SecureEnclave.isAvailable, "this Mac has a Secure Enclave (the real key factory is used)")
        let device = NativeVaultFillDevice(store: NativeVaultFillDeviceMemoryStore(), keys: .secureEnclave)
        let usePassword: (NativeFillStepUpRequest) -> NativeFillStepUp = { _ in .password(password) }

        print("1. first use: register the enclave key with the password step-up, then a signed fill")
        let first = fill([g1], device: device, stepUp: usePassword)
        check(first.completed != nil && first.prompts.count == 1 && first.prompts[0].notice == nil, "first use asks once, registers, fills")
        let paths = first.log.map { $0.0.url!.path }
        check(paths.count == 5 && paths[2] == "/api/vault/fill-devices/step-up-methods" && first.log[2].1 == 200 && paths[3] == "/api/vault/fill-devices" && paths[4].hasSuffix("/materialize") && first.log[3].1 == 200 && first.log[4].1 == 200, "live step-up methods (200), register (200), signed materialize (200)")
        let firstState = try! device.current(subject: g1.subject); let firstID = firstState.record.device_id!
        let itemID = String(paths[4].split(separator: "/").dropLast().last!)

        print("2. second fill: no prompt, fresh nonce")
        let second = fill([g1], device: device, stepUp: { _ in fatalError("a registered Mac must not ask") })
        check(second.completed != nil && second.prompts.isEmpty && second.log.count == 3, "registered Mac fills without asking")
        let signed = second.log.last!.0

        print("3. attack: replay that exact signed request")
        let replay = raw(signed)
        check(replay.0 == 403 && replay.1.contains("fill_device_required"), "a replayed nonce is refused (\(replay.0))")

        print("4. attack: the same request without a signature")
        var unsigned = signed; ["X-Matrx-Fill-Device", "X-Matrx-Fill-Timestamp", "X-Matrx-Fill-Nonce", "X-Matrx-Fill-Signature"].forEach { unsigned.setValue(nil, forHTTPHeaderField: $0) }
        let bare = raw(unsigned)
        check(bare.0 == 403 && bare.1.contains("fill_device_required"), "an unsigned native materialize is refused (\(bare.0))")

        print("5. attack: a fresh request signed by a DIFFERENT key claiming this Mac's device id")
        let stranger = P256.Signing.PrivateKey()
        let wrong = raw(resigned(signed, itemID: itemID, subject: g1.subject, deviceID: firstID, sign: { try stranger.signature(for: $0).rawRepresentation }, grant: g1))
        check(wrong.0 == 403 && wrong.1.contains("fill_device_required"), "a wrong-key signature is refused (\(wrong.0))")
        let control = raw(resigned(signed, itemID: itemID, subject: g1.subject, deviceID: firstID, sign: { try firstState.key.sign($0) }, grant: g1))
        check(control.0 == 200, "control: the same re-signing with the REAL key fills (\(control.0)) — so the refusals above are the key, not the harness")

        print("6. attack: a script holding only a web sign-in registers a native device (even WITH the password)")
        let jwk = NativeVaultFillWire.publicJWK(x963: P256.Signing.PrivateKey().publicKey.x963Representation)!
        let webRegister = raw(api("api/vault/fill-devices", token: web, method: "POST", json: ["public_key_jwk": jwk, "label": "script", "password": password]))
        check(webRegister.0 == 403 && webRegister.1.contains("extension_session_required"), "a web-session registration is refused (\(webRegister.0))")
        let fakePasskey = raw(api("api/vault/fill-devices/approvals", token: web, method: "POST", json: ["key_thumbprint": NativeVaultFillWire.thumbprint(jwk)!, "challenge_id": "forged", "credential": ["id": "forged", "type": "public-key"]]))
        check(fakePasskey.0 == 403 && fakePasskey.1.contains("step_up_failed"), "a forged passkey approval is refused by the auth server's verifier (\(fakePasskey.0))")

        print("7. turn this Mac off (the web Vault's revoke), then try to fill")
        let revoked = raw(api("api/vault/fill-devices/\(firstID)/revoke", token: web, method: "POST"))
        check(revoked.0 == 200 && revoked.1.contains("revoked_by_owner"), "revoke answered \(revoked.0)")
        let off = fill([g1], device: device, reconnect: false, stepUp: usePassword)
        check(off.completed == nil && off.log.contains { $0.1 == 401 && NativePasswordCodec.errorCode($0.2) == "fill_device_revoked" }, "the server says the session ended BECAUSE filling was turned off (401 fill_device_revoked)")
        check(off.offered == [NativeVaultFillDeviceError.turnedOffMessage] && off.cancel == NativeVaultFillDeviceError.turnedOffMessage, "the Mac says 'Password filling was turned off for this Mac. Approve it again to keep filling.' with one action (never 'needs reconnect')")
        let revokedKey = raw(resigned(signed, itemID: itemID, subject: g1.subject, deviceID: firstID, sign: { try firstState.key.sign($0) }, grant: g1))
        check(revokedKey.0 == 403 || revokedKey.0 == 401, "attack: the revoked key, correctly signed, is refused (\(revokedKey.0))")

        print("8. the one action: reconnect and approve, and the same fill continues")
        let back = fill([g1, g2], device: device, stepUp: usePassword)
        check(back.completed != nil && back.offered.count == 1 && back.prompts.count == 1, "reconnect, one approval, filled")
        let registrations = back.log.filter { $0.0.url!.path == "/api/vault/fill-devices" }.map { $0.1 }
        check(registrations == [200], "a NEW enclave key is registered straight away (the revoked one is never offered) (\(registrations))")
        let secondID = try! device.current(subject: g2.subject).record.device_id!
        check(secondID != firstID, "new device id after re-approval")
        let oldKey = raw(resigned(signed, itemID: itemID, subject: g2.subject, deviceID: firstID, sign: { try firstState.key.sign($0) }, grant: g2))
        check(oldKey.0 == 403, "attack: the OLD key after re-registering, on the new session, is refused (\(oldKey.0))")

        print("9. passkey approval, the equal alternative (new Mac key, new provider session)")
        _ = raw(api("api/vault/fill-devices/\(secondID)/revoke", token: web, method: "POST"))
        let passkeyDevice = NativeVaultFillDevice(store: NativeVaultFillDeviceMemoryStore(), keys: .secureEnclave)
        var asks = 0
        let viaPasskey = fill([g3], device: passkeyDevice) { request in
            asks += 1
            let key = URLComponents(url: request.approvalURL, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "key" }!.value!
            if asks == 2 {
                // The person approves on aimatrx.com with their passkey. A live
                // WebAuthn ceremony needs a real authenticator: the fixture records
                // the exact row the verified approval writes (stated, not hidden).
                check(shell("cd \"$AIDREAM\" && uv run python \"$FILL_LIVE_APPROVE\" \(g3.subject) \(key) >/dev/null") == 0, "stand-in for a verified passkey approval of key \(key.prefix(8))…")
            }
            return .passkeyApproved
        }
        let passkeyRegs = viaPasskey.log.filter { $0.0.url!.path == "/api/vault/fill-devices" }
        let sentPassword = passkeyRegs.contains { ((try? JSONSerialization.jsonObject(with: $0.0.httpBody!)) as? [String: Any])?["password"] != nil }
        check(passkeyRegs.map { $0.1 } == [403, 200] && !sentPassword, "not approved yet is refused (step_up_required), approved is claimed — no password ever sent")
        check(viaPasskey.prompts.count == 2 && viaPasskey.prompts[1].notice?.contains("No passkey approval") == true && viaPasskey.completed != nil, "the Mac re-asks with the reason, then fills")
        let claimedAgain = raw(api("api/vault/fill-devices", token: g3.accessToken, method: "POST", json: ["public_key_jwk": try! passkeyDevice.current(subject: g3.subject).key.publicJWK, "label": "replay"]))
        check(claimedAgain.0 == 403 && claimedAgain.1.contains("step_up_required"), "attack: a passkey approval is single-use — re-registering without a password is refused (\(claimedAgain.0))")

        print("10. cleanup: every device this run registered is turned off")
        for id in Set(created) {
            let r = raw(api("api/vault/fill-devices/\(id)/revoke", token: web, method: "POST"))
            check(r.0 == 200, "revoked \(id.prefix(8))…")
        }
        print("LIVE native fill-device harness passed")
    }
}
