import Foundation

@main
struct NativeVaultCodecCorpus {
    private static let subject = "11111111-1111-4111-8111-111111111111"
    private static let generation = "22222222-2222-4222-8222-222222222222"

    static func main() {
        let cases: [(String, () throws -> Void)] = [
            ("strict JSON accepts separate-object keys and a colon in strings", strictJSONAllowsIndependentKeys),
            ("strict JSON rejects escaped duplicate keys", strictJSONRejectsEscapedDuplicate),
            ("strict JSON rejects surrogate duplicate keys", strictJSONRejectsSurrogateDuplicate),
            ("strict JSON enforces depth eight", strictJSONEnforcesDepth),
            ("strict JSON validates UTF-8 and surrogate pairs", strictJSONValidatesUnicode),
            ("strict JSON refuses malformed punctuation without process termination", strictJSONRejectsMalformedPunctuation),
            ("token envelope closes key, type, number, scope, and size contracts", tokenContract),
            ("userinfo envelope closes key, UUID, email, and bool contracts", userinfoContract),
            ("public state envelope closes exact nullable UUID contract", publicStateContract),
            ("private active and refresh-pending envelopes remain disjoint", privateSessionContract),
            ("typed persisted envelopes map every parse failure to their fixed remedy", persistedEnvelopeErrors),
        ]
        var failures = 0
        for (name, body) in cases {
            do { try body(); print("PASS: \(name)") }
            catch { failures += 1; fputs("FAIL: \(name): \(error)\n", stderr) }
        }
        if failures != 0 { exit(1) }
    }

    private static func data(_ value: String) -> Data { Data(value.utf8) }
    private static func parse(_ input: Data) throws { var parser = try StrictJSON(input); _ = try parser.parse() }
    private static func accepts(_ body: () throws -> Void) throws { try body() }
    private static func rejects(_ body: () throws -> Void) throws {
        do { try body(); throw CorpusFailure.expectedRefusal }
        catch CorpusFailure.expectedRefusal { throw CorpusFailure.expectedRefusal }
        catch { }
    }

    private static func strictJSONAllowsIndependentKeys() throws {
        try accepts { try parse(data(#"[{"key":1},{"key":2,"message":"value: with colon"}]"#)) }
    }

    private static func strictJSONRejectsEscapedDuplicate() throws {
        try rejects { try parse(data(#"{"a":1,"\u0061":2}"#)) }
    }

    private static func strictJSONRejectsSurrogateDuplicate() throws {
        try rejects { try parse(data(#"{"\uD83D\uDE00":1,"😀":2}"#)) }
    }

    private static func strictJSONEnforcesDepth() throws {
        let eight = String(repeating: "{\"a\":", count: 7) + "{\"a\":null}" + String(repeating: "}", count: 7)
        let nine = "{\"a\":" + eight + "}"
        try accepts { try parse(data(eight)) }
        try rejects { try parse(data(nine)) }
    }

    private static func strictJSONValidatesUnicode() throws {
        try accepts { try parse(data(#"{"word":"é","pair":"\uD83D\uDE00"}"#)) }
        try rejects { try parse(Data([123, 34, 120, 34, 58, 34, 0xff, 34, 125])) }
        try rejects { try parse(data(#"{"word":"\uD800"}"#)) }
        try rejects { try parse(data(#"{"word":"\uDC00"}"#)) }
    }

    private static func strictJSONRejectsMalformedPunctuation() throws {
        for malformed in [#"{"a" 1}"#, #"{"a":1 "b":2}"#, #"{"a":1,}"#, #"{"a":1x}"#] {
            try rejects { try parse(data(malformed)) }
        }
    }

    private static func tokenContract() throws {
        let valid = #"{"access_token":"access","token_type":"Bearer","expires_in":3600,"refresh_token":"refresh","scope":"openid email offline_access"}"#
        try accepts { _ = try VaultEnvelopeCodec.token(data(valid)) }
        try accepts { _ = try VaultEnvelopeCodec.token(data(#"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh"}"#)) }
        // Shape observed from a live authorized Auth token response. The native grant
        // still derives its subject only from the separately validated userinfo
        // response; these serializer metadata fields are never persisted.
        let liveSerializerShape = #"{"access_token":"access","token_type":"bearer","expires_in":3600,"expires_at":1790000000,"refresh_token":"refresh","user":{"id":"11111111-1111-4111-8111-111111111111"},"weak_password":null}"#
        try accepts { _ = try VaultEnvelopeCodec.token(data(liveSerializerShape)) }
        // Supabase OAuth authorization-code grant with the documented maximum
        // GOTRUE_JWT_EXP of seven days. This failed against the former 86_400 cap.
        let oauthCodeGrantSevenDays = #"{"access_token":"access","token_type":"bearer","expires_in":604800,"refresh_token":"refresh","scope":"openid email offline_access","id_token":"id-token"}"#
        try accepts { _ = try VaultEnvelopeCodec.token(data(oauthCodeGrantSevenDays)) }
        for invalid in [
            #"{"token_type":"bearer","expires_in":1,"refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh","extra":true}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1.5,"refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":0,"refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":604801,"refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"basic","expires_in":1,"refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh","scope":"openid email"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh","id_token":false}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"expires_at":"1790000000","refresh_token":"refresh"}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh","user":false}"#,
            #"{"access_token":"access","token_type":"bearer","expires_in":1,"refresh_token":"refresh","weak_password":false}"#,
        ] { try rejects { _ = try VaultEnvelopeCodec.token(data(invalid)) } }
        let huge = String(repeating: "a", count: 16 * 1024 + 1)
        try rejects { _ = try VaultEnvelopeCodec.token(data(#"{"access_token":"\#(huge)","token_type":"bearer","expires_in":1,"refresh_token":"refresh"}"#)) }
    }

    private static func userinfoContract() throws {
        try accepts { _ = try VaultEnvelopeCodec.userinfo(data(#"{"sub":"\#(subject)","email":"person@example.com","email_verified":true}"#)) }
        for invalid in [
            #"{}"#,
            #"{"sub":"\#(subject)","unknown":true}"#,
            #"{"sub":"not-a-uuid"}"#,
            #"{"sub":"\#(subject)","email_verified":"true"}"#,
            #"{"sub":"\#(subject)","email":null}"#,
        ] { try rejects { _ = try VaultEnvelopeCodec.userinfo(data(invalid)) } }
        let oversized = String(repeating: "a", count: 321) + "@x"
        try rejects { _ = try VaultEnvelopeCodec.userinfo(data(#"{"sub":"\#(subject)","email":"\#(oversized)"}"#)) }
    }

    private static func publicStateContract() throws {
        let valid = #"{"version":1,"generation":"\#(generation)","host_subject":null,"provider_subject":"\#(subject)"}"#
        try accepts { _ = try VaultEnvelopeCodec.publicState(data(valid)) }
        for invalid in [
            #"{"version":1,"generation":"\#(generation)","host_subject":null}"#,
            #"{"version":1,"generation":"\#(generation)","host_subject":null,"provider_subject":null,"unknown":true}"#,
            #"{"version":"1","generation":"\#(generation)","host_subject":null,"provider_subject":null}"#,
            #"{"version":1,"generation":"\#(generation)","host_subject":false,"provider_subject":null}"#,
        ] { try rejects { _ = try VaultEnvelopeCodec.publicState(data(invalid)) } }
    }

    private static func privateSessionContract() throws {
        let active = #"{"version":1,"phase":"active","subject":"\#(subject)","generation":"\#(generation)","access_token":"access","refresh_token":"refresh","expires_at_ms":1}"#
        let pending = #"{"version":1,"phase":"refresh_pending","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1}"#
        try accepts { let value = try PrivateSessionCodec.decode(data(active)); guard value.access_token == "access" else { throw CorpusFailure.badValue } }
        try accepts { let value = try PrivateSessionCodec.decode(data(pending)); guard value.access_token.isEmpty && value.refresh_token.isEmpty else { throw CorpusFailure.badValue } }
        for (source, tokensExpected) in [(active, true), (pending, false)] {
            let decoded = try PrivateSessionCodec.decode(data(source))
            let encoded = try JSONEncoder().encode(decoded)
            let object = try StrictEnvelope.object(encoded, required: ["version", "phase", "subject", "generation", "expires_at_ms"], optional: ["access_token", "refresh_token"])
            let hasTokens = object["access_token"] != nil || object["refresh_token"] != nil
            guard hasTokens == tokensExpected else { throw CorpusFailure.badValue }
            _ = try PrivateSessionCodec.decode(encoded)
        }
        for invalid in [
            #"{"version":1,"phase":"active","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1}"#,
            #"{"version":1,"phase":"refresh_pending","subject":"\#(subject)","generation":"\#(generation)","access_token":"access","expires_at_ms":1}"#,
            #"{"version":1,"phase":"active","subject":"\#(subject)","generation":"\#(generation)","access_token":"access","refresh_token":"refresh","expires_at_ms":"1"}"#,
            #"{"version":1,"phase":"other","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1}"#,
            #"{"version":1,"phase":"refresh_pending","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1,"unknown":true}"#,
        ] { try rejects { _ = try PrivateSessionCodec.decode(data(invalid)) } }
        try rejects { _ = try PrivateSessionCodec.decode(Data(repeating: 32, count: 48 * 1024 + 1)) }
    }

    private static func persistedEnvelopeErrors() throws {
        let publicFailures = [#"{"version" 1}"#, #"[]"#, #"{"version":1,"version":1,"generation":"\#(generation)","host_subject":null,"provider_subject":null}"#, #"{"version":1,"generation":"\#(generation)","host_subject":null,"provider_subject":null,"unknown":true}"#, #"{"version":2,"generation":"\#(generation)","host_subject":null,"provider_subject":null}"#]
        for value in publicFailures { try fixedError("Vault status is corrupt. Reconnect the provider.") { _ = try VaultEnvelopeCodec.publicState(data(value)) } }
        let privateFailures = [#"{"version" 1}"#, #"[]"#, #"{"version":1,"version":1,"phase":"refresh_pending","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1}"#, #"{"version":1,"phase":"refresh_pending","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1,"unknown":true}"#, #"{"version":1,"phase":"active","subject":"\#(subject)","generation":"\#(generation)","expires_at_ms":1}"#]
        for value in privateFailures { try fixedError("Vault session is corrupt. Reconnect the provider.") { _ = try PrivateSessionCodec.decode(data(value)) } }
    }

    private static func fixedError(_ expected: String, _ body: () throws -> Void) throws {
        do { try body(); throw CorpusFailure.expectedRefusal }
        catch let error as LocalizedError {
            guard error.errorDescription == expected else { throw CorpusFailure.badValue }
        }
    }
}

private enum CorpusFailure: Error { case expectedRefusal, badValue }
