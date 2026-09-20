import Foundation

/// The provider's closed JSON contracts. This file is compiled into the real
/// application extension and independently by the executable corpus below.
enum EnrollmentError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { value } else { nil } }
}

struct NativeSuggestions: Codable, Equatable {
    let organization_id: String?
    let revision: String
    let status: String
    let refreshed_at_ms: Int64?
    let count: Int
    let unsupported_count: Int

    static func empty() -> NativeSuggestions {
        NativeSuggestions(organization_id: nil, revision: UUID().canonical, status: "empty", refreshed_at_ms: nil, count: 0, unsupported_count: 0)
    }
}

struct PublicState: Codable {
    let version: Int
    let generation: String
    let host_subject: String?
    let provider_subject: String?
    let suggestions: NativeSuggestions
    private enum CodingKeys: String, CodingKey { case version, generation, host_subject, provider_subject, suggestions }
    init(version: Int = 2, generation: String, host_subject: String?, provider_subject: String?, suggestions: NativeSuggestions = .empty()) {
        self.version = version; self.generation = generation; self.host_subject = host_subject; self.provider_subject = provider_subject; self.suggestions = suggestions
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(version, forKey: .version); try c.encode(generation, forKey: .generation); try c.encode(host_subject, forKey: .host_subject); try c.encode(provider_subject, forKey: .provider_subject)
        if version == 2 { try c.encode(suggestions, forKey: .suggestions) }
    }
}

struct PrivateSession: Codable {
    let version: Int
    let phase: String
    let subject: String
    let generation: String
    let access_token: String
    let refresh_token: String
    let expires_at_ms: Int64

    private enum CodingKeys: String, CodingKey {
        case version, phase, subject, generation, access_token, refresh_token, expires_at_ms
    }

    init(version: Int, phase: String, subject: String, generation: String, access_token: String, refresh_token: String, expires_at_ms: Int64) {
        self.version = version
        self.phase = phase
        self.subject = subject
        self.generation = generation
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at_ms = expires_at_ms
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(version, forKey: .version)
        try container.encode(phase, forKey: .phase)
        try container.encode(subject, forKey: .subject)
        try container.encode(generation, forKey: .generation)
        try container.encode(expires_at_ms, forKey: .expires_at_ms)
        if phase == "active" {
            try container.encode(access_token, forKey: .access_token)
            try container.encode(refresh_token, forKey: .refresh_token)
        }
    }
}

struct Token {
    let access_token: String
    let token_type: String
    let expires_in: Int
    let refresh_token: String
    let scope: String?
}

struct Identity {
    let sub: String
    let email: String?
    let email_verified: Bool?
}

indirect enum JSONValue {
    case object([String: JSONValue])
    case array([JSONValue])
    case string(String)
    case number(String)
    case bool(Bool)
    case null
}

/// Deliberately small JSON parser for fixed Auth/Keychain envelopes. It
/// validates source UTF-8 and checks decoded keys in every object, including
/// keys expressed with Unicode escapes.
struct StrictJSON {
    private var bytes: [UInt8]
    private var index = 0

    init(_ data: Data, maxBytes: Int = 64 * 1024) throws {
        guard maxBytes > 0, maxBytes <= 96 * 1024, data.count <= maxBytes else { throw Self.bad() }
        bytes = Array(data)
    }

    mutating func parse() throws -> JSONValue {
        let parsed = try value(1)
        space()
        guard index == bytes.count else { throw Self.bad() }
        return parsed
    }

    private mutating func value(_ depth: Int) throws -> JSONValue {
        space()
        guard index < bytes.count else { throw Self.bad() }
        switch bytes[index] {
        case 123:
            guard depth <= 8 else { throw Self.bad() }
            return .object(try object(depth))
        case 91:
            guard depth <= 8 else { throw Self.bad() }
            return .array(try array(depth))
        case 34: return .string(try string())
        case 116: try literal("true"); return .bool(true)
        case 102: try literal("false"); return .bool(false)
        case 110: try literal("null"); return .null
        case 45, 48...57: return .number(try number())
        default: throw Self.bad()
        }
    }

    private mutating func object(_ depth: Int) throws -> [String: JSONValue] {
        try take(123)
        space()
        var result: [String: JSONValue] = [:]
        var keys = Set<String>()
        if accept(125) { return result }
        while true {
            space()
            guard index < bytes.count, bytes[index] == 34 else { throw Self.bad() }
            let key = try string()
            guard keys.insert(key).inserted else { throw Self.bad() }
            space()
            try take(58)
            result[key] = try value(depth + 1)
            space()
            if accept(125) { return result }
            try take(44)
        }
    }

    private mutating func array(_ depth: Int) throws -> [JSONValue] {
        try take(91)
        space()
        var result: [JSONValue] = []
        if accept(93) { return result }
        while true {
            result.append(try value(depth + 1))
            space()
            if accept(93) { return result }
            try take(44)
        }
    }

    private mutating func string() throws -> String {
        try take(34)
        var result = ""
        var utf8 = [UInt8]()
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if byte == 34 {
                guard let suffix = String(bytes: utf8, encoding: .utf8) else { throw Self.bad() }
                return result + suffix
            }
            guard byte >= 32 else { throw Self.bad() }
            if byte != 92 {
                utf8.append(byte)
                continue
            }
            guard let prefix = String(bytes: utf8, encoding: .utf8), index < bytes.count else { throw Self.bad() }
            result += prefix
            utf8.removeAll(keepingCapacity: true)
            let escape = bytes[index]
            index += 1
            switch escape {
            case 34, 92, 47: result.unicodeScalars.append(UnicodeScalar(escape))
            case 98: result.unicodeScalars.append("\u{08}")
            case 102: result.unicodeScalars.append("\u{0c}")
            case 110: result.unicodeScalars.append("\n")
            case 114: result.unicodeScalars.append("\r")
            case 116: result.unicodeScalars.append("\t")
            case 117:
                let first = try hex4()
                if (0xD800...0xDBFF).contains(first) {
                    guard index + 1 < bytes.count, bytes[index] == 92, bytes[index + 1] == 117 else { throw Self.bad() }
                    index += 2
                    let second = try hex4()
                    guard (0xDC00...0xDFFF).contains(second), let scalar = UnicodeScalar(0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)) else { throw Self.bad() }
                    result.unicodeScalars.append(scalar)
                } else {
                    guard !(0xDC00...0xDFFF).contains(first), let scalar = UnicodeScalar(first) else { throw Self.bad() }
                    result.unicodeScalars.append(scalar)
                }
            default: throw Self.bad()
            }
        }
        throw Self.bad()
    }

    private mutating func hex4() throws -> UInt32 {
        guard index + 4 <= bytes.count else { throw Self.bad() }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let c = bytes[index]
            index += 1
            let digit: UInt32
            switch c {
            case 48...57: digit = UInt32(c - 48)
            case 65...70: digit = UInt32(c - 55)
            case 97...102: digit = UInt32(c - 87)
            default: throw Self.bad()
            }
            value = value * 16 + digit
        }
        return value
    }

    private mutating func number() throws -> String {
        let start = index
        _ = accept(45)
        guard index < bytes.count else { throw Self.bad() }
        if accept(48) {
        } else {
            guard digit19() else { throw Self.bad() }
            while digit() {}
        }
        if accept(46) {
            guard digit() else { throw Self.bad() }
            while digit() {}
        }
        if accept(69) || accept(101) {
            _ = accept(43) || accept(45)
            guard digit() else { throw Self.bad() }
            while digit() {}
        }
        return String(decoding: bytes[start..<index], as: UTF8.self)
    }

    private mutating func literal(_ text: String) throws {
        for byte in text.utf8 {
            guard index < bytes.count, bytes[index] == byte else { throw Self.bad() }
            index += 1
        }
    }
    private mutating func space() { while index < bytes.count, [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
    private mutating func accept(_ byte: UInt8) -> Bool { guard index < bytes.count, bytes[index] == byte else { return false }; index += 1; return true }
    private mutating func take(_ byte: UInt8) throws {
        guard accept(byte) else { throw Self.bad() }
    }
    private mutating func digit() -> Bool { guard index < bytes.count, bytes[index] >= 48, bytes[index] <= 57 else { return false }; index += 1; return true }
    private mutating func digit19() -> Bool { guard index < bytes.count, bytes[index] >= 49, bytes[index] <= 57 else { return false }; index += 1; return true }
    private static func bad() -> Error { EnrollmentError.message("Account response was rejected. Try again.") }
}

enum StrictEnvelope {
    static func object(_ data: Data, required: Set<String>, optional: Set<String>, maxBytes: Int = 64 * 1024) throws -> [String: JSONValue] {
        var parser = try StrictJSON(data, maxBytes: maxBytes)
        guard case let .object(value) = try parser.parse(), required.isSubset(of: Set(value.keys)), Set(value.keys).isSubset(of: required.union(optional)) else { throw EnrollmentError.message("Account response was rejected. Try again.") }
        return value
    }
}

enum VaultEnvelopeCodec {
    /// The Keychain envelope shares the same duplicate-safe parser and closed
    /// persisted-contract authority as the public state envelope.
    static func privateSession(_ data: Data) throws -> PrivateSession {
        try PrivateSessionCodec.decode(data)
    }

    static func encodePrivateSession(_ value: PrivateSession) throws -> Data {
        let data = try JSONEncoder().encode(value)
        _ = try privateSession(data)
        return data
    }

    static func token(_ data: Data) throws -> Token {
        let object = try StrictEnvelope.object(data, required: ["access_token", "token_type", "expires_in", "refresh_token"], optional: ["id_token", "scope"])
        guard case let .string(access)? = object["access_token"], case let .string(kind)? = object["token_type"], case let .number(expiry)? = object["expires_in"], case let .string(refresh)? = object["refresh_token"], access.validToken, refresh.validToken, kind.lowercased() == "bearer", let expiresIn = Int(expiry), (1...86400).contains(expiresIn) else { throw EnrollmentError.message("Account response was rejected. Try again.") }
        let scope: String?
        if let scopeValue = object["scope"] {
            guard case let .string(value) = scopeValue, Set(["openid", "email", "offline_access"]).isSubset(of: Set(value.split(whereSeparator: { $0.isWhitespace }).map(String.init))) else { throw EnrollmentError.message("Account response was rejected. Try again.") }
            scope = value
        } else { scope = nil }
        if let idToken = object["id_token"] {
            guard case let .string(value) = idToken, value.validToken else { throw EnrollmentError.message("Account response was rejected. Try again.") }
        }
        return Token(access_token: access, token_type: kind, expires_in: expiresIn, refresh_token: refresh, scope: scope)
    }

    static func userinfo(_ data: Data) throws -> Identity {
        let object = try StrictEnvelope.object(data, required: ["sub"], optional: ["email", "email_verified"])
        guard case let .string(subject)? = object["sub"], UUID(uuidString: subject)?.canonical == subject else { throw EnrollmentError.message("Account identity was rejected. Try again.") }
        let email: String?
        if let raw = object["email"] { guard case let .string(value) = raw, value.utf8.count <= 320 else { throw EnrollmentError.message("Account identity was rejected. Try again.") }; email = value } else { email = nil }
        let verified: Bool?
        if let raw = object["email_verified"] { guard case let .bool(value) = raw else { throw EnrollmentError.message("Account identity was rejected. Try again.") }; verified = value } else { verified = nil }
        return Identity(sub: subject, email: email, email_verified: verified)
    }

    static func publicState(_ data: Data) throws -> PublicState {
        guard data.count <= 2048 else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        let object: [String: JSONValue]
        do {
            object = try StrictEnvelope.object(data, required: ["version", "generation", "host_subject", "provider_subject"], optional: ["suggestions"])
        } catch { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        guard case let .number(versionText)? = object["version"], let version = Int(versionText), (1...2).contains(version), case let .string(generation)? = object["generation"], generation.canonicalUUID, let host = optionalCanonicalUUID(object["host_subject"]), let provider = optionalCanonicalUUID(object["provider_subject"]) else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        if version == 1 { guard object["suggestions"] == nil else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }; return PublicState(version: 1, generation: generation, host_subject: host, provider_subject: provider, suggestions: .empty()) }
        guard case let .object(raw)? = object["suggestions"], Set(raw.keys) == Set(["organization_id", "revision", "status", "refreshed_at_ms", "count", "unsupported_count"]), let organization = optionalCanonicalUUID(raw["organization_id"]), case let .string(revision)? = raw["revision"], revision.canonicalUUID, case let .string(status)? = raw["status"], Set(["empty", "ready", "stale", "failed"]).contains(status), let refreshed = optionalPositiveInt64(raw["refreshed_at_ms"]), case let .number(countText)? = raw["count"], let count = Int(countText), case let .number(unsupportedText)? = raw["unsupported_count"], let unsupported = Int(unsupportedText), (0...2000).contains(count), (0...2000).contains(unsupported) else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") }
        if status == "empty" { guard organization == nil, refreshed == nil, count == 0, unsupported == 0 else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") } }
        if status == "ready" { guard organization != nil, refreshed != nil else { throw EnrollmentError.message("Vault status is corrupt. Reconnect the provider.") } }
        return PublicState(version: 2, generation: generation, host_subject: host, provider_subject: provider, suggestions: NativeSuggestions(organization_id: organization, revision: revision, status: status, refreshed_at_ms: refreshed, count: count, unsupported_count: unsupported))
    }

    private static func optionalPositiveInt64(_ value: JSONValue?) -> Int64?? {
        guard let value else { return nil }
        if case .null = value { return .some(nil) }
        guard case let .number(text) = value, let number = Int64(text), number > 0 else { return nil }
        return .some(number)
    }

    private static func optionalCanonicalUUID(_ value: JSONValue?) -> String?? {
        guard let value else { return nil }
        switch value {
        case .null: return .some(nil)
        case let .string(subject) where subject.canonicalUUID: return .some(subject)
        default: return nil
        }
    }
}

enum PrivateSessionCodec {
    static func decode(_ data: Data) throws -> PrivateSession {
        guard data.count <= 48 * 1024 else { throw corrupt() }
        let object: [String: JSONValue]
        do {
            object = try StrictEnvelope.object(data, required: ["version", "phase", "subject", "generation", "expires_at_ms"], optional: ["access_token", "refresh_token"])
        } catch {
            throw corrupt()
        }
        guard case .number("1")? = object["version"], case let .string(phase)? = object["phase"], case let .string(subject)? = object["subject"], case let .string(generation)? = object["generation"], case let .number(expiry)? = object["expires_at_ms"], subject.canonicalUUID, generation.canonicalUUID, let expiryValue = Int64(expiry), expiryValue > 0 else { throw corrupt() }
        switch phase {
        case "active": guard case let .string(access)? = object["access_token"], case let .string(refresh)? = object["refresh_token"], access.validToken, refresh.validToken else { throw corrupt() }; return PrivateSession(version: 1, phase: phase, subject: subject, generation: generation, access_token: access, refresh_token: refresh, expires_at_ms: expiryValue)
        case "refresh_pending": guard object["access_token"] == nil, object["refresh_token"] == nil else { throw corrupt() }; return PrivateSession(version: 1, phase: phase, subject: subject, generation: generation, access_token: "", refresh_token: "", expires_at_ms: expiryValue)
        default: throw corrupt()
        }
    }
    private static func corrupt() -> Error { EnrollmentError.message("Vault session is corrupt. Reconnect the provider.") }
}

extension String {
    var validToken: Bool { !isEmpty && utf8.count <= 16 * 1024 && utf8.allSatisfy { $0 >= 0x21 && $0 <= 0x7e } }
    var canonicalUUID: Bool { UUID(uuidString: self)?.uuidString.lowercased() == self }
}

extension UUID { var canonical: String { uuidString.lowercased() } }

struct NativeOrganization {
    let id: String
    let name: String
    let isPersonal: Bool
}

/// Shared strict organization report decoder; never selects a saved preference.
enum NativeOrganizationCodec {
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

}
