import Cocoa
import ApplicationServices

// Read-only App navigation: never invoke 标熟, 重学, answer buttons, or paid export.
enum Failure: Error, CustomStringConvertible {
    case message(String)
    var description: String { switch self { case .message(let text): return text } }
}
func fail(_ message: String) throws -> Never { throw Failure.message(message) }
func attr(_ el: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(el, key as CFString, &value) == .success ? value : nil
}
func str(_ el: AXUIElement, _ key: String) -> String { attr(el, key) as? String ?? "" }
func label(_ el: AXUIElement) -> String {
    for key in [kAXDescriptionAttribute, kAXTitleAttribute, kAXValueAttribute] {
        let text = str(el, key)
        if !text.isEmpty { return text }
    }
    return ""
}
func actions(_ el: AXUIElement) -> [String] {
    var value: CFArray?
    AXUIElementCopyActionNames(el, &value)
    return value as? [String] ?? []
}
func point(_ el: AXUIElement, _ key: String) -> CGPoint {
    var result = CGPoint.zero
    if let value = attr(el, key), CFGetTypeID(value) == AXValueGetTypeID() {
        AXValueGetValue(value as! AXValue, .cgPoint, &result)
    }
    return result
}
func size(_ el: AXUIElement) -> CGSize {
    var result = CGSize.zero
    if let value = attr(el, kAXSizeAttribute), CFGetTypeID(value) == AXValueGetTypeID() {
        AXValueGetValue(value as! AXValue, .cgSize, &result)
    }
    return result
}
func snapshot(_ root: AXUIElement) -> [AXUIElement] {
    var result: [AXUIElement] = []
    func visit(_ el: AXUIElement, _ depth: Int) {
        guard depth < 45, result.count < 12000 else { return }
        result.append(el)
        for child in attr(el, kAXChildrenAttribute) as? [AXUIElement] ?? [] { visit(child, depth + 1) }
    }
    visit(root, 0)
    return result
}
func settleUI() { Thread.sleep(forTimeInterval: 0.65) }
func click(_ el: AXUIElement) throws {
    if actions(el).contains(kAXPressAction), AXUIElementPerformAction(el, kAXPressAction as CFString) == .success {
        settleUI()
        return
    }
    let origin = point(el, kAXPositionAttribute), extent = size(el)
    guard extent.width > 0, extent.height > 0 else { try fail("目标没有可点击区域；请打开我的内容 → 近日已学。") }
    let center = CGPoint(x: origin.x + extent.width / 2, y: origin.y + extent.height / 2)
    for type in [CGEventType.leftMouseDown, .leftMouseUp] {
        CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: center, mouseButton: .left)?.post(tap: .cghidEventTap)
    }
    settleUI()
}
func press(_ name: String, root: AXUIElement) throws {
    let candidates = snapshot(root).filter { label($0) == name }
    guard let target = candidates.first(where: { str($0, kAXRoleAttribute) == "AXButton" }) ?? candidates.first else {
        try fail("界面未找到“\(name)”；请打开我的内容 → 近日已学 → 今天。")
    }
    try click(target)
}
func wordLabels(_ root: AXUIElement) -> [String] {
    snapshot(root).filter { str($0, kAXRoleAttribute) == "AXStaticText" }.map(label).filter {
        $0.range(of: "^[A-Za-z][A-Za-z '’()./–-]*$", options: .regularExpression) != nil
    }
}
func scroll(_ root: AXUIElement, _ direction: String) throws -> Bool {
    let action = direction == "up" ? "AXScrollUpByPage" : "AXScrollDownByPage"
    guard let target = snapshot(root).first(where: {
        str($0, kAXRoleAttribute) == "AXGroup" && actions($0).contains(action)
    }) else { return false }
    guard AXUIElementPerformAction(target, action as CFString) == .success else { try fail("列表滚动失败，未保存不完整词表。") }
    settleUI()
    return true
}
func count(_ root: AXUIElement) throws -> Int {
    let labels = snapshot(root).map(label)
    if let text = labels.first(where: { $0.range(of: "^\\d+\\s*词$", options: .regularExpression) != nil }) {
        return Int(text.filter(\.isNumber))!
    }
    if labels.contains(where: { $0.contains("还没有") && $0.contains("词") }) { return 0 }
    try fail("未读到词数；界面可能未加载完成或已更新。保留已有日档。")
}
func collect(_ root: AXUIElement, _ tab: String) throws -> (Int, [String]) {
    try press(tab, root: root)
    let expected = try count(root)
    if expected == 0 { return (0, []) }
    var atTop = false
    for _ in 0..<180 {
        let before = wordLabels(root)
        if try !scroll(root, "up") { atTop = true; break }
        if before == wordLabels(root) { atTop = true; break }
    }
    guard atTop else { try fail("超过滚动上限，未确认列表开头；未保存日档。") }
    var words: [String] = [], seen = Set<String>(), atBottom = false
    for _ in 0..<180 {
        let page = wordLabels(root)
        for word in page {
            let key = word.precomposedStringWithCompatibilityMapping.lowercased()
            if seen.insert(key).inserted { words.append(word) }
        }
        if try !scroll(root, "down") { atBottom = true; break }
        if page == wordLabels(root) { atBottom = true; break }
    }
    guard atBottom, words.count == expected, try count(root) == expected else {
        try fail("\(tab)页显示 \(expected) 词，实际读到 \(words.count) 词，未保存不完整日档；请保持 App 窗口不变后重试。")
    }
    return (expected, words)
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    if args.contains("--help") {
        print("swift collect_today.swift [--root /absolute/archive/path]\n读取不背单词今天的学习/复习/已标熟列表，校验后写 daily/YYYY-MM-DD.json。")
        exit(0)
    }
    let rootIndex = args.firstIndex(of: "--root")
    if let index = rootIndex, index + 1 >= args.count { try fail("--root 需要目录路径") }
    let archive = rootIndex.map { args[$0 + 1] } ?? NSHomeDirectory() + "/ielts-context-coach-data"
    guard AXIsProcessTrusted() else { try fail("当前进程没有辅助功能权限；请在 macOS 设置中为运行此脚本的终端或 AI 应用启用辅助功能后重试。") }
    let startedAt = Date()
    let bundle = "cn.com.langeasy.LangEasyLexis"
    if NSRunningApplication.runningApplications(withBundleIdentifier: bundle).isEmpty {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/不背单词.app"))
        Thread.sleep(forTimeInterval: 2)
    }
    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first else { try fail("不背单词没有运行。") }
    app.activate()
    settleUI()
    let appRoot = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(appRoot, 2)
    var labels = snapshot(appRoot).map(label)
    if labels.contains("Learn"), labels.contains("Review") {
        guard let window = (attr(appRoot, kAXWindowsAttribute) as? [AXUIElement])?.first else { try fail("未找到 App 窗口。") }
        let bottom = point(window, kAXPositionAttribute).y + size(window).height
        let buttons = snapshot(window).filter {
            str($0, kAXRoleAttribute) == "AXButton" && point($0, kAXPositionAttribute).y > bottom - 100
        }.sorted { point($0, kAXPositionAttribute).x < point($1, kAXPositionAttribute).x }
        guard buttons.count == 3 else { try fail("首页布局与记录不同，请手动打开我的内容 → 近日已学。") }
        try click(buttons[1])
        labels = snapshot(appRoot).map(label)
    }
    if labels.contains("近日已学") {
        try press("近日已学", root: appRoot)
        labels = snapshot(appRoot).map(label)
    }
    guard labels.contains("今天"), labels.contains("学习"), labels.contains("复习") else {
        try fail("请打开不背单词：我的内容 → 近日已学 → 今天，再开始收集。当前页面未确认是今天。")
    }
    var lists: [String: [String]] = [:], counts: [String: Int] = [:]
    for tab in ["学习", "复习", "已标熟"] {
        let (expected, words) = try collect(appRoot, tab)
        lists[tab] = words; counts[tab] = expected
    }
    // Refuse a snapshot spanning local midnight.
    let formatter = DateFormatter()
    formatter.timeZone = TimeZone(identifier: "Asia/Shanghai")
    formatter.dateFormat = "yyyy-MM-dd"
    let day = formatter.string(from: Date())
    guard day == formatter.string(from: startedAt) else { try fail("采集跨过午夜，请重新采集今天的词表。") }
    let payload: [String: Any] = ["schema_version": 1, "date": day, "timezone": "Asia/Shanghai",
        "collected_at": ISO8601DateFormatter().string(from: Date()), "source": "不背单词·近日已学·今天",
        "counts": counts, "lists": lists]
    let folder = URL(fileURLWithPath: archive).appendingPathComponent("daily")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let destination = folder.appendingPathComponent(day + ".json")
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    try data.write(to: destination, options: .atomic)
    print(String(data: data, encoding: .utf8)!)
    fputs("\n已保存：\(destination.path)\n", stderr)
    try? press("复习", root: appRoot)
} catch {
    fputs("收集失败：\(error)\n", stderr)
    exit(1)
}
