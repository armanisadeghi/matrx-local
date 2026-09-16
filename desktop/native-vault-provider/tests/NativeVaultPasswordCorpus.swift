import Foundation
import AuthenticationServices

@main
struct NativeVaultPasswordCorpus {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) {
        guard condition() else { fatalError(message) }
    }
    static func rejects(_ body: () throws -> Void, _ message: String) {
        do { try body(); fatalError(message) } catch { }
    }
    @MainActor static func main() {
        let subject = "11111111-1111-4111-8111-111111111111"
        let org = "22222222-2222-4222-8222-222222222222"
        let item = "33333333-3333-4333-8333-333333333333"
        let report = "{\"authenticated\":true,\"user_id\":\"\(subject)\",\"organizations\":[{\"id\":\"\(org)\",\"name\":\"Personal\",\"is_personal\":true,\"abbreviation\":\"P\"}],\"default_organization_id\":\"\(org)\",\"default_preference_status\":\"valid\",\"warnings\":[],\"missing_organization_count\":0}".data(using: .utf8)!
        let decoded = try! NativePasswordCodec.organizations(report, subject: subject)
        require(decoded.organizations.count == 1 && decoded.selected == org, "valid organization report must decode")
        let match = "{\"matches\":[{\"item_id\":\"\(item)\",\"display_name\":\"Example\",\"request_identifier_index\":0}],\"truncated\":false,\"reason\":null}".data(using: .utf8)!
        require((try! NativePasswordCodec.matches(match)).matches.first?.itemID == item, "value-free match must decode")
        rejects({ _ = try NativePasswordCodec.matches("{\"matches\":[],\"matches\":[],\"truncated\":false,\"reason\":null}".data(using: .utf8)!) }, "duplicate response keys must fail")
        rejects({ _ = try NativePasswordCodec.materialized("{\"username\":\"u\",\"password\":\"\"}".data(using: .utf8)!) }, "empty password must fail")
        rejects({ _ = try NativePasswordCodec.materialized("{\"username\":\"u\",\"password\":\"p\",\"extra\":true}".data(using: .utf8)!) }, "unknown materialize key must fail")
        let coordinator = NativePasswordOperationCoordinator()
        let first = coordinator.begin([("url", "https://one.example")])
        require(coordinator.current(first), "new operation must be current")
        let replacement = coordinator.begin([("url", "https://two.example")])
        require(!coordinator.current(first) && coordinator.current(replacement), "replacement must terminalize only the old operation")
        coordinator.clearCompleted(first)
        require(coordinator.current(replacement), "stale completion must not clear replacement")
        require(coordinator.prepareCompletion(replacement), "current operation completes once")
        require(!coordinator.prepareCompletion(replacement), "completion must be exactly once")
        let actual = try! NativePasswordStage.identifiers([ASCredentialServiceIdentifier(identifier: "one.example", type: .domain), ASCredentialServiceIdentifier(identifier: "https://two.example/path", type: .URL)])
        require(actual.count == 2 && actual[0].1 == "one.example" && actual[1].1 == "https://two.example/path", "actual Apple identifier order must be preserved")
        let oversized = ASCredentialServiceIdentifier(identifier: String(repeating: "a", count: 2049), type: .domain)
        rejects({ _ = try NativePasswordStage.identifiers([oversized]) }, "oversized Apple identifier must reject the whole request")
        let grant = NativeVaultSessionAccess.Grant(accessToken: "token", subject: subject, generation: "generation-a")
        require(NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-a", subject: subject), grant), "current state must admit matching grant")
        require(!NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-b", subject: subject), grant), "generation change must reject stale grant")
        require(!NativePasswordStage.grantIsCurrent(NativePasswordCurrentState(generation: "generation-a", subject: org), grant), "subject change must reject stale grant")
        require(NativePasswordStage.interactionRequiredCode == ASExtensionError.userInteractionRequired.rawValue, "modern no-interaction policy must require interaction")
        print("Native Vault password codec corpus passed")
    }
}
