import Foundation

// This must never typecheck: public/journal failure reasons are a closed enum.
let arbitraryReason: NativeVaultImportTransportFailure = .refused(reason: "private-source-secret")
