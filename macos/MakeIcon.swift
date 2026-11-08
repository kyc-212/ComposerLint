// ComposerLint's original vector artwork. No downloaded artwork or font glyphs.
import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let transform = NSAffineTransform(); transform.scale(by: CGFloat(pixels) / 1024); transform.concat()
        let tile = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 200, yRadius: 200)
        NSGradient(starting: NSColor(srgbRed: 0.05, green: 0.12, blue: 0.22, alpha: 1),
                   ending: NSColor(srgbRed: 0.06, green: 0.35, blue: 0.39, alpha: 1))!.draw(in: tile, angle: 45)
        NSColor.white.withAlphaComponent(0.12).setStroke()
        for y in stride(from: 360, through: 680, by: 80) {
            let staff = NSBezierPath(); staff.lineWidth = 12
            staff.move(to: NSPoint(x: 190, y: y)); staff.line(to: NSPoint(x: 800, y: y)); staff.stroke()
        }
        NSColor.white.setFill()
        NSBezierPath(ovalIn: NSRect(x: 235, y: 300, width: 185, height: 140)).fill()
        NSBezierPath(ovalIn: NSRect(x: 525, y: 375, width: 185, height: 140)).fill()
        let notes = NSBezierPath()
        notes.move(to: NSPoint(x: 380, y: 365)); notes.line(to: NSPoint(x: 380, y: 735))
        notes.line(to: NSPoint(x: 670, y: 810)); notes.line(to: NSPoint(x: 670, y: 440))
        notes.lineWidth = 42; notes.lineJoinStyle = .round; NSColor.white.setStroke(); notes.stroke()
        NSColor(srgbRed: 0.43, green: 0.91, blue: 0.72, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 630, y: 170, width: 250, height: 250)).fill()
        let tick = NSBezierPath(); tick.move(to: NSPoint(x: 692, y: 295))
        tick.line(to: NSPoint(x: 738, y: 250)); tick.line(to: NSPoint(x: 817, y: 339))
        tick.lineWidth = 27; tick.lineCapStyle = .round; tick.lineJoinStyle = .round
        NSColor(srgbRed: 0.04, green: 0.19, blue: 0.23, alpha: 1).setStroke(); tick.stroke()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(
            to: destination.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
