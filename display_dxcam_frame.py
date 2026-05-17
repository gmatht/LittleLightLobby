#!/usr/bin/env python3
"""
Live DXCam thumbnail (Desktop Duplication / DXGI)

Creates a draggable, borderless live thumbnail that captures the target
window using the DXCam library (which uses the Desktop Duplication API).

Requires: dxcam (pip install dxcam). Pillow is recommended for fast
resizing/display but the script will fall back to a pure-Python path.

Usage:
  winpython display_dxcam_frame.py --title "StarCraft II"

Notes:
- DXCam may require appropriate GPU/drivers and permissions. If dxcam
  is not available this script will print a message and exit.
"""

import argparse
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

# Try to import dxcam and numpy
try:
    import dxcam
except Exception as e:
    print('dxcam is required for this script (pip install dxcam):', e)
    sys.exit(1)

try:
    import numpy as np
except Exception:
    np = None

# Pillow (optional, for nicer resizing)
try:
    from PIL import Image, ImageTk
    have_pil = True
except Exception:
    Image = None
    ImageTk = None
    have_pil = False


def _ppm_from_rgb_bytes(rgb_bytes, w, h):
    header = f"P6\n{w} {h}\n255\n".encode('ascii')
    return header + rgb_bytes


def _downscale_rgb_from_array(arr, dst_w, dst_h):
    """Nearest-neighbour downscale of an HxWx3 RGB numpy array to bytes."""
    # arr expected shape (h, w, 3), dtype=uint8
    src_h, src_w = arr.shape[:2]
    xmap = [min(src_w - 1, int(x * src_w / dst_w)) for x in range(dst_w)]
    ymap = [min(src_h - 1, int(y * src_h / dst_h)) for y in range(dst_h)]
    out = bytearray(dst_w * dst_h * 3)
    di = 0
    for yy in range(dst_h):
        sy = ymap[yy]
        row = arr[sy]
        for xx in range(dst_w):
            sx = xmap[xx]
            r, g, b = row[sx]
            out[di] = r
            out[di + 1] = g
            out[di + 2] = b
            di += 3
    return bytes(out)


def run_live_dxcam(title='StarCraft II', thumb_w=320, thumb_h=200, interval_ms=150):
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

    # Create DXCam camera. Prefer RGB output if supported.
    cam = None
    use_rgb = False
    try:
        cam = dxcam.create(output_color='rgb')
        use_rgb = True
    except Exception:
        try:
            cam = dxcam.create(output_color='bgr')
            use_rgb = False
        except Exception:
            # Try default create
            cam = dxcam.create()
            use_rgb = False

    # For continuous capture some backends require start()
    try:
        cam.start()
    except Exception:
        # not fatal; grab may work without start
        pass

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

    # Close on Escape (local)
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

            # Grab current window rectangle and capture via DXCam
            left, top, right, bottom = t.get_window_rect(hwnd)
            w = right - left
            h = bottom - top
            # dxcam grab region uses (left, top, width, height)
            region = (left, top, w, h)

            frame = None
            try:
                # Some dxcam backends return numpy arrays (H, W, C)
                frame = cam.grab(region=region)
            except TypeError:
                # Older API may accept positional args
                frame = cam.grab(region)
            except Exception:
                # If grab fails, fall back to window DC capture
                raw, src_w, src_h = t.capture_window_dc(hwnd)
                # raw is BGRA -> convert
                import array as arr
                mv = memoryview(raw)
                # create numpy-like view if numpy available
                if np is not None:
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape((src_h, src_w, 4))[:, :, :3]
                    frame = frame[..., ::-1]  # BGRA -> RGB
                else:
                    # convert raw BGRA to RGB bytes and downscale below
                    frame = None

            if frame is None:
                # fallback: use GDI capture then downscale
                raw, src_w, src_h = t.capture_region(left, top, w, h)
                # raw is BGRA; convert to RGB bytes and downscale
                if have_pil and np is not None:
                    arr = np.frombuffer(raw, dtype=np.uint8).reshape((src_h, src_w, 4))
                    rgb_arr = arr[:, :, :3][:, :, ::-1]
                    img = Image.fromarray(rgb_arr, 'RGB')
                    img = img.resize((thumb_w, thumb_h), Image.BILINEAR)
                    photo = ImageTk.PhotoImage(img)
                else:
                    # manual conversion and nearest-downscale
                    if np is not None:
                        arr = np.frombuffer(raw, dtype=np.uint8).reshape((src_h, src_w, 4))
                        rgb_arr = arr[:, :, :3][:, :, ::-1]
                        rgb_bytes = _downscale_rgb_from_array(rgb_arr, thumb_w, thumb_h)
                    else:
                        # rare: no numpy and no pil -> give up
                        rgb_bytes = b'\x00' * (thumb_w * thumb_h * 3)
                    ppm = _ppm_from_rgb_bytes(rgb_bytes, thumb_w, thumb_h)
                    b64 = base64.b64encode(ppm).decode('ascii')
                    photo = tk.PhotoImage(data=b64)
            else:
                # frame is a numpy array or similar
                # Ensure RGB ordering
                try:
                    # some dxcam variants already return RGB
                    if hasattr(frame, 'shape') and frame.shape[2] == 3:
                        arr = frame
                    else:
                        arr = frame
                except Exception:
                    arr = frame

                # If PIL available, use it for resize and PhotoImage
                if have_pil:
                    try:
                        # Convert numpy array to PIL Image. If array is BGR, swap channels.
                        if np is not None and isinstance(arr, np.ndarray):
                            # Assume arr is RGB if camera was created with output_color='rgb'
                            img = Image.fromarray(arr, 'RGB')
                        else:
                            img = Image.frombytes('RGB', (arr.shape[1], arr.shape[0]), arr.tobytes())
                        img = img.resize((thumb_w, thumb_h), Image.BILINEAR)
                        photo = ImageTk.PhotoImage(img)
                    except Exception:
                        # fallback to manual downscale
                        if np is not None:
                            rgb_bytes = _downscale_rgb_from_array(arr, thumb_w, thumb_h)
                            ppm = _ppm_from_rgb_bytes(rgb_bytes, thumb_w, thumb_h)
                            b64 = base64.b64encode(ppm).decode('ascii')
                            photo = tk.PhotoImage(data=b64)
                        else:
                            photo = None
                else:
                    # No PIL: downscale with numpy fallback
                    if np is not None:
                        rgb_bytes = _downscale_rgb_from_array(arr, thumb_w, thumb_h)
                        ppm = _ppm_from_rgb_bytes(rgb_bytes, thumb_w, thumb_h)
                        b64 = base64.b64encode(ppm).decode('ascii')
                        photo = tk.PhotoImage(data=b64)
                    else:
                        photo = None

            if photo is not None:
                label.configure(image=photo)
                label.image = photo

        except Exception:
            # ignore per-frame errors
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
        try:
            cam.stop()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default='StarCraft II')
    ap.add_argument('--width', type=int, default=320)
    ap.add_argument('--height', type=int, default=200)
    ap.add_argument('--interval', type=int, default=150)
    args = ap.parse_args()
    run_live_dxcam(title=args.title, thumb_w=args.width, thumb_h=args.height, interval_ms=args.interval)


if __name__ == '__main__':
    main()
