#!/usr/bin/env python3
"""Draw termgrid's app icon: Terminal's own icon split into a 2x2 grid of terminals.

Usage: make-icon.py OUT.icns      (or OUT.png for a 1024-pixel preview)

The body, rim and shadow are Terminal's icon as macOS draws it, so the result
keeps Terminal's look.  Its ">_" prompt is lifted off and redrawn smaller in
each pane.
"""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager

import AppKit

TERMINAL = "/System/Applications/Utilities/Terminal.app"

# Measured on Terminal's 1024-pixel icon, as (x, y, width, height) from the top left.
BODY = (100, 100, 823, 823)       # the rounded square
PROMPT = (246, 260, 368, 278)     # ">_" with its antialiasing and shadow
CLEAR_X = 700                     # a column of plain background beside the prompt

RIM = 14                          # the body's lit edge, which the dividers stop short of

PROMPT_SCALE = 0.45
DIVIDER = 10                      # width of the lines between panes
DIVIDER_COLOR = (0.30, 0.30, 0.30)


def rect(x, y, w, h, scale=1):
    """A top-left-origin rectangle in 1024-pixel units, as a Cocoa (bottom-left origin) rect."""
    return ((x * scale, (1024 - y - h) * scale), (w * scale, h * scale))


def canvas(w, h):
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, w, h, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    return rep.bitmapImageRepByRetaggingWithColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())


def image(rep):
    img = AppKit.NSImage.alloc().initWithSize_((rep.pixelsWide(), rep.pixelsHigh()))
    img.addRepresentation_(rep)
    return img


@contextmanager
def drawing_into(rep):
    AppKit.NSGraphicsContext.saveGraphicsState()
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    ctx.setImageInterpolation_(AppKit.NSImageInterpolationHigh)
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    try:
        yield
    finally:
        ctx.flushGraphics()
        AppKit.NSGraphicsContext.restoreGraphicsState()


def draw(img, dst, src=AppKit.NSZeroRect, op=AppKit.NSCompositingOperationSourceOver):
    img.drawInRect_fromRect_operation_fraction_(dst, src, op, 1.0)


def terminal_icon(size):
    rep = canvas(size, size)
    with drawing_into(rep):
        draw(AppKit.NSWorkspace.sharedWorkspace().iconForFile_(TERMINAL), ((0, 0), (size, size)))
    return image(rep)


def lift_prompt(terminal):
    """Terminal's ">_" on black, ready to be added onto any background."""
    x, y, w, h = PROMPT
    rep = canvas(w, h)
    with drawing_into(rep):
        draw(terminal, ((0, 0), (w, h)), rect(x, y, w, h), AppKit.NSCompositingOperationCopy)
        # Taking away the background beside it leaves just the glyphs.
        draw(terminal, ((0, 0), (w, h)), rect(CLEAR_X, y, 1, h), AppKit.NSCompositingOperationDifference)
    return image(rep)


def panes():
    """The four panes of the body, as (x, y, w, h) in 1024-pixel units."""
    bx, by, bw, bh = BODY
    half_w, half_h = bw / 2, bh / 2
    return [(bx + c * half_w, by + r * half_h, half_w, half_h) for r in (0, 1) for c in (0, 1)]


def render(size, prompt):
    k = size / 1024
    terminal = terminal_icon(size)
    rep = canvas(size, size)
    with drawing_into(rep):
        draw(terminal, ((0, 0), (size, size)), AppKit.NSZeroRect, AppKit.NSCompositingOperationCopy)

        # Paint over the big prompt with the background beside it, row by row.
        x, y, w, h = PROMPT
        (_, sy), (_, sh) = rect(x, y, w, h, k)
        draw(terminal, rect(x, y, w, h, k), ((CLEAR_X * k, sy), (1, sh)), AppKit.NSCompositingOperationCopy)

        # Lines between the panes, inside the rim.
        bx, by, bw, bh = BODY
        line = max(1, DIVIDER * k)
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*DIVIDER_COLOR, 1).set()
        cx, cy = (bx + bw / 2) * k, (1024 - by - bh / 2) * k
        AppKit.NSRectFill(((cx - line / 2, (1024 - by - bh + RIM) * k), (line, (bh - 2 * RIM) * k)))
        AppKit.NSRectFill((((bx + RIM) * k, cy - line / 2), ((bw - 2 * RIM) * k, line)))

        # A small prompt centred in each pane.
        pw, ph = PROMPT[2] * PROMPT_SCALE, PROMPT[3] * PROMPT_SCALE
        for px, py, pane_w, pane_h in panes():
            dst = rect(px + (pane_w - pw) / 2, py + (pane_h - ph) / 2, pw, ph, k)
            draw(prompt, dst, AppKit.NSZeroRect, AppKit.NSCompositingOperationPlusLighter)
    return rep


def save_png(rep, path):
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    if not data.writeToFile_atomically_(path, True):
        sys.exit(f"make-icon: cannot write {path}")


def main():
    if len(sys.argv) != 2 or not sys.argv[1].endswith((".icns", ".png")):
        sys.exit("usage: make-icon.py OUT.icns|OUT.png")
    out = sys.argv[1]
    prompt = lift_prompt(terminal_icon(1024))
    if out.endswith(".png"):
        save_png(render(1024, prompt), out)
        return
    with tempfile.TemporaryDirectory() as work:
        iconset = os.path.join(work, "termgrid.iconset")
        os.mkdir(iconset)
        for points in (16, 32, 128, 256, 512):
            for factor, suffix in ((1, ""), (2, "@2x")):
                save_png(render(points * factor, prompt),
                         os.path.join(iconset, f"icon_{points}x{points}{suffix}.png"))
        subprocess.run(["iconutil", "-c", "icns", "-o", out, iconset], check=True)


if __name__ == "__main__":
    main()
