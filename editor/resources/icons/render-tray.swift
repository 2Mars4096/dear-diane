// Run: swift render-tray.swift <icon-directory>
import AppKit
let destination = CommandLine.arguments[1]
for scale in [1, 2] {
 let rep = NSBitmapImageRep(bitmapDataPlanes:nil, pixelsWide:18*scale, pixelsHigh:18*scale, bitsPerSample:8, samplesPerPixel:4, hasAlpha:true, isPlanar:false, colorSpaceName:.deviceRGB, bytesPerRow:0, bitsPerPixel:0)!
 NSGraphicsContext.saveGraphicsState()
 NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep:rep)
 (AffineTransform(scale:CGFloat(scale)) as NSAffineTransform).concat()
 NSColor.black.setFill()
 func rounded(_ x:CGFloat,_ y:CGFloat,_ w:CGFloat,_ h:CGFloat,_ r:CGFloat) {
  NSBezierPath(roundedRect:NSRect(x:x,y:y,width:w,height:h),xRadius:r,yRadius:r).fill()
 }
 rounded(4,1.5,9,11.5,2); rounded(5,12,2,4.5,1); rounded(14,7.5,1.5,3,0.6)
 NSGraphicsContext.current!.compositingOperation = .clear
 for y:CGFloat in [6.5,8.5,10.5] { rounded(5.75,y,5.5,1,0.5) }
 NSBezierPath(ovalIn:NSRect(x:7.2,y:2.7,width:2.6,height:2.6)).fill()
 NSGraphicsContext.restoreGraphicsState()
 let suffix=scale == 1 ? "" : "@2x"
 try rep.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:destination+"/trayTemplate"+suffix+".png"))
}
