import Foundation
import Darwin

/// Reads only a native chooser's file URL. Paths and file bytes have no public
/// host-command representation. The owned allocation is wiped on release; this
/// does not claim that Foundation or later ABI copies are all zeroizable.
enum NativeVaultImportFile {
    static let maximumBytes = 16 * 1024 * 1024
    enum Failure: Error { case unavailable, changed, limit }

    static func read(_ url: URL, afterRead: (() throws -> Void)? = nil) throws -> Data {
        guard url.isFileURL else { throw Failure.unavailable }
        let descriptor = url.withUnsafeFileSystemRepresentation { path in
            path.map { Darwin.open($0, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK) } ?? -1
        }
        guard descriptor >= 0 else { throw Failure.unavailable }
        defer { Darwin.close(descriptor) }
        var before = stat()
        guard fstat(descriptor, &before) == 0,
              before.st_mode & S_IFMT == S_IFREG,
              before.st_size > 0 else { throw Failure.unavailable }
        guard before.st_size <= maximumBytes else { throw Failure.limit }
        let length = Int(before.st_size)
        let capacity = length + 1
        guard let allocation = malloc(capacity) else { throw Failure.unavailable }
        var transferred = false
        defer { if !transferred { _ = memset_s(allocation, capacity, 0, capacity); free(allocation) } }
        var count = 0
        while count < capacity {
            let received = Darwin.read(descriptor, allocation.advanced(by: count), capacity - count)
            if received < 0, errno == EINTR { continue }
            guard received >= 0 else { throw Failure.unavailable }
            if received == 0 { break }
            count += received
        }
        // External-file mutation seam lets the corpus force a concurrent change
        // after reading, before the actual descriptor snapshot is verified.
        try afterRead?()
        var after = stat()
        guard count == length, fstat(descriptor, &after) == 0,
              before.st_dev == after.st_dev, before.st_ino == after.st_ino,
              before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec,
              before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec else { throw Failure.changed }
        transferred = true
        return Data(bytesNoCopy: allocation, count: length, deallocator: .custom { pointer, _ in
            _ = memset_s(pointer, capacity, 0, capacity)
            free(pointer)
        })
    }
}
