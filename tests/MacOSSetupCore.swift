// Headless native core checks. These do NOT establish graphical acceptance.
import Foundation

@main struct NativeCoreProbe {
    @MainActor static func main() async throws {
        let model = SetupModel()
        guard let index = CommandLine.arguments.firstIndex(of: "--archive-cache"), CommandLine.arguments.count > index + 1 else {
            throw SetupFailure("Supply a disposable archive cache containing the verified uv.tar.gz/python.tar.gz.")
        }
        let archives = URL(fileURLWithPath: CommandLine.arguments[index + 1])
        try model.acquireLock()
        defer { model.releaseLock() }
        let source = try await model.prepareSource()
        let again = try await model.prepareSource()
        precondition(source == again)
        var executables: [URL] = []
        for (name, artifact) in [("uv", model.pins.uv), ("python", model.pins.python)] {
            let destination = model.root.appendingPathComponent("setup/bootstrap/\(artifact.sha256)")
            try privateDirectory(destination)
            let archive = destination.appendingPathComponent("archive.tar.gz")
            try FileManager.default.copyItem(at: archives.appendingPathComponent(name + ".tar.gz"), to: archive)
            let executable = try await model.bootstrap(artifact, name: name)
            executables.append(executable)
            let before = try FileManager.default.attributesOfItem(atPath: executable.path)[.modificationDate] as! Date
            let reused = try await model.bootstrap(artifact, name: name)
            precondition(executable == reused)
            let after = try FileManager.default.attributesOfItem(atPath: executable.path)[.modificationDate] as! Date
            precondition(before == after)
        }
        let uv = try await model.execute(executables[0], ["--version"])
        precondition(uv.contains("uv 0.12.3 "))
        let python = try await model.execute(executables[1], ["-I", "-B", "-c", "import platform; print(platform.python_version(), platform.machine())"])
        precondition(python.trimmingCharacters(in: .whitespacesAndNewlines) == "3.12.13 arm64")
        // The archive still matches, but a changed installed binary must refuse reuse.
        let saved = try Data(contentsOf: executables[0])
        try (saved + Data([0])).write(to: executables[0])
        do {
            _ = try await model.bootstrap(model.pins.uv, name: "uv")
            throw SetupFailure("Changed native bootstrap binary was accepted.")
        } catch let error as SetupFailure {
            precondition(error.message.contains("files changed"))
        }
        try saved.write(to: executables[0])
        let extracted = executables[0].deletingLastPathComponent().deletingLastPathComponent()
        let escaping = extracted.appendingPathComponent("escaping-link")
        try FileManager.default.createSymbolicLink(at: escaping, withDestinationURL: source)
        do {
            _ = try model.bootstrapInventory(extracted)
            throw SetupFailure("Escaping bootstrap symlink was accepted.")
        } catch let error as SetupFailure { precondition(error.message.contains("link escapes")) }
        try FileManager.default.removeItem(at: escaping)
        let manifest = try JSONDecoder().decode([String: SourceEntry].self,
            from: Data(contentsOf: model.resources.appendingPathComponent("source-manifest.json")))
        let unexpected = source.appendingPathComponent("unexpected.txt")
        try Data("unexpected".utf8).write(to: unexpected)
        do {
            try model.verifySource(source, manifest)
            throw SetupFailure("Unexpected native source file was accepted.")
        } catch let error as SetupFailure { precondition(error.message.contains("Unexpected source file")) }
        try FileManager.default.removeItem(at: unexpected)
        let cancelled = Task { @MainActor in
            try await Task.sleep(nanoseconds: 100_000_000)
            return try await model.bootstrap(model.pins.uv, name: "uv")
        }
        cancelled.cancel()
        do { _ = try await cancelled.value; throw SetupFailure("Cancellation was ignored.") }
        catch is CancellationError {}
        print("PASS: native source preparation/reuse, pinned archive extraction, Python/uv execution, file tamper refusal, escaping-link refusal, unexpected-source refusal, cancellation.")
        print("Stable source: \(source.path)")
    }
}
