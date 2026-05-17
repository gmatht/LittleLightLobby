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
import time
import base64
import ctypes
from ctypes import wintypes

if sys.platform != 'win32':
    print('This script only runs on Windows.')
    sys.exit(1)

import test_screencapture_variance as t
import tkinter as tk


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


def _downscale_bgra_to_rgb(raw, src_w, src_h, dst_w, dst_h):
    """Nearest-neighbour downscale from BGRA raw to RGB bytes.

    Returns bytes length dst_w*dst_h*3.
    """
    src = memoryview(raw)
    dst = bytearray(dst_w * dst_h * 3)

    # Precompute mappings to avoid float ops in inner loop
    xmap = [min(src_w - 1, int(x * src_w / dst_w)) for x in range(dst_w)]
    ymap = [min(src_h - 1, int(y * src_h / dst_h)) for y in range(dst_h)]

    di = 0
    for yy in range(dst_h):
        sy = ymap[yy]
        row_start = sy * src_w * 4
        for xx in range(dst_w):
            sx = xmap[xx]
            si = row_start + sx * 4
            b = src[si]
            g = src[si + 1]
            r = src[si + 2]
            dst[di] = r
            dst[di + 1] = g
            dst[di + 2] = b
            di += 3

    return bytes(dst)


def _ppm_from_rgb_bytes(rgb_bytes, w, h):
    header = f"P6\n{w} {h}\n255\n".encode('ascii')
    return header + rgb_bytes


def capture_scaled_frame(hwnd, dst_w=320, dst_h=200):
    """Capture the window content (window DC) and downscale to dst_w x dst_h.

    Returns (rgb_bytes, w, h) where rgb_bytes are raw RGB bytes (no alpha).
    """
    # Try fast path: capture window DC full-size and downscale in Python
    try:
        raw, src_w, src_h = t.capture_window_dc(hwnd)
    except Exception:
        # Fall back to screen region capture
        left, top, right, bottom = t.get_window_rect(hwnd)
        raw, src_w, src_h = t.capture_region(left, top, right - left, bottom - top)

    if src_w == dst_w and src_h == dst_h:
        # Convert BGRA -> RGB
        mv = memoryview(raw)
        rgb = bytearray(dst_w * dst_h * 3)
        ri = 0
        for i in range(0, len(raw), 4):
            rgb[ri] = mv[i + 2]
            rgb[ri + 1] = mv[i + 1]
            rgb[ri + 2] = mv[i]
            ri += 3
        return bytes(rgb), dst_w, dst_h

    rgb = _downscale_bgra_to_rgb(raw, src_w, src_h, dst_w, dst_h)
    return rgb, dst_w, dst_h


def run_live_thumbnail(title='StarCraft II', thumb_w=320, thumb_h=200, interval_ms=150):
    hwnd = t.find_window_by_title_substring(title)
    if not hwnd:
        print(f'Could not find a window with title containing "{title}"')
        sys.exit(2)

    # Prepare foreground-check helpers
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    target_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
    target_pid_val = target_pid.value

    def is_target_foreground():
        try:
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            if fg == hwnd:
                return True
            fg_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
            return fg_pid.value == target_pid_val
        except Exception:
            return False

    root = tk.Tk()
    root.overrideredirect(True)  # borderless
    root.attributes('-topmost', True)
    root.geometry(f"{thumb_w}x{thumb_h}+100+100")

    # Make a label to display the image
    label = tk.Label(root, bd=0)
    label.pack(fill='both', expand=True)

    # Drag support
    drag = {'x': 0, 'y': 0}

    def on_press(event):
        drag['x'] = event.x
        drag['y'] = event.y

    def on_drag(event):
        x = event.x_root - drag['x']
        y = event.y_root - drag['y']
        root.geometry(f'+{x}+{y}')

    label.bind('<Button-1>', on_press)
    label.bind('<B1-Motion>', on_drag)

    # Close on Escape (local binding)
    def on_escape_local(event=None):
        try:
            root.destroy()
        except Exception:
            pass

    root.bind('<Escape>', on_escape_local)

    # Try to install a global low-level keyboard hook so Esc quits even when
    # the thumbnail is not focused.
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    VK_ESCAPE = 0x1B

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [('vkCode', wintypes.DWORD),
                    ('scanCode', wintypes.DWORD),
                    ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD),
                    ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

    @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    def _low_level_keyboard_proc(nCode, wParam, lParam):
        try:
            if nCode == 0 and wParam == WM_KEYDOWN:
                k = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if k.vkCode == VK_ESCAPE:
                    # Post a quit to the Tk mainloop
                    try:
                        root.event_generate('<<GlobalEscape>>', when='tail')
                    except Exception:
                        pass
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # Keep references so callback isn't GC'd
    _hook_proc = _low_level_keyboard_proc
    _hook_id = None
    try:
        hmod = kernel32.GetModuleHandleW(None)
        _hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_proc, hmod, 0)
    except Exception:
        _hook_id = None

    def on_global_escape(event=None):
        on_escape_local()

    root.bind('<<GlobalEscape>>', on_global_escape)

    # Try to use Pillow for faster conversion if available
    have_pil = False
    ImageTk = None
    try:
        from PIL import Image, ImageTk as _ImageTk
        have_pil = True
        ImageTk = _ImageTk
        PIL_Image = Image
    except Exception:
        have_pil = False

    # Main update loop
    photo = None
    idle = False

    # Start hidden if StarCraft is foreground
    if is_target_foreground():
        idle = True
        root.withdraw()

    def update_frame():
        nonlocal photo, idle
        try:
            if is_target_foreground():
                # If the target is foreground, hide and poll slowly
                if not idle:
                    try:
                        root.withdraw()
                    except Exception:
                        pass
                    idle = True
                root.after(1000, update_frame)
                return

            # Target not foreground
            if idle:
                # restore the thumbnail
                try:
                    root.deiconify()
                    root.lift()
                except Exception:
                    pass
                idle = False

            rgb, w, h = capture_scaled_frame(hwnd, dst_w=thumb_w, dst_h=thumb_h)
            if have_pil:
                # Create PIL image from raw RGB bytes
                img = PIL_Image.frombytes('RGB', (w, h), rgb)
                photo = ImageTk.PhotoImage(img)
            else:
                ppm = _ppm_from_rgb_bytes(rgb, w, h)
                b64 = base64.b64encode(ppm).decode('ascii')
                photo = tk.PhotoImage(data=b64)

            label.configure(image=photo)
            label.image = photo
        except Exception:
            # Ignore frame errors; continue
            pass
        try:
            root.after(interval_ms, update_frame)
        except Exception:
            pass

    # Start the loop
    root.after(0, update_frame)

    try:
        root.mainloop()
    finally:
        # Unhook if we set a hook
        try:
            if _hook_id:
                user32.UnhookWindowsHookEx(_hook_id)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default='StarCraft II', help='window title substring')
    ap.add_argument('--width', type=int, default=320, help='thumbnail width')
    ap.add_argument('--height', type=int, default=200, help='thumbnail height')
    ap.add_argument('--interval', type=int, default=150, help='update interval in ms')
    ap.add_argument('--out', help='(optional) save one frame to BMP and exit')
    args = ap.parse_args()

    if args.out:
        hwnd = t.find_window_by_title_substring(args.title)
        if not hwnd:
            print(f'Could not find a window with title containing "{args.title}"')
            sys.exit(2)
        left, top, right, bottom = t.get_window_rect(hwnd)
        width = right - left
        height = bottom - top
        try:
            raw, w, h = t.capture_window_dc(hwnd)
        except Exception:
            raw, w, h = t.capture_region(left, top, width, height)
        write_bmp_topdown(args.out, raw, w, h)
        print('Wrote', os.path.abspath(args.out))
        try:
            os.startfile(os.path.abspath(args.out))
        except Exception:
            pass
        return

    run_live_thumbnail(title=args.title, thumb_w=args.width, thumb_h=args.height, interval_ms=args.interval)


if __name__ == '__main__':
    main()
