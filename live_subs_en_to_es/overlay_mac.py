# overlay_mac.py
import threading
from typing import Optional

from Cocoa import (
    NSApp, NSApplication, NSBackingStoreBuffered, NSBorderlessWindowMask,
    NSMakeRect, NSWindow, NSColor, NSFont, NSScreen, NSPanel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSFloatingWindowLevel,
)
from Cocoa import NSObject
from Quartz import CALayer, CATextLayer, kCAAlignmentCenter
from Foundation import NSNumber, NSAutoreleasePool

# Helper: choose a good default frame at bottom center
def _default_frame(width=1000, height=140, bottom_margin=80):
    screen = NSScreen.mainScreen().frame()
    x = (screen.size.width - width) / 2.0
    y = bottom_margin
    return NSMakeRect(x, y, width, height)

class OverlayController(NSObject):
    def init(self):
        self = super().init()
        if self is None:
            return None
        self.window: Optional[NSPanel] = None
        self.root_layer: Optional[CALayer] = None
        self.text_layer: Optional[CATextLayer] = None
        return self

    def setupWindow(self):
        frame = _default_frame()

        # Non-activating panel for overlay usage
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSBorderlessWindowMask,
            NSBackingStoreBuffered,
            False,
        )

        # Visuals
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(False)

        # Always on top; float above most windows
        win.setLevel_(NSFloatingWindowLevel + 1)

        # Across all Spaces and in full-screen
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        # Click-through
        win.setIgnoresMouseEvents_(True)

        # Layer-backed
        content = win.contentView()
        content.setWantsLayer_(True)

        # Root layer: rounded translucent pill
        root = CALayer.layer()
        root.setCornerRadius_(22.0)
        # Slight translucent dark background
        root.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.1, 0.85).CGColor())
        # Soft shadow for readability
        root.setShadowOpacity_(0.6)
        root.setShadowRadius_(16.0)
        root.setShadowOffset_((0.0, 4.0))
        root.setMasksToBounds_(False)

        # Text layer
        txt = CATextLayer.layer()
        txt.setAlignmentMode_(kCAAlignmentCenter)
        txt.setWrapped_(True)
        txt.setTruncationMode_("end")
        txt.setForegroundColor_(NSColor.whiteColor().CGColor())
        # Use SF Pro Rounded if available; fallback to system
        font = NSFont.fontWithName_size_("SF Pro Rounded Bold", 40.0) or NSFont.systemFontOfSize_(40.0)
        # Bridge NSFont to CoreText via fontName
        txt.setFont_(font.fontName())
        txt.setFontSize_(font.pointSize())

        # Insets inside the pill
        pad_x, pad_y = 24.0, 18.0
        w = frame.size.width - pad_x * 2
        h = frame.size.height - pad_y * 2
        txt.setFrame_(((pad_x, pad_y), (w, h)))

        root.addSublayer_(txt)
        content.setLayer_(root)

        self.window = win
        self.root_layer = root
        self.text_layer = txt

        win.orderFrontRegardless()

    def setText_(self, text: str):
        if self.text_layer is not None:
            # Update on main thread—AppKit is main-thread only
            def _apply():
                self.text_layer.setString_(text)
            self.performSelectorOnMainThread_withObject_waitUntilDone_("applyText:", text, False)

    # Expose selector to run on main
    def applyText_(self, text):
        if self.text_layer is not None:
            self.text_layer.setString_(text)

class OverlayApp:
    """
    Runs a tiny NSApplication with a click-through, rounded overlay window.
    Call .start() from a non-main thread; it spins the AppKit runloop.
    Use .set_text(...) from any thread.
    """
    def __init__(self):
        self.app = None
        self.controller = None
        self._thread = None

    def _run(self):
        pool = NSAutoreleasePool.alloc().init()
        self.app = NSApplication.sharedApplication()
        self.controller = OverlayController.alloc().init()
        self.controller.setupWindow()
        self.app.run()  # run loop blocks on this thread
        del pool

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._thread = t

    def set_text(self, text: str):
        if self.controller:
            self.controller.applyText_(text)

    def stop(self):
        if self.app:
            self.app.terminate_(None)
