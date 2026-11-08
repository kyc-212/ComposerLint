import SwiftUI
import AppKit
import Foundation
import CryptoKit
import Darwin

struct SetupFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}
struct Artifact: Decodable {
    let version: String
    let url: String
    let sha256: String
    let archive_root: String
    let executable: String
}
struct BootstrapPins: Decodable {
    let schema: Int
    let allowed_hosts: [String]
    let uv: Artifact
    let python: Artifact
}
struct SourceEntry: Codable {
    let sha256: String?
    let link: String?
}

func hashFile(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hash = SHA256()
    while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty { hash.update(data: data) }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
}
func safeDestination(_ url: URL) throws {
    guard url.path.hasPrefix("/"), !url.pathComponents.contains("..") else { throw SetupFailure("Choose an absolute installation folder.") }
    var current = url
    while current.path != "/" {
        if (try? current.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
            throw SetupFailure("Symbolic-link installation folders are refused: \(current.path)")
        }
        current.deleteLastPathComponent()
    }
}
func privateDirectory(_ url: URL) throws {
    try safeDestination(url)
    let fm = FileManager.default
    try fm.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let attributes = try fm.attributesOfItem(atPath: url.path)
    guard (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0 else {
        throw SetupFailure("Installation folder must be private and owned by you: \(url.path)")
    }
}

// HTTPS redirects are limited to the reviewed release service. TLS verification
// stays enabled; artifact identity is checked against the committed pins as well.
final class DownloadPolicy: NSObject, URLSessionTaskDelegate, URLSessionDownloadDelegate {
    let hosts: Set<String>
    let progress: (Int64, Int64) -> Void
    init(hosts: [String], progress: @escaping (Int64, Int64) -> Void) { self.hosts = Set(hosts); self.progress = progress }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(request.url?.scheme == "https" && hosts.contains(request.url?.host ?? "") ? request : nil)
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > 200 * 1024 * 1024 { downloadTask.cancel() }
        progress(totalBytesWritten, totalBytesExpectedToWrite)
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {}
}

@MainActor final class SetupModel: ObservableObject {
    @Published var mode = "full"
    @Published var phase = "welcome"
    @Published var message = "Install ComposerLint for this user"
    @Published var details = ""
    @Published var log = ""
    @Published var busy = false
    @Published var hasTools = false
    @Published var completion = ""
    @Published var stage = 0
    let root: URL
    let resources: URL
    let pins: BootstrapPins
    var job: Task<Void, Never>?
    var process: Process?
    var input: FileHandle?
    var source: URL?
    var planID = ""
    var prefix: URL?
    var outputBuffer = ""
    var logOffset: UInt64 = 0
    var lockFD: Int32 = -1
    var cancelled = false
    var backendStatus = ""

    init() {
        umask(0o077)
        resources = Bundle.main.resourceURL!
        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "--data-root"), args.count > index + 1 {
            root = URL(fileURLWithPath: args[index + 1], isDirectory: true)
        } else {
            root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/ComposerLint", isDirectory: true)
        }
        // A build missing its reviewed pins cannot run setup.
        do {
            pins = try JSONDecoder().decode(BootstrapPins.self, from: Data(contentsOf: resources.appendingPathComponent("bootstrap-pins.json")))
        } catch { fatalError("Invalid application resources; rebuild from reviewed source: \(error)") }
        check()
    }
    func append(_ text: String) {
        log += text
        if log.count > 60000 { log = String(log.suffix(60000)) }
    }
    func check() {
        if phase == "welcome" { stage = 0 }
        var system = utsname(); uname(&system)
        let machine = withUnsafePointer(to: &system.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 256) { String(cString: $0) }
        }
        guard machine == "arm64", getuid() != 0 else {
            phase = "unsupported"; message = "Use a native Apple Silicon Mac and a normal user account."; return
        }
        let p = Process(); let pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select"); p.arguments = ["-p"]
        p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        do { try p.run(); p.waitUntilExit(); hasTools = p.terminationStatus == 0 } catch { hasTools = false }
        message = hasTools ? "Apple developer tools detected" : "Full setup needs Apple Command Line Tools"
        details = "Python \(pins.python.version) and uv \(pins.uv.version) will be verified in a private folder. Existing system tools, scores, exports and client settings stay unchanged.\n\nInstallation location:\n\(root.path)\n\nOnly macOS Apple Silicon is supported. This app is not Developer ID signed or notarized. macOS may block its first launch; see the release instructions for Apple's per-app Open Anyway procedure."
    }
    func requestAppleTools() {
        // User explicitly operates Apple's licence/security dialogs; never elevated here.
        do {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select"); p.arguments = ["--install"]
            p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
            try p.run()
            message = "Complete Apple's installation and licence dialogs, then click Recheck."
        } catch { fail(error) }
    }
    func review() {
        guard mode == "text" || hasTools else { message = "Install Apple tools first, or select text analysis only."; return }
        phase = "bootstrapReview"
        message = "Review private tools and source preparation"
        stage = 1
        details = "Approve private prerequisite setup and source preparation:\n\nPython \(pins.python.version)\n\(pins.python.url)\nSHA-256: \(pins.python.sha256)\n\nuv \(pins.uv.version)\n\(pins.uv.url)\nSHA-256: \(pins.uv.sha256)\n\nOnly missing archives are downloaded from the official Astral GitHub releases and verified before extraction. Verified private copies are reused. No Homebrew, system Python changes, shell-profile changes or administrator helper.\n\nComposerLint source and Skills will be copied from this app into a stable private folder under:\n\(root.path)\n\nYou will review and approve the separate dependency/renderer plan before installation."
    }
    func acquireLock() throws {
        try privateDirectory(root)
        let setup = root.appendingPathComponent("setup", isDirectory: true)
        try privateDirectory(setup)
        let lock = setup.appendingPathComponent("graphical.lock")
        try safeDestination(lock)
        lockFD = Darwin.open(lock.path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
            if lockFD >= 0 { close(lockFD); lockFD = -1 }
            throw SetupFailure("Another graphical setup is running. Wait for it to finish.")
        }
    }
    func releaseLock() { if lockFD >= 0 { close(lockFD); lockFD = -1 } }
    func start() {
        cancelled = false; busy = true; phase = "running"; log = ""; completion = ""; backendStatus = ""
        stage = 1
        job = Task { [self] in
            do {
                try acquireLock()
                source = try await prepareSource()
                let uv = try await bootstrap(pins.uv, name: "uv")
                let python = try await bootstrap(pins.python, name: "Python")
                try Task.checkCancellation()
                let uvVersion = try await execute(uv, ["--version"])
                guard uvVersion.contains("uv \(pins.uv.version) ") else { throw SetupFailure("Private uv identity changed.") }
                let pythonVersion = try await execute(python, ["-I", "-B", "-c", "import platform; print(platform.python_version(), platform.machine())"])
                guard pythonVersion.trimmingCharacters(in: .whitespacesAndNewlines) == "\(pins.python.version) arm64" else {
                    throw SetupFailure("Private Python version/architecture does not match the reviewed pin.")
                }
                let installations = root.appendingPathComponent("setup/installations", isDirectory: true)
                try privateDirectory(installations)
                var environment = sanitizedEnvironment()
                environment["PATH"] = uv.deletingLastPathComponent().path + ":/usr/bin:/bin:/usr/sbin:/sbin"
                message = "Checking the installation plan…"; outputBuffer = ""; prefix = nil; logOffset = 0
                let polling = Task { [weak self] in
                    while !Task.isCancelled {
                        try? await Task.sleep(nanoseconds: 500_000_000)
                        if Task.isCancelled { break }
                        self?.readBackendLog()
                    }
                }
                defer { polling.cancel(); readBackendLog() }
                let status = try await backend(python, ["-I", "-B", source!.appendingPathComponent("tools/setup_composerlint.py").path,
                    "--json", "--mode", mode, "--installation-base", installations.path], environment)
                try Task.checkCancellation()
                guard status == 0 && backendStatus == "complete" else {
                    throw SetupFailure("\(backendStatus.isEmpty ? "Installation stopped" : backendStatus). Inspect the details below and retry. The previous working installation is preserved.")
                }
                phase = "complete"; message = "ComposerLint installation verified"
                stage = 4
            } catch {
                if cancelled || error is CancellationError {
                    phase = "cancelled"; message = "Setup cancelled. Retained verified work can be reused on retry."
                } else { fail(error) }
            }
            input = nil; process = nil; busy = false; releaseLock()
        }
    }
    func sanitizedEnvironment() -> [String: String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["HOME": home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": NSTemporaryDirectory(), "PYTHONDONTWRITEBYTECODE": "1"]
    }
    func verifySource(_ directory: URL, _ entries: [String: SourceEntry]) throws {
        for (name, entry) in entries {
            guard !name.hasPrefix("/"), !name.split(separator: "/").contains("..") else { throw SetupFailure("Unsafe bundled source path.") }
            let target = directory.appendingPathComponent(name)
            if let link = entry.link {
                guard try FileManager.default.destinationOfSymbolicLink(atPath: target.path) == link else { throw SetupFailure("Source discovery link changed: \(name)") }
            } else {
                guard (try target.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true,
                      try hashFile(target) == entry.sha256 else { throw SetupFailure("Source integrity check failed: \(name)") }
            }
        }
        let enumerator = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])!
        for case let file as URL in enumerator {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            if values.isRegularFile == true || values.isSymbolicLink == true {
                let relative = String(file.path.dropFirst(directory.path.count + 1))
                guard entries[relative] != nil else { throw SetupFailure("Unexpected source file: \(relative)") }
            }
        }
    }
    func prepareSource() async throws -> URL {
        try Task.checkCancellation(); message = "Verifying ComposerLint source…"
        let manifest = resources.appendingPathComponent("source-manifest.json")
        let entries = try JSONDecoder().decode([String: SourceEntry].self, from: Data(contentsOf: manifest))
        let id = String(try hashFile(manifest).prefix(16))
        let target = root.appendingPathComponent("setup/sources/\(id)", isDirectory: true)
        let bundled = resources.appendingPathComponent("Source", isDirectory: true)
        try verifySource(bundled, entries)
        try privateDirectory(target.deletingLastPathComponent())
        if !FileManager.default.fileExists(atPath: target.path) {
            try FileManager.default.copyItem(at: bundled, to: target)
        }
        try safeDestination(target); try verifySource(target, entries)
        return target
    }
    func bootstrap(_ artifact: Artifact, name: String) async throws -> URL {
        try Task.checkCancellation()
        let base = root.appendingPathComponent("setup/bootstrap/\(artifact.sha256)", isDirectory: true)
        try privateDirectory(base)
        let archive = base.appendingPathComponent("archive.tar.gz")
        if !FileManager.default.fileExists(atPath: archive.path) {
            message = "Downloading \(name) \(artifact.version)…"
            guard let url = URL(string: artifact.url), url.scheme == "https", pins.allowed_hosts.contains(url.host ?? "") else { throw SetupFailure("Unreviewed bootstrap URL.") }
            let delegate = DownloadPolicy(hosts: pins.allowed_hosts) { [weak self] bytes, expected in
                Task { @MainActor in
                    self?.message = "Downloading \(name): \(bytes / 1024 / 1024) MB" + (expected > 0 ? " of \(expected / 1024 / 1024) MB" : "")
                }
            }
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForResource = 900
            let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
            defer { session.invalidateAndCancel() }
            let (temporary, response) = try await session.download(from: url)
            defer { try? FileManager.default.removeItem(at: temporary) }
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  pins.allowed_hosts.contains(response.url?.host ?? ""),
                  try hashFile(temporary) == artifact.sha256 else { throw SetupFailure("\(name) download/hash check failed. No executable was installed.") }
            try safeDestination(archive)
            try FileManager.default.moveItem(at: temporary, to: archive)
            append("Verified download: \(artifact.url)\nStored: \(archive.path)\n")
        }
        guard try hashFile(archive) == artifact.sha256 else { throw SetupFailure("Retained \(name) archive changed; refusing automatic re-pinning.") }
        let extracted = base.appendingPathComponent("files", isDirectory: true)
        let receipt = base.appendingPathComponent("files.json")
        if FileManager.default.fileExists(atPath: receipt.path) {
            let records = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: receipt))
            guard records.count > 0 else { throw SetupFailure("Empty bootstrap integrity receipt.") }
            guard try bootstrapInventory(extracted) == records else { throw SetupFailure("Private \(name) files changed. Use a fresh setup location; existing evidence is preserved.") }
            append("Reusing verified private \(name) \(artifact.version).\n")
        } else {
            message = "Extracting verified \(name)…"
            // BSD tar retains its default path/link safety. Only a pinned archive
            // with reviewed top-level paths can reach this extraction step.
            let listing = try await execute(URL(fileURLWithPath: "/usr/bin/tar"), ["-tzf", archive.path])
            guard listing.split(separator: "\n").allSatisfy({ line in
                let parts = line.split(separator: "/")
                return !line.hasPrefix("/") && parts.first == Substring(artifact.archive_root) && !parts.contains("..")
            }) else { throw SetupFailure("Unsafe bootstrap archive paths.") }
            if FileManager.default.fileExists(atPath: extracted.path) { try FileManager.default.removeItem(at: extracted) }
            try privateDirectory(extracted)
            _ = try await execute(URL(fileURLWithPath: "/usr/bin/tar"), ["-xzf", archive.path, "-C", extracted.path])
            let records = try bootstrapInventory(extracted)
            try JSONEncoder().encode(records).write(to: receipt, options: .atomic)
        }
        try Task.checkCancellation()
        return extracted.appendingPathComponent(artifact.executable)
    }
    func bootstrapInventory(_ directory: URL) throws -> [String: String] {
        try safeDestination(directory)
        // Normalize both sides: Foundation can represent /private/tmp as /tmp.
        let canonicalRoot = directory.resolvingSymlinksInPath().standardizedFileURL.path
        var records: [String: String] = [:]
        let iterator = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])!
        for case let file as URL in iterator {
            let name = String(file.path.dropFirst(directory.path.count + 1))
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            if values.isSymbolicLink == true {
                let resolved = file.resolvingSymlinksInPath().standardizedFileURL.path
                guard resolved.hasPrefix(canonicalRoot + "/") else { throw SetupFailure("Bootstrap link escapes its private directory: \(file.path)") }
                records[name] = "link:" + (try FileManager.default.destinationOfSymbolicLink(atPath: file.path))
            } else if values.isRegularFile == true { records[name] = try hashFile(file) }
        }
        return records
    }
    func execute(_ executable: URL, _ arguments: [String]) async throws -> String {
        try Task.checkCancellation()
        let p = Process(); p.executableURL = executable; p.arguments = arguments; p.environment = sanitizedEnvironment()
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe; p.standardInput = FileHandle.nullDevice
        process = p
        let result: (Int32, String) = try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global().async {
                do {
                    try p.run()
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    p.waitUntilExit()
                    continuation.resume(returning: (p.terminationStatus, String(decoding: data, as: UTF8.self)))
                } catch { continuation.resume(throwing: error) }
            }
        }
        process = nil; try Task.checkCancellation()
        guard result.0 == 0 else { throw SetupFailure("\(executable.lastPathComponent) failed: \(result.1.suffix(2000))") }
        return result.1
    }
    func backend(_ executable: URL, _ arguments: [String], _ environment: [String: String]) async throws -> Int32 {
        let p = Process(); p.executableURL = executable; p.arguments = arguments; p.environment = environment
        let stdout = Pipe(); let stderr = Pipe(); let stdin = Pipe()
        p.standardOutput = stdout; p.standardError = stderr; p.standardInput = stdin
        process = p; input = stdin.fileHandleForWriting
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global().async {
                do {
                    try p.run()
                    let group = DispatchGroup()
                    for (pipe, json) in [(stdout, true), (stderr, false)] {
                        group.enter()
                        DispatchQueue.global().async {
                            while true {
                                let data = pipe.fileHandleForReading.availableData
                                if data.isEmpty { break }
                                let text = String(decoding: data, as: UTF8.self)
                                Task { @MainActor in
                                    if json { self.receive(text) } else { self.append(text) }
                                }
                            }
                            group.leave()
                        }
                    }
                    p.waitUntilExit(); group.wait()
                    Task { @MainActor in continuation.resume(returning: p.terminationStatus) }
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    func receive(_ text: String) {
        outputBuffer += text
        while let newline = outputBuffer.firstIndex(of: "\n") {
            let line = String(outputBuffer[..<newline]); outputBuffer.removeSubrange(...newline)
            do {
                let object = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] ?? [:]
                guard object["protocol"] as? Int == 1 else { throw SetupFailure("Unsupported setup protocol.") }
                switch object["type"] as? String {
                case "plan":
                    planID = object["plan_id"] as? String ?? ""
                    details = object["description"] as? String ?? ""
                    prefix = URL(fileURLWithPath: object["prefix"] as! String)
                    phase = "installReview"; message = "Review the installation plan"
                    stage = 2
                case "progress": message = object["message"] as? String ?? "Working…"
                case "complete":
                    backendStatus = "complete"
                    let python = object["python"] as! String
                    let manifest = object["manifest"] as? String
                    completion = "Command:\n\(python)\n\nArguments:\n-B -m composerlint.server"
                    if let manifest { completion += "\n\nServer environment:\nCOMPOSERLINT_RENDERER_CONFIG=\(manifest)" }
                    completion += "\n\nSkills/source folder:\n\(source!.path)\n\nOpen this folder in Codex, reconnect the MCP server, and start a fresh chat. Verify version 0.2.0 and 18 tools. Project-local Skills: composer-import, composer-review, composer-edit.\n\nNo global Codex settings were changed. Preserve any intentional export/data overrides."
                case "error": backendStatus = object["message"] as? String ?? "Setup failed"; append(backendStatus + "\n")
                case "cancelled": backendStatus = "Cancelled"
                default: throw SetupFailure("Unknown setup protocol event.")
                }
            } catch { backendStatus = error.localizedDescription; cancel() }
        }
    }
    func approveInstallation() {
        guard phase == "installReview", !planID.isEmpty else { return }
        do {
            let data = try JSONSerialization.data(withJSONObject: ["action": "approve", "plan_id": planID])
            try input?.write(contentsOf: data + Data([10]))
            phase = "running"; message = "Starting approved installation…"
            stage = 3
        } catch { fail(error); cancel() }
    }
    func readBackendLog() {
        guard let prefix, let handle = try? FileHandle(forReadingFrom: prefix.appendingPathComponent("setup.log")) else { return }
        defer { try? handle.close() }
        do {
            try handle.seek(toOffset: logOffset)
            if let data = try handle.read(upToCount: 256 * 1024), !data.isEmpty { logOffset += UInt64(data.count); append(String(decoding: data, as: UTF8.self)) }
        } catch { append("Could not read setup log: \(error.localizedDescription)\n") }
    }
    func cancel() {
        cancelled = true; message = "Cancelling safely…"
        job?.cancel()
        if let process, process.isRunning { process.interrupt() }
    }
    func fail(_ error: Error) { phase = "error"; message = error.localizedDescription; append("\n\(message)\n") }
    func copyInstructions() { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(completion, forType: .string) }
    func saveLog() {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "ComposerLint-setup-log.txt"
        if panel.runModal() == .OK, let url = panel.url { try? log.write(to: url, atomically: true, encoding: .utf8) }
    }
}

struct SetupView: View {
    @ObservedObject var model: SetupModel
    private let accent = Color(red: 0.30, green: 0.78, blue: 0.66)
    private let steps = [
        ("Choose your setup", "Full experience or text analysis"),
        ("Prepare tools", "Review private Python and uv tools"),
        ("Review installation", "Approve the complete plan"),
        ("Install & verify", "Build locally, then run checks"),
        ("Connect to Codex", "Copy your connection instructions")
    ]
    private var title: String {
        switch model.phase {
        case "welcome": return "Make room for your next idea."
        case "bootstrapReview": return "Your tools. Your permission."
        case "installReview": return "Review before we install."
        case "complete": return "ComposerLint is ready."
        case "cancelled": return "Pick up where you left off."
        case "error", "unsupported": return "Let's resolve this first."
        default: return "Setting things up for you."
        }
    }
    private var subtitle: String {
        switch model.phase {
        case "welcome": return "A guided installation for your music workspace."
        case "bootstrapReview": return "Nothing is downloaded until you approve the plan below."
        case "installReview": return "Your tools are verified. Check the packages, build and destinations."
        case "complete": return "One final connection, then bring your score to the conversation."
        case "cancelled", "error": return "Verified work is retained. Review the details before retrying."
        default: return "Follow the real installation stages. You can cancel safely."
        }
    }
    private var interrupted: Bool { ["error", "cancelled", "unsupported"].contains(model.phase) }
    private var icon: NSImage {
        NSImage(contentsOf: model.resources.appendingPathComponent("ComposerLint.icns")) ?? NSApplication.shared.applicationIconImage
    }
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 22) {
                Image(nsImage: icon).resizable().frame(width: 76, height: 76).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 5) {
                    Text("ComposerLint").font(.title2.bold())
                    Text("SETUP · APPLE SILICON").font(.system(size: 10, weight: .semibold)).tracking(1.3).foregroundStyle(.white.opacity(0.65))
                }
                VStack(alignment: .leading, spacing: 23) {
                    ForEach(steps.indices, id: \.self) { index in
                        HStack(alignment: .top, spacing: 11) {
                            ZStack {
                                Circle().fill(index <= model.stage ? accent.opacity(index == model.stage ? 1 : 0.20) : Color.white.opacity(0.08))
                                if index < model.stage {
                                    Image(systemName: "checkmark").font(.system(size: 11, weight: .bold))
                                } else if index == model.stage && interrupted {
                                    Image(systemName: "pause.fill").font(.system(size: 10, weight: .bold))
                                } else { Text(String(index + 1)).font(.system(size: 11, weight: .semibold)) }
                            }.frame(width: 26, height: 26)
                                .foregroundStyle(index == model.stage ? Color(red: 0.04, green: 0.18, blue: 0.21) : .white)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(steps[index].0).font(.system(size: 13, weight: index == model.stage ? .bold : .medium))
                                Text(steps[index].1).font(.system(size: 11)).foregroundStyle(.white.opacity(0.60)).fixedSize(horizontal: false, vertical: true)
                            }
                        }.opacity(index > model.stage ? 0.55 : 1)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel("Step \(index + 1): \(steps[index].0), \(index < model.stage ? "complete" : index == model.stage ? "current" : "pending")")
                    }
                }.padding(.top, 8)
                Spacer(minLength: 12)
                Label("Local by design", systemImage: "lock.shield").font(.system(size: 12, weight: .semibold))
                Text("Your scores stay yours. No global Codex settings are changed.")
                    .font(.system(size: 11)).foregroundStyle(.white.opacity(0.65)).fixedSize(horizontal: false, vertical: true)
            }.padding(26).frame(width: 250).foregroundStyle(.white)
                .background(LinearGradient(colors: [Color(red: 0.06, green: 0.13, blue: 0.23), Color(red: 0.06, green: 0.25, blue: 0.28)], startPoint: .topLeading, endPoint: .bottomTrailing))
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    Text(model.phase == "complete" ? "SETUP COMPLETE" : "STEP \(model.stage + 1) OF 5")
                        .font(.system(size: 11, weight: .bold)).tracking(1.3).foregroundStyle(.secondary)
                    Spacer()
                    Text("v0.2.0").font(.caption).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(title).font(.system(size: 28, weight: .bold, design: .rounded)).fixedSize(horizontal: false, vertical: true)
                    Text(subtitle).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if model.phase == "welcome" {
                            VStack(alignment: .leading, spacing: 14) {
                                Text("Choose your installation").font(.headline)
                                modeCard("full", title: "Full experience", subtitle: "Notation, analysis and verified editing", symbol: "music.note.list")
                                modeCard("text", title: "Text analysis only", subtitle: "Musical analysis without renderer compilation", symbol: "text.alignleft")
                            }
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Before you begin").font(.headline)
                                Label(model.message, systemImage: model.hasTools ? "checkmark.circle.fill" : "exclamationmark.circle")
                                    .font(.subheadline).foregroundStyle(model.hasTools ? Color.primary : .orange)
                                Text("Private Python \(model.pins.python.version) and uv \(model.pins.uv.version) tools will be downloaded after your approval.")
                                    .font(.subheadline).foregroundStyle(.secondary)
                                Text("Installation folder").font(.caption.bold())
                                Text(model.root.path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            }.padding(18).frame(maxWidth: .infinity, alignment: .leading).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
                            Label("Not Developer ID signed or notarized. First launch may require Apple's per-app Open Anyway approval. See the release instructions.", systemImage: "info.circle")
                                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        } else {
                            HStack(alignment: .top, spacing: 12) {
                                if model.busy && model.phase != "installReview" { ProgressView().controlSize(.small).padding(.top, 2) }
                                else { Image(systemName: model.phase == "complete" ? "checkmark.seal.fill" : interrupted ? "exclamationmark.circle" : "list.bullet.clipboard").foregroundStyle(model.phase == "complete" ? accent : .secondary) }
                                Text(model.message).font(.headline).textSelection(.enabled)
                            }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
                            Text(model.phase == "complete" ? model.completion : model.details)
                                .font(.system(size: 12, design: model.phase == "complete" ? .monospaced : .default))
                                .lineSpacing(4).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        }
                        if !model.log.isEmpty {
                            DisclosureGroup("Installation details and errors") {
                                ScrollView { Text(model.log).font(.system(.caption, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }.frame(height: 180)
                            }.font(.caption)
                        }
                    }.padding(.trailing, 8)
                }
                Divider()
                actions
            }.padding(30).frame(minWidth: 570, maxWidth: .infinity)
                .background(Color(nsColor: .windowBackgroundColor))
        }.frame(minWidth: 880, minHeight: 700).tint(Color(red: 0.08, green: 0.49, blue: 0.42))
    }
    private func modeCard(_ value: String, title: String, subtitle: String, symbol: String) -> some View {
        Button { model.mode = value } label: {
            HStack(spacing: 14) {
                Image(systemName: symbol).font(.title2).frame(width: 30)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.headline)
                    Text(subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: model.mode == value ? "checkmark.circle.fill" : "circle").foregroundStyle(model.mode == value ? Color.accentColor : .secondary)
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
                .background(model.mode == value ? Color.accentColor.opacity(0.08) : Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(model.mode == value ? Color.accentColor : Color.secondary.opacity(0.20), lineWidth: model.mode == value ? 1.5 : 1))
        }.buttonStyle(.plain).accessibilityValue(model.mode == value ? "Selected" : "Not selected")
    }
    private var actions: some View {
        HStack(spacing: 10) {
            if model.phase == "welcome" {
                if !model.hasTools {
                    Button("Install Apple Tools…") { model.requestAppleTools() }
                    Button("Recheck") { model.check() }
                }
                Button("Close") { NSApplication.shared.terminate(nil) }
                Spacer()
                Button("Review Setup") { model.review() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            } else if model.phase == "bootstrapReview" {
                Button("Back") { model.phase = "welcome"; model.check() }
                Spacer()
                Button("Approve Prerequisites") { model.start() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            } else if model.phase == "installReview" {
                Button("Cancel") { model.cancel() }.disabled(model.cancelled)
                Spacer()
                Button("Approve Installation") { model.approveInstallation() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            } else if model.phase == "complete" {
                Menu("More") {
                    Button("Show Source Folder") { if let source = model.source { NSWorkspace.shared.open(source) } }
                    Button("Check / Run Again") { model.phase = "welcome"; model.check() }
                }
                Button("Close") { NSApplication.shared.terminate(nil) }
                Spacer()
                Button("Copy Connection Instructions") { model.copyInstructions() }.buttonStyle(.borderedProminent)
            } else if interrupted {
                Menu("Help") {
                    Button("Save Log…") { model.saveLog() }
                    Button("Manual Instructions") { NSWorkspace.shared.open(URL(string: "https://github.com/kyc-212/ComposerLint/blob/main/docs/INSTALL.md")!) }
                }
                Button("Close") { NSApplication.shared.terminate(nil) }
                Spacer()
                if model.phase != "unsupported" { Button("Retry") { model.phase = "welcome"; model.check() }.buttonStyle(.borderedProminent) }
            } else {
                Text("Verified work can be reused on retry.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") { model.cancel() }.disabled(model.cancelled)
            }
        }.controlSize(.large)
    }
}
final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: SetupModel?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if model?.busy == true { model?.cancel(); return .terminateCancel }
        return .terminateNow
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
#if !SETUP_CORE_TEST
@main struct ComposerLintSetupApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = SetupModel()
    var body: some Scene {
        WindowGroup("ComposerLint Setup") {
            SetupView(model: model).onAppear { delegate.model = model; NSApp.activate(ignoringOtherApps: true) }
        }.defaultSize(width: 940, height: 740)
    }
}
#endif
