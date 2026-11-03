// Apple Vision OCR: prints JSON {"lines":[{"text","x","y","w","h","conf"}]} for one image (macOS only).
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1,
      let img = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("cannot read image\n".utf8)); exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
if #available(macOS 13.0, *) { req.automaticallyDetectsLanguage = true; req.recognitionLanguages = ["fa-IR", "en-US"] }
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
var lines: [[String: Any]] = []
for o in (req.results ?? []) {
    guard let c = o.topCandidates(1).first else { continue }
    let b = o.boundingBox
    lines.append(["text": c.string, "x": b.minX, "y": 1 - b.maxY, "w": b.width, "h": b.height, "conf": c.confidence])
}
let data = try JSONSerialization.data(withJSONObject: ["lines": lines], options: [])
print(String(data: data, encoding: .utf8)!)
