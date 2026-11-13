import Cocoa
import Foundation
import Darwin

// This application deliberately has no launch agent, login item, service, or network server.
// The Python supervisor owns retries, clock preflight, SSH, and its own singleton lock.
private let runtimeURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/RBE201TC/connection", isDirectory: true)
private let logDirectory = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/RBE201TC", isDirectory: true)
private let consoleURL = URL(string: "http://127.0.0.1:8765/")!
private let healthURL = URL(string: "http://127.0.0.1:8765/api/v1/health")!

private struct ConnectionState: Decodable {
    let supervisor_pid: Int32
    let child_pid: Int32?
    let started_by_app: Bool?
}

private final class AppLog {
    private let lock = NSLock()
    private var handle: FileHandle?

    init() {
        do {
            try FileManager.default.createDirectory(at: logDirectory,
                withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let file = logDirectory.appendingPathComponent("desktop-app.log")
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil,
                    attributes: [.posixPermissions: 0o600])
            }
            handle = try FileHandle(forWritingTo: file)
            handle?.seekToEndOfFile()
        } catch {
            NSLog("Unable to open desktop log: %@", String(describing: error))
        }
    }

    func write(_ text: String) {
        lock.lock()
        defer { lock.unlock() }
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(text)\n"
        if let bytes = line.data(using: .utf8) { handle?.write(bytes) }
    }
}

// A Process object, not a PID read from disk, is the authority for termination.
// Only this app's running Process may be signalled. Never adopt a pre-existing PID.
private final class OwnedSupervisor {
    private let lock = NSLock()
    private var process: Process?
    private var output: FileHandle?
    private var stopping = false

    var isStopping: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopping
    }

    func launch() throws {
        lock.lock()
        defer { lock.unlock() }
        if stopping || process != nil { return }
        let script = runtimeURL.appendingPathComponent("supervise_wifi_connection.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw NSError(domain: "RBE201TC", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "连接组件尚未安装，请先完成 runtime 部署。"])
        }
        let log = logDirectory.appendingPathComponent("desktop-supervisor.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil,
                attributes: [.posixPermissions: 0o600])
        }
        let stream = try FileHandle(forWritingTo: log)
        stream.seekToEndOfFile()
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        child.arguments = ["-B", "-u", script.path, "--run", "--owned-by-app"]
        child.currentDirectoryURL = runtimeURL
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        child.environment = environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = stream
        child.standardError = stream
        try child.run()
        output = stream
        process = child
    }

    func runningPID() -> pid_t? {
        lock.lock()
        defer { lock.unlock() }
        guard !stopping, let child = process, child.isRunning else { return nil }
        return child.processIdentifier
    }

    func exitStatus() -> Int32? {
        lock.lock()
        defer { lock.unlock() }
        guard let child = process, !child.isRunning else { return nil }
        return child.terminationStatus
    }

    func stop() {
        lock.lock()
        defer { lock.unlock() }
        if stopping { return }
        stopping = true
        guard let child = process, child.isRunning else { return }
        let pid = child.processIdentifier
        guard pid > 1 else { return }
        if getpgid(pid) == pid {
            // Python's --owned-by-app calls setsid; its SSH/preflight descendants share this group.
            _ = Darwin.kill(-pid, SIGTERM)
        } else {
            // Very early launch or failed setsid: terminate only the Process this app created.
            child.terminate()
        }
    }
}

private struct ListenerSnapshot {
    var pids = Set<pid_t>()
    var names = Set<String>()
}

// A health response must come directly from the verified loopback endpoint.
// Never follow an HTTP redirect to another host or port.
private final class LocalHealthDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var statusField: NSTextField!
    private var detailField: NSTextField!
    private var spinner: NSProgressIndicator!
    private var openButton: NSButton!
    private let owner = OwnedSupervisor()
    private let log = AppLog()
    private let queue = DispatchQueue(label: "RBE201TC.desktop.connection", qos: .utility)
    private var timer: DispatchSourceTimer?
    private var session: URLSession!
    private var probing = false
    private var openedAutomatically = false
    private var requestedOpen = false
    private var launched = false
    private var lastStatus = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeWindow()
        NSApp.activate(ignoringOtherApps: true)
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.timeoutIntervalForResource = 4
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.connectionProxyDictionary = [:]
        config.httpShouldSetCookies = false
        session = URLSession(configuration: config, delegate: LocalHealthDelegate(), delegateQueue: nil)
        queue.async { [weak self] in self?.beginConnection() }
    }

    private func makeWindow() {
        NSApp.setActivationPolicy(.regular)
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出并断开", action: #selector(quit), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)
        NSApp.mainMenu = mainMenu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 570, height: 320),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "小车控制台"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()

        let title = NSTextField(labelWithString: "小车无线控制台")
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        statusField = NSTextField(wrappingLabelWithString: "正在检查连接组件…")
        statusField.font = .systemFont(ofSize: 15, weight: .medium)
        detailField = NSTextField(wrappingLabelWithString: "仅在打开本 App 时连接；关闭窗口或退出后断开本 App 拥有的连接。")
        detailField.textColor = .secondaryLabelColor
        detailField.font = .systemFont(ofSize: 12)
        detailField.preferredMaxLayoutWidth = 490
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.startAnimation(nil)
        openButton = NSButton(title: "打开控制页面", target: self, action: #selector(openConsole))
        openButton.bezelStyle = .rounded
        openButton.isEnabled = false
        let quitButton = NSButton(title: "退出并断开", target: self, action: #selector(quit))
        quitButton.bezelStyle = .rounded
        let buttons = NSStackView(views: [openButton, quitButton])
        buttons.orientation = .horizontal
        buttons.spacing = 14
        let statusRow = NSStackView(views: [spinner, statusField])
        statusRow.spacing = 10
        statusRow.alignment = .centerY
        let note = NSTextField(labelWithString: "无开机 / 登录自启 · 仅使用本机 127.0.0.1:8765")
        note.font = .systemFont(ofSize: 11)
        note.textColor = .tertiaryLabelColor
        let stack = NSStackView(views: [title, statusRow, detailField, buttons, note])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 22
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -32),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 28),
            detailField.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
        window.makeKeyAndOrderFront(nil)
    }

    private func state() -> ConnectionState? {
        let path = runtimeURL.appendingPathComponent("wifi/state.json")
        guard let data = try? Data(contentsOf: path), data.count < 16384 else { return nil }
        return try? JSONDecoder().decode(ConnectionState.self, from: data)
    }

    // This runs only on the worker queue, never on Cocoa's main thread.
    private func listeners() throws -> ListenerSnapshot {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        task.arguments = ["-nP", "-iTCP:8765", "-sTCP:LISTEN", "-Fpn"]
        task.standardInput = FileHandle.nullDevice
        let stdout = Pipe()
        let stderr = Pipe()
        task.standardOutput = stdout
        task.standardError = stderr
        try task.run()
        let bytes = stdout.fileHandleForReading.readDataToEndOfFile()
        let errors = stderr.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        if task.terminationStatus != 0 && !(task.terminationStatus == 1 && bytes.isEmpty && errors.isEmpty) {
            throw NSError(domain: "RBE201TC", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "无法核验 8765 端口的进程归属。"])
        }
        var snapshot = ListenerSnapshot()
        for line in String(decoding: bytes, as: UTF8.self).split(separator: "\n") {
            if line.first == "p", let pid = Int32(line.dropFirst()), pid > 1 {
                snapshot.pids.insert(pid)
            } else if line.first == "n" {
                snapshot.names.insert(String(line.dropFirst()))
            }
        }
        return snapshot
    }

    private func verifiedOwnedChild() throws -> pid_t? {
        guard let supervisor = owner.runningPID(), supervisor > 1,
              let current = state(), current.supervisor_pid == supervisor,
              current.started_by_app == true, let child = current.child_pid, child > 1,
              getpgid(supervisor) == supervisor, getpgid(child) == supervisor else { return nil }
        let listener = try listeners()
        guard listener.pids == Set([child]), listener.names == Set(["127.0.0.1:8765"]) else { return nil }
        // Recheck state after lsof; a restarted SSH worker must not inherit an earlier health result.
        guard owner.runningPID() == supervisor, let latest = state(),
              latest.supervisor_pid == supervisor, latest.child_pid == child,
              latest.started_by_app == true, getpgid(child) == supervisor else { return nil }
        return child
    }

    private func beginConnection() {
        guard !owner.isStopping else { return }
        do {
            // A saved PID can belong to an unrelated process after a reboot.
            // The supervisor's canonical flock, not stale PID data, decides ownership.
            // A losing supervisor exits 73 before creating any SSH worker.
            if !(try listeners()).pids.isEmpty {
                render("8765 端口已被占用", "无法确认该监听属于本 App，因此不会启动连接、打开网页或停止该进程。", ready: false, busy: false)
                return
            }
            try owner.launch()
            guard !owner.isStopping else { return }
            launched = true
            log.write("Started owned supervisor pid=\(owner.runningPID() ?? 0)")
            let ticker = DispatchSource.makeTimerSource(queue: queue)
            ticker.schedule(deadline: .now(), repeating: .seconds(2))
            ticker.setEventHandler { [weak self] in self?.poll() }
            timer = ticker
            ticker.resume()
        } catch {
            log.write("Launch refused/failed: \(error.localizedDescription)")
            render("未能启动连接", error.localizedDescription + " 日志：~/Library/Logs/RBE201TC", ready: false, busy: false)
        }
    }

    private func poll() {
        guard !owner.isStopping, launched else { return }
        guard owner.runningPID() != nil else {
            if owner.exitStatus() == 73 {
                render("已有连接由其他实例拥有", "为保留原连接，本 App 不重建、不接管，也不会在退出时停止它。请使用原有窗口。", ready: false, busy: false)
            } else {
                let status = owner.exitStatus().map(String.init) ?? "未知"
                render("连接进程已结束（\(status)）", "未接管其他连接。请查看 ~/Library/Logs/RBE201TC；修复后重新打开本 App。", ready: false, busy: false)
            }
            return
        }
        guard !probing else { return }
        do {
            guard let child = try verifiedOwnedChild() else {
                render("正在等待网络 / 建立安全连接…", "连接组件会自动重试。只有确认本 App 的 SSH 监听且健康检查成功后，才会打开控制页面。", ready: false, busy: true)
                return
            }
            probing = true
            var request = URLRequest(url: healthURL)
            request.timeoutInterval = 3
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            session.dataTask(with: request) { [weak self] data, response, error in
                guard let self = self else { return }
                self.queue.async {
                    self.probing = false
                    guard !self.owner.isStopping else { return }
                    let http = response as? HTTPURLResponse
                    let health = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
                    let healthy = error == nil && http?.statusCode == 200 && health != nil &&
                        (health?["ok"] as? Bool != false) && (health?["status"] as? String != "error")
                    do {
                        guard try self.verifiedOwnedChild() == child else {
                            self.render("连接正在更新…", "已丢弃旧连接的健康检查结果，等待当前 SSH 重新就绪。", ready: false, busy: true)
                            return
                        }
                    } catch {
                        self.render("无法核验连接归属", error.localizedDescription, ready: false, busy: false)
                        return
                    }
                    guard healthy else {
                        self.render("SSH 已连接，等待控制服务…", "健康检查尚未成功；不会打开可能不安全的页面。连接组件会继续重试。", ready: false, busy: true)
                        return
                    }
                    self.render("已连接 · 控制页面就绪", "已确认 8765 监听属于本 App 的 SSH 进程。关闭窗口或退出会停止本 App 的连接。", ready: true, busy: false)
                    if !self.openedAutomatically || self.requestedOpen {
                        self.openedAutomatically = true
                        self.requestedOpen = false
                        DispatchQueue.main.async {
                            guard !self.owner.isStopping else { return }
                            if !NSWorkspace.shared.open(consoleURL) {
                                self.log.write("Default browser could not open the console URL")
                            }
                        }
                    }
                }
            }.resume()
        } catch {
            render("无法核验连接归属", error.localizedDescription, ready: false, busy: false)
        }
    }

    private func render(_ status: String, _ detail: String, ready: Bool, busy: Bool) {
        if status != lastStatus {
            log.write(status)
            lastStatus = status
        }
        DispatchQueue.main.async { [weak self] in
            guard let self = self, !self.owner.isStopping else { return }
            self.statusField.stringValue = status
            self.detailField.stringValue = detail
            self.openButton.isEnabled = ready
            if busy { self.spinner.startAnimation(nil) } else { self.spinner.stopAnimation(nil) }
            self.spinner.isHidden = !busy
        }
    }

    @objc private func openConsole() {
        openButton.isEnabled = false
        queue.async { [weak self] in
            self?.requestedOpen = true
            self?.poll()
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.deminiaturize(nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // This does not wait for network I/O and never signals a PID taken from state.json.
        owner.stop()
        session?.invalidateAndCancel()
        log.write("App terminating: SIGTERM sent only to owned Process / verified process group")
    }
}

let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.run()
