import Foundation

@_silgen_name("matrx_vault_exchange_dispatch")
func dispatchExchange(_ input: UnsafePointer<UInt8>?, _ length: Int, _ window: UnsafeMutableRawPointer?, _ output: UnsafeMutablePointer<UInt8>?, _ capacity: Int) -> Int

@main struct NativeVaultExchangeHostCorpus {
    static func invoke(_ value: String) -> String? {
        let bytes = Array(value.utf8)
        return bytes.withUnsafeBufferPointer { buffer in
            var output = [UInt8](repeating: 0, count: 2048)
            let length = output.withUnsafeMutableBufferPointer { target in
                dispatchExchange(buffer.baseAddress, buffer.count, nil, target.baseAddress, target.count)
            }
            guard length >= 0 else { return nil }
            precondition(length <= output.count)
            return String(bytes: output.prefix(length), encoding: .utf8)
        }
    }

    static func main() {
        precondition(Thread.isMainThread)
        precondition(dispatchExchange(nil, 1, nil, nil, 0) == -1)
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
