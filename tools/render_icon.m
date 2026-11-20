#import <Cocoa/Cocoa.h>
// Render our own vector artwork with AppKit. No desktop or screen capture.
int main(int argc,const char **argv){@autoreleasepool{
    if(argc!=4)return 2;
    NSImage *image=[[NSImage alloc] initWithContentsOfFile:@(argv[1])];
    NSInteger size=atoi(argv[3]);if(!image || size<16 || size>1024)return 3;
    NSBitmapImageRep *bitmap=[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:size pixelsHigh:size
        bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
    [NSGraphicsContext saveGraphicsState];NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
    [image drawInRect:NSMakeRect(0,0,size,size) fromRect:NSZeroRect operation:NSCompositingOperationCopy fraction:1];
    [NSGraphicsContext restoreGraphicsState];
    NSData *png=[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    return [png writeToFile:@(argv[2]) atomically:YES]?0:4;
}}
