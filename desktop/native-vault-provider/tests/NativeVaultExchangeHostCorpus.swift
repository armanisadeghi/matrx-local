import Foundation

@_silgen_name("matrx_vault_exchange_dispatch")
func dispatchExchange(_ input: UnsafePointer<UInt8>?, _ length: Int, _ window: UnsafeMutableRawPointer?) -> UnsafeMutablePointer<CChar>?
@_silgen_name("matrx_vault_exchange_free")
func freeExchange(_ response: UnsafeMutablePointer<CChar>?)

@main struct NativeVaultExchangeHostCorpus {
    static func invoke(_ value: String) -> String? {
        let bytes = Array(value.utf8)
        return bytes.withUnsafeBufferPointer { buffer in
            guard let result = dispatchExchange(buffer.baseAddress, buffer.count, nil) else { return nil }
            defer { freeExchange(result) }
            return String(cString: result)
        }
    }
    static func main() {
        precondition(Thread.isMainThread)
        precondition(dispatchExchange(nil, 1, nil) == nil)
        for input in ["{}", "[]", "null", "{\"action\":\"invalidate\",\"action\":\"invalidate\"}",
                      "{\"action\":\"invalidate\",\"token\":\"synthetic\"}",
                      "{\"action\":\"read_session\"}",
                      "{\"action\":\"status\",\"operation_id\":\"bad\"}",
                      String(repeating: " ", count: 96 * 1024 + 1)] {
            precondition(invoke(input) == nil, "invalid public command accepted")
        }
        if #available(macOS 26.0, *) {
            precondition(invoke("{\"action\":\"invalidate\"}") == "{}")
        }
        let completed = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            precondition(invoke("{\"action\":\"invalidate\"}") == nil, "off-main command accepted")
            completed.signal()
        }
        completed.wait()
        print("PASS native exchange C ABI rejects malformed, secret-bearing and off-main commands")
    }
}
