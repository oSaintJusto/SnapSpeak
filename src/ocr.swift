// ocr.swift —— 基于 macOS Vision 框架的本地文字识别
//
// 编译: swiftc -O -o bin/ocr src/ocr.swift -framework Vision -framework AppKit
//
// 用法:
//   ocr <图片路径>                 识别图片文件
//   ocr --paste                    识别剪贴板里的图片
//   ocr -                          识别 stdin 读入的 PNG
//   ocr 图片 -l zh-Hans,en-US      指定语言优先级
//   ocr 图片 -c 0.5                置信度低于 0.5 的丢掉
//   ocr 图片 --json                输出带坐标和置信度的 JSON

import AppKit
import Foundation
import Vision

// ---------------------------------------------------------------- 参数解析
var langs = ["zh-Hans", "zh-Hant", "en-US"]
var minConf: Float = 0.0
var asJSON = false
var spec: String?

var argv = Array(CommandLine.arguments.dropFirst())
var i = 0
while i < argv.count {
    let a = argv[i]
    switch a {
    case "-l", "--langs":
        i += 1
        if i < argv.count {
            langs = argv[i].split(separator: ",").map { String($0).trimmingCharacters(in: .whitespaces) }
        }
    case "-c", "--min-confidence":
        i += 1
        if i < argv.count, let v = Float(argv[i]) { minConf = v }
    case "--json":
        asJSON = true
    case "-h", "--help":
        FileHandle.standardError.write(Data("用法: ocr <图片> | --paste | -\n".utf8))
        exit(0)
    default:
        if spec == nil { spec = a }
    }
    i += 1
}

guard let target = spec else {
    FileHandle.standardError.write(Data("缺少图片路径(或用 --paste / -)\n".utf8))
    exit(64)
}

// ---------------------------------------------------------------- 读图
func loadCGImage(_ spec: String) -> CGImage? {
    if spec == "--paste" || spec == "-p" {
        let pb = NSPasteboard.general
        let types: [NSPasteboard.PasteboardType] = [.png, .tiff]
        for t in types {
            if let d = pb.data(forType: t), let rep = NSBitmapImageRep(data: d), let cg = rep.cgImage {
                return cg
            }
        }
        return nil
    }
    if spec == "-" {
        let d = FileHandle.standardInput.readDataToEndOfFile()
        guard let rep = NSBitmapImageRep(data: d) else { return nil }
        return rep.cgImage
    }
    guard let img = NSImage(contentsOfFile: spec) else { return nil }
    var rect = NSRect(origin: .zero, size: img.size)
    return img.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

guard let cgImage = loadCGImage(target) else {
    FileHandle.standardError.write(Data("读不到图片: \(target)\n".utf8))
    exit(66)
}

// ---------------------------------------------------------------- OCR
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = langs

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write(Data("OCR 失败: \(error)\n".utf8))
    exit(70)
}

struct Item {
    let text: String
    let conf: Float
    let box: CGRect
}

let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
var items: [Item] = []
for obs in observations {
    guard let cand = obs.topCandidates(1).first else { continue }
    if cand.confidence < minConf { continue }
    let t = cand.string.trimmingCharacters(in: .whitespacesAndNewlines)
    if t.isEmpty { continue }
    items.append(Item(text: t, conf: cand.confidence, box: obs.boundingBox))
}

// ---------------------------------------------------------------- 排版还原
func isCJK(_ c: Character) -> Bool {
    guard let s = c.unicodeScalars.first else { return false }
    let v = s.value
    return (0x4E00...0x9FFF).contains(v) || (0x3400...0x4DBF).contains(v)
        || (0x3000...0x303F).contains(v) || (0xFF00...0xFFEF).contains(v)
}

func smartJoin(_ parts: [String]) -> String {
    var out = ""
    for p in parts {
        if out.isEmpty { out = p; continue }
        if let l = out.last, let f = p.first, !isCJK(l) && !isCJK(f) {
            out += " "
        }
        out += p
    }
    return out
}

// Vision 的 boundingBox 原点在左下角,y 越大越靠上
items.sort { $0.box.midY > $1.box.midY }

var lines: [[Item]] = []
for it in items {
    if let last = lines.last, let ref = last.first,
       abs(ref.box.midY - it.box.midY) < max(ref.box.height, it.box.height) * 0.55 {
        lines[lines.count - 1].append(it)
    } else {
        lines.append([it])
    }
}

let rendered: [String] = lines.map { line in
    smartJoin(line.sorted { $0.box.minX < $1.box.minX }.map { $0.text })
}

// ---------------------------------------------------------------- 输出
if asJSON {
    let payload: [[String: Any]] = items.map { it in
        [
            "text": it.text,
            "confidence": Double(it.conf),
            "x": Double(it.box.minX), "y": Double(it.box.minY),
            "w": Double(it.box.width), "h": Double(it.box.height),
        ]
    }
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .withoutEscapingSlashes]) {
        FileHandle.standardOutput.write(data)
        print("")
    }
} else {
    let text = rendered.joined(separator: "\n")
    if text.isEmpty {
        FileHandle.standardError.write(Data("没有识别到文字\n".utf8))
        exit(1)
    }
    print(text)
}
