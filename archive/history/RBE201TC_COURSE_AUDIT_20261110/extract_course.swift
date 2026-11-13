import Foundation
import PDFKit

let base = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let destination = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
let inputs = [
    "lecture/RBE201TC_Lecture_20260907_stu.pdf",
    "lecture/RBE201TC_Lecture_20260922_1.pdf",
    "RBE201TC_RDK_Robot_Manual_AY2627.pdf",
    "RBE201TC_RDK_Robot_Manual_AY2627 (1).pdf",
    "RBE201TC provided hardware manual.pdf",
    "RBE201TC_MATLAB_Exercises_Basics.pdf"
]
var index = [[String: Any]]()
for relative in inputs {
    let source = base.appendingPathComponent(relative)
    guard let pdf = PDFDocument(url: source) else { fatalError("Unreadable PDF: \(source.path)") }
    var extracted = "Original PDF: \(source.path)\nPhysical PDF pages: \(pdf.pageCount)\n"
    var characters = 0
    for i in 0..<pdf.pageCount {
        let text = pdf.page(at: i)?.string ?? ""
        characters += text.count
        extracted += "\n===== PDF PAGE \(i + 1) / \(pdf.pageCount) =====\n\(text)\n"
    }
    let filename = source.deletingPathExtension().lastPathComponent + ".txt"
    try Data(extracted.utf8).write(to: destination.appendingPathComponent(filename), options: .withoutOverwriting)
    index.append(["source": source.path, "pages": pdf.pageCount, "text_characters": characters, "extracted_filename": filename])
}
let metadata = try JSONSerialization.data(withJSONObject: index, options: [.prettyPrinted, .sortedKeys])
try metadata.write(to: destination.appendingPathComponent("pdf_index.json"), options: .withoutOverwriting)
print(String(decoding: metadata, as: UTF8.self))
