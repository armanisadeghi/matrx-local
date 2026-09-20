import Foundation
import Darwin

@main struct NativeVaultImportFileCorpus {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let fixture = root.appendingPathComponent("fixture.json")
        let content = Data("{\"synthetic\":true}".utf8)
        try content.write(to: fixture)
        let actual = try NativeVaultImportFile.read(fixture)
        precondition(actual == content)
        let link = root.appendingPathComponent("link.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: fixture)
        let empty = root.appendingPathComponent("empty.json")
        try Data().write(to: empty)
        let large = root.appendingPathComponent("large.json")
        let fd = open(large.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        precondition(fd >= 0)
        precondition(ftruncate(fd, off_t(NativeVaultImportFile.maximumBytes + 1)) == 0)
        close(fd)
        for refused in [link, empty, large, root, URL(string: "https://example.com/file.json")!] {
            do { _ = try NativeVaultImportFile.read(refused); preconditionFailure("unsafe file admitted") }
            catch { }
        }
        var changedRefused = false
        do {
            _ = try NativeVaultImportFile.read(fixture, afterRead: {
                let handle = try FileHandle(forWritingTo: fixture)
                try handle.truncate(atOffset: 1)
                try handle.close()
            })
        } catch NativeVaultImportFile.Failure.changed { changedRefused = true }
        precondition(changedRefused, "concurrently changed file admitted")
        print("PASS native import reader exact bytes, symlink, empty, oversized, directory and non-file refusal")
    }
}
