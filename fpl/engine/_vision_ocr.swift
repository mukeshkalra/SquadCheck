// SquadCheck — Apple Vision text recognition helper
// Reads an image file, runs VNRecognizeTextRequest, and prints JSON to stdout.
// Each element: {"text": str, "conf": float, "x": float, "y": float, "w": float, "h": float}
// Coordinates are normalised [0,1]; origin is BOTTOM-LEFT (Vision convention).
// Built by scanner.py on first use:  swiftc _vision_ocr.swift -o _vision_ocr

import Vision
import Foundation
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else {
    fputs("usage: _vision_ocr <image_path>\n", stderr)
    exit(1)
}

guard let nsImage = NSImage(contentsOfFile: args[1]),
      let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil)
else {
    fputs("error: could not load \(args[1])\n", stderr)
    exit(2)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel      = .accurate
request.usesLanguageCorrection = false   // raw text preferred for name matching

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do { try handler.perform([request]) } catch {
    fputs("error: \(error)\n", stderr); exit(3)
}

var out: [[String: Any]] = []
for obs in request.results ?? [] {
    guard let top = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox
    out.append([
        "text": top.string,
        "conf": Double(top.confidence),
        "x":    Double(b.origin.x),
        "y":    Double(b.origin.y),
        "w":    Double(b.size.width),
        "h":    Double(b.size.height),
    ])
}

let json = try! JSONSerialization.data(withJSONObject: out)
print(String(data: json, encoding: .utf8)!)
