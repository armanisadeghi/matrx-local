import Foundation

@main
struct NativeVaultPasswordCorpus {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        guard condition() else { fatalError(message) }
    }
    static func rejects(_ body: () throws -> Void, _ message: String) {
        do { try body(); fatalError(message) } catch { }
    }
    static func main() {
        let subject = "11111111-1111-4111-8111-111111111111"
        let org = "22222222-2222-4222-8222-222222222222"
        let item = "33333333-3333-4333-8333-333333333333"
        let report = "{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":\"P\"}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}".data(using: .utf8)!
        let decoded = try! NativePasswordCodec.organizations(report, subject: subject)
        require(decoded.organizations.count == 1 && decoded.selected == org, "valid organization report must decode")
        let match = "{\"matches\":[{\"item_id\":\"\(item)\",\"display_name\":\"Example\",\"request_identifier_index\":0}],\"truncated\":false,\"reason\":null}".data(using: .utf8)!
        require((try! NativePasswordCodec.matches(match)).matches.first?.itemID == item, "value-free match must decode")
        rejects({ _ = try NativePasswordCodec.matches("{\"matches\":[],\"matches\":[],\"truncated\":false,\"reason\":null}".data(using: .utf8)!) }, "duplicate response keys must fail")
        rejects({ _ = try NativePasswordCodec.materialized("{\"username\":\"u\",\"password\":\"\",\"extra\":true}".data(using: .utf8)!) }, "empty password and unknown keys must fail")
        print("Native Vault password codec corpus passed")
    }
}
