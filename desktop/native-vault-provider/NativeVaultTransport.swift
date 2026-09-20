import Foundation

// Native OAuth shared by the provider and containing native exchange process.
// Tokens never enter webview, Python, or value-free host command responses.
let clientID = "d8a02629-f5f2-4064-ab26-31da07f082fc"
let callback = URL(string: "matrx-vault-provider://oauth/callback")!
let authorizeURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/authorize")!
let tokenURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/token")!
let userinfoURL = URL(string: "https://db.matrxserver.com/auth/v1/oauth/userinfo")!
/// URLSession's convenience completion handler has already accumulated the
/// response. This delegate refuses redirects and cancels as soon as the fixed
/// envelope limit is crossed.
final class BoundedTransport: NSObject, URLSessionDataDelegate {
    private var data = Data()
    private let limit: Int
    private let completion: (Result<(Data, HTTPURLResponse), Error>) -> Void
    private let lifecycle = NSLock()
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var completed = false
    private var response: HTTPURLResponse?
    init(limit: Int = 64 * 1024, _ completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        self.limit = limit; self.completion = completion
    }
    func start(_ request: URLRequest) {
        lifecycle.lock()
        guard !completed, session == nil else { lifecycle.unlock(); return }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10; config.timeoutIntervalForResource = 10
        config.urlCache = nil; config.requestCachePolicy = .reloadIgnoringLocalCacheData
        let session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        let task = session.dataTask(with: request)
        self.session = session; self.task = task; lifecycle.unlock()
        task.resume()
    }
    private func finish(_ result: Result<(Data, HTTPURLResponse), Error>) {
        lifecycle.lock()
        guard !completed else { lifecycle.unlock(); return }
        completed = true; let session = self.session
        self.session = nil; task = nil; lifecycle.unlock()
        session?.invalidateAndCancel()
        completion(result)
    }
    func cancel() { finish(.failure(EnrollmentError.message("Vault request was cancelled."))) }
    func urlSession(_: URLSession, task _: URLSessionTask, willPerformHTTPRedirection _: HTTPURLResponse, newRequest _: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) {
        guard data.count <= limit - chunk.count else { dataTask.cancel(); return }
        data.append(chunk)
    }
    func urlSession(_: URLSession, task _: URLSessionTask, didCompleteWithError error: Error?) {
        guard error == nil, let response else { finish(.failure(EnrollmentError.message("Vault connection is temporarily unavailable. Try again."))); return }
        finish(.success((data, response)))
    }
    func urlSession(_: URLSession, dataTask _: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        self.response = response as? HTTPURLResponse
        completionHandler(response.expectedContentLength > Int64(limit) ? .cancel : .allow)
    }
}

func formBody(_ parameters: [(String, String)]) -> Data {
    func encode(_ value: String) -> String {
        value.utf8.map { byte in
            switch byte {
            case 65...90, 97...122, 48...57, 45, 46, 95, 126:
                return String(UnicodeScalar(byte))
            default:
                return String(format: "%%%02X", byte)
            }
        }.joined()
    }
    return parameters.map { "\(encode($0.0))=\(encode($0.1))" }.joined(separator: "&").data(using: .utf8)!
}
func connectionResponseError(_ status: Int) -> Error {
    switch status {
    case 401, 403: return EnrollmentError.message("Vault connection needs reconnect.")
    case 429: return EnrollmentError.message("Vault connection is busy. Try again later.")
    case 500...599: return EnrollmentError.message("Vault connection is temporarily unavailable. Try again.")
    default: return EnrollmentError.message("Vault connection was rejected. Reconnect the provider.")
    }
}

private let nativePasskeyResponseLimit = 96 * 1024

protocol NativeVaultPasskeyTransporting: AnyObject {
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void)
    func cancel()
}

final class NativeVaultPasskeyTransport: NativeVaultPasskeyTransporting {
    private let lock = NSLock()
    private var active: (id: UUID, transport: BoundedTransport)?
    private let makeTransport: (@escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) -> BoundedTransport
    init(makeTransport: @escaping (@escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) -> BoundedTransport = { BoundedTransport(limit: nativePasskeyResponseLimit, $0) }) {
        self.makeTransport = makeTransport
    }
    func send(_ request: URLRequest, completion: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        let id = UUID()
        let transport = makeTransport { [weak self] result in
            guard let self else { completion(result); return }
            self.lock.lock()
            if self.active?.id == id { self.active = nil }
            self.lock.unlock()
            completion(result)
        }
        lock.lock(); let previous = active; active = (id, transport); lock.unlock()
        previous?.transport.cancel()
        transport.start(request)
    }
    func cancel() {
        lock.lock(); let pending = active; active = nil; lock.unlock()
        pending?.transport.cancel()
    }
}


let nativeAPIOrigin = URL(string: "https://server.app.matrxserver.com")!
