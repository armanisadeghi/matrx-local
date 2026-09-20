import Foundation
import CryptoKit

@main struct NativeVaultImportParserCorpus {
    static func main() throws {
        let key = try P256.Signing.PrivateKey(rawRepresentation: Data(repeating: 7, count: 32))
        func b64(_ value: Data) -> String { value.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
        let passkey: [String: Any] = ["type": "passkey", "credentialId": b64(Data(repeating: 1, count: 16)), "rpId": "example.com", "username": "user", "userDisplayName": "User", "userHandle": b64(Data("user".utf8)), "key": b64(key.derRepresentation)]
        let fixture: [String: Any] = ["version": ["major": 1, "minor": 0], "exporterRpId": "example.com", "exporterDisplayName": "Example", "timestamp": 0, "accounts": [["id": "YQ", "username": "", "email": "", "collections": [], "items": [["id": "aQ", "title": "Imported passkey", "credentials": [passkey]]]]]]
        let bytes = try JSONSerialization.data(withJSONObject: fixture)
        let parser = NativeVaultImportParser()
        let inventory = try parser.parseCXFv1(bytes)
        precondition(inventory.total == 1 && inventory.candidates.count == 1 && inventory.unsupported.isEmpty)
        precondition(inventory.candidates[0].title == "Imported passkey")
        let restored = try nativeExportSourcePkcs8(source: inventory.candidates[0].canonicalSource, maxSourceBytes: 65_536)
        let restoredKey = try P256.Signing.PrivateKey(derRepresentation: restored.pkcs8Der)
        precondition(restoredKey.publicKey.rawRepresentation == key.publicKey.rawRepresentation)
        do { _ = try parser.parseCXFv1(bytes); preconditionFailure("parser reused") } catch {}
        let cancelled = NativeVaultImportParser(); cancelled.cancel()
        do { _ = try cancelled.parseCXFv1(bytes); preconditionFailure("cancelled parse accepted") } catch {}
        let duplicate = Data("{\"version\":{},\"version\":{}}".utf8)
        do { _ = try NativeVaultImportParser().parseCXFv1(duplicate); preconditionFailure("duplicate accepted") } catch {}
        print("Native Swift/Rust file import adapter preserves original public key; refusal corpus passed")
    }
}
