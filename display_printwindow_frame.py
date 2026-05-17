#!/usr/bin/env python3
"""
Live PrintWindow thumbnail

Creates a draggable, borderless 320x200 live thumbnail using PrintWindow
as the capture method. Press Escape to quit (global if possible).

Usage:
  winpython display_printwindow_frame.py --title "StarCraft II"
"""

import argparse
import os
import sys
import time
import base64
import ctypes
from ctypes import wintypes

if sys.platform != 'win32':
    print('This script only runs on Windows.')
    sys.exit(1)

import tkinter as tk
import test_screencapture_variance as t


def _downscale_bgra_to_rgb(raw, src_w, src_h, dst_w, dst_h):
    src = memoryview(raw)
    dst = bytearray(dst_w * dst_h * 3)
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


def capture_scaled_printwindow(hwnd, dst_w=320, dst_h=200):
    """Capture with PrintWindow (fallbacks to window DC and screen).

    Returns raw RGB bytes (no alpha) sized dst_w x dst_h.
    """
    try:
        raw, src_w, src_h = t.capture_printwindow(hwnd)
    except Exception:
        # fallback to window dc
        try:
            raw, src_w, src_h = t.capture_window_dc(hwnd)
        except Exception:
            left, top, right, bottom = t.get_window_rect(hwnd)
            raw, src_w, src_h = t.capture_region(left, top, right - left, bottom - top)

    if src_w == dst_w and src_h == dst_h:
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


def run_live_printwindow(title='StarCraft II', thumb_w=320, thumb_h=200, interval_ms=150):
    hwnd = t.find_window_by_title_substring(title)
    if not hwnd:
        print(f'Could not find a window with title containing "{title}"')
        sys.exit(2)

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
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.geometry(f"{thumb_w}x{thumb_h}+100+100")
    label = tk.Label(root, bd=0)
    label.pack(fill='both', expand=True)

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

    def on_escape_local(event=None):
        try:
            root.destroy()
        except Exception:
            pass

    root.bind('<Escape>', on_escape_local)

    # global ESC hook
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
                    try:
                        root.event_generate('<<GlobalEscape>>', when='tail')
                    except Exception:
                        pass
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

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

    have_pil = False
    ImageTk = None
    try:
        from PIL import Image, ImageTk as _ImageTk
        have_pil = True
        ImageTk = _ImageTk
        PIL_Image = Image
    except Exception:
        have_pil = False

    photo = None
    idle = False
    if is_target_foreground():
        idle = True
        root.withdraw()

    def update_frame():
        nonlocal photo, idle
        try:
            if is_target_foreground():
                if not idle:
                    try:
                        root.withdraw()
                    except Exception:
                        pass
                    idle = True
                root.after(1000, update_frame)
                return

            if idle:
                try:
                    root.deiconify()
                    root.lift()
                except Exception:
                    pass
                idle = False

            rgb, w, h = capture_scaled_printwindow(hwnd, dst_w=thumb_w, dst_h=thumb_h)
            if have_pil:
                img = PIL_Image.frombytes('RGB', (w, h), rgb)
                photo = ImageTk.PhotoImage(img)
            else:
                ppm = _ppm_from_rgb_bytes(rgb, w, h)
                b64 = base64.b64encode(ppm).decode('ascii')
                photo = tk.PhotoImage(data=b64)

            label.configure(image=photo)
            label.image = photo
        except Exception:
            pass
        try:
            root.after(interval_ms, update_frame)
        except Exception:
            pass

    root.after(0, update_frame)
    try:
        root.mainloop()
    finally:
        try:
            if _hook_id:
                user32.UnhookWindowsHookEx(_hook_id)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default='StarCraft II')
    ap.add_argument('--width', type=int, default=320)
    ap.add_argument('--height', type=int, default=200)
    ap.add_argument('--interval', type=int, default=150)
    args = ap.parse_args()
    run_live_printwindow(title=args.title, thumb_w=args.width, thumb_h=args.height, interval_ms=args.interval)


if __name__ == '__main__':
    main()
