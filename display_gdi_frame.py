#!/usr/bin/env python3
"""
Capture one GDI frame of a target window and save it as a BMP file, then
open it with the system default image viewer (Windows: os.startfile).

Usage:
  winpython display_gdi_frame.py --title "StarCraft II" --out sc_capture.bmp

This script re-uses the capture helpers from test_screencapture_variance.py.
"""

import argparse
import os
import struct
import sys

if sys.platform != 'win32':
    print('This script only runs on Windows.')
    sys.exit(1)

import test_screencapture_variance as t


def write_bmp_topdown(filename, raw_bytes, width, height):
    """Write a 32bpp top-down BMP file from raw BGRA bytes.

    raw_bytes must be exactly width*height*4 bytes arranged top-to-bottom
    and each pixel B,G,R,A.
    """
    biSize = 40
    biWidth = width
    biHeight = -height  # negative for top-down
    biPlanes = 1
    biBitCount = 32
    biCompression = 0  # BI_RGB
    biSizeImage = width * height * 4
    biXPelsPerMeter = 0
    biYPelsPerMeter = 0
    biClrUsed = 0
    biClrImportant = 0

    bfOffBits = 14 + biSize
    bfSize = bfOffBits + biSizeImage

    # BITMAPFILEHEADER
    bfType = b'BM'
    bfReserved1 = 0
    bfReserved2 = 0

    with open(filename, 'wb') as f:
        f.write(struct.pack('<2sIHHI', bfType, bfSize, bfReserved1, bfReserved2, bfOffBits))
        # BITMAPINFOHEADER
        f.write(struct.pack('<IiiHHIIiiII', biSize, biWidth, biHeight, biPlanes, biBitCount,
                            biCompression, biSizeImage, biXPelsPerMeter, biYPelsPerMeter,
                            biClrUsed, biClrImportant))
        f.write(raw_bytes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default='StarCraft II', help='window title substring')
    ap.add_argument('--out', default='gdi_capture.bmp', help='output BMP filename')
    args = ap.parse_args()

    hwnd = t.find_window_by_title_substring(args.title)
    if not hwnd:
        print(f'Could not find a window with title containing "{args.title}"')
        sys.exit(2)

    left, top, right, bottom = t.get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    print(f'Capturing window HWND=0x{hwnd:08X} rect=({left},{top}) {width}x{height}')
    # Try to capture from the window DC first (better for window content).
    try:
        raw, w, h = t.capture_window_dc(hwnd)
    except Exception:
        print('capture_window_dc failed, falling back to screen capture')
        raw, w, h = t.capture_region(left, top, width, height)

    stats = t.pixel_stats_from_raw(raw, w, h)
    print('Pixel stats:', stats)

    write_bmp_topdown(args.out, raw, w, h)
    print('Wrote', os.path.abspath(args.out))

    # Try to open with default viewer on Windows
    try:
        os.startfile(os.path.abspath(args.out))
    except Exception:
        print('Could not open image automatically. Please open the file manually.')


if __name__ == '__main__':
    main()
