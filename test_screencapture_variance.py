"""
test_screencapture_variance.py

Standalone test utility that exercises multiple Windows screen-capture
methods and reports the observed variance (stability) of each method.

Methods tested:
- gdi: classic BitBlt from the screen DC
- printwindow: user32.PrintWindow on the target HWND
- dwm: create a small Tk host window, register a DWM thumbnail of the
  target window and capture the host window (measures what the DWM
  thumbnail appears as when captured via GDI)

Run on Windows with a visible target window. Example:
  python test_screencapture_variance.py --title StarCraft --frames 30

Notes:
- This is a diagnostic script (not a unit test). It prints results to
  stdout. It purposely avoids external dependencies.
"""

import sys
import time
import argparse
import ctypes
from ctypes import wintypes
import math

if sys.platform != 'win32':
    print('This test only runs on Windows.')
    sys.exit(0)

import tkinter as tk

# Reuse the window-finding helper from starcraft_thumbnail if available;
# otherwise define a minimal fallback.
try:
    from starcraft_thumbnail import find_window_by_title_substring
except Exception:
    user32 = ctypes.windll.user32

    def find_window_by_title_substring(substring, exclude_hwnd=None):
        substring = substring.lower()
        found = {'hwnd': None}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, lparam):
            try:
                if exclude_hwnd and hwnd == exclude_hwnd:
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if substring in title.lower():
                    found['hwnd'] = hwnd
                    return False
            except Exception:
                pass
            return True

        user32.EnumWindows(enum_proc, 0)
        return found['hwnd']


# Win32 / GDI helpers
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = None
try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    dwmapi = None
kernel32 = ctypes.windll.kernel32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER)]


SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0


def get_window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return rect.left, rect.top, rect.right, rect.bottom


def capture_region(left, top, width, height):
    """Capture a screen rectangle and return (raw_bytes, width, height).

    Returned bytes are 32bpp (B,G,R,unused) row-major top-to-bottom.
    """
    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        raise ctypes.WinError()

    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        user32.ReleaseDC(0, hdc_screen)
        raise ctypes.WinError()

    hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    if not hbitmap:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        raise ctypes.WinError()

    prev = gdi32.SelectObject(hdc_mem, hbitmap)
    # Copy from the primary screen DC
    if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, left, top, SRCCOPY):
        # cleanup
        gdi32.SelectObject(hdc_mem, prev)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        raise ctypes.WinError()

    # Prepare BITMAPINFO for a top-down 32bpp image
    bmi = BITMAPINFO()
    hdr = bmi.bmiHeader
    hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    hdr.biWidth = width
    hdr.biHeight = -height  # negative for top-down DIB
    hdr.biPlanes = 1
    hdr.biBitCount = 32
    hdr.biCompression = BI_RGB
    hdr.biSizeImage = 0

    buf_size = width * height * 4
    buffer = ctypes.create_string_buffer(buf_size)
    bits = gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS)

    # cleanup
    gdi32.SelectObject(hdc_mem, prev)
    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    if bits == 0:
        raise RuntimeError('GetDIBits failed')

    return buffer.raw, width, height


def capture_printwindow(hwnd):
    # Capture using PrintWindow
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        raise ctypes.WinError()

    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    if not hdc_mem:
        user32.ReleaseDC(hwnd, hdc_window)
        raise ctypes.WinError()

    hbitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    if not hbitmap:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
        raise ctypes.WinError()

    prev = gdi32.SelectObject(hdc_mem, hbitmap)

    # Try PrintWindow; some applications (fullscreen D3D) will fail or produce black
    PW_RENDERFULLCONTENT = 0x00000002
    try:
        res = user32.PrintWindow(hwnd, hdc_mem, 0)
    except Exception:
        res = 0

    # Read the bitmap out (even if PrintWindow failed, we may still have something)
    try:
        bmi = BITMAPINFO()
        hdr = bmi.bmiHeader
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth = width
        hdr.biHeight = -height
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB

        buf_size = width * height * 4
        buffer = ctypes.create_string_buffer(buf_size)
        bits = gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS)
        if bits == 0:
            raise RuntimeError('GetDIBits failed after PrintWindow')
        return buffer.raw, width, height
    finally:
        gdi32.SelectObject(hdc_mem, prev)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


def capture_window_dc(hwnd):
    """Capture a window by using GetWindowDC and BitBlt from that DC.

    This tries to read the window's own device context rather than the
    screen DC. It can succeed where screen BitBlt doesn't when the window
    draws directly into its window DC, but will still fail for many
    GPU-exclusive fullscreen surfaces.
    """
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        raise ctypes.WinError()

    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    if not hdc_mem:
        user32.ReleaseDC(hwnd, hdc_window)
        raise ctypes.WinError()

    hbitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    if not hbitmap:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
        raise ctypes.WinError()

    prev = gdi32.SelectObject(hdc_mem, hbitmap)

    if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY):
        gdi32.SelectObject(hdc_mem, prev)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
        raise ctypes.WinError()

    # Read the bitmap out
    bmi = BITMAPINFO()
    hdr = bmi.bmiHeader
    hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    hdr.biWidth = width
    hdr.biHeight = -height
    hdr.biPlanes = 1
    hdr.biBitCount = 32
    hdr.biCompression = BI_RGB

    buf_size = width * height * 4
    buffer = ctypes.create_string_buffer(buf_size)
    bits = gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS)

    # cleanup
    gdi32.SelectObject(hdc_mem, prev)
    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    if bits == 0:
        raise RuntimeError('GetDIBits failed')

    return buffer.raw, width, height


def capture_dwm_thumbnail(hwnd, thumb_w=320, thumb_h=200, visible=True, wait=0.05):
    """Create a small Tk host window, register a DWM thumbnail of hwnd,
    let it render, then capture the host client area.
    """
    if not dwmapi:
        raise RuntimeError('dwmapi not available on this system')

    root = tk.Tk()
    # Make the host small and topmost so it is visible
    root.overrideredirect(False)
    root.geometry(f"{thumb_w}x{thumb_h}+50+50")
    root.attributes('-topmost', True)
    # Ensure the window is realized
    root.update_idletasks()
    root.update()

    host_hwnd = wintypes.HWND(root.winfo_id())

    hthumb = wintypes.HANDLE()
    res = dwmapi.DwmRegisterThumbnail(host_hwnd, hwnd, ctypes.byref(hthumb))
    if res != 0 or not hthumb.value:
        root.destroy()
        raise RuntimeError(f'DwmRegisterThumbnail failed (HRESULT={res})')

    # Set the destination to the full client area (client coords)
    class RECT(ctypes.Structure):
        _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                    ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

    class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
        _fields_ = [
            ("dwFlags", wintypes.DWORD),
            ("rcDestination", RECT),
            ("rcSource", RECT),
            ("opacity", wintypes.BYTE),
            ("fVisible", wintypes.BOOL),
            ("fSourceClientAreaOnly", wintypes.BOOL),
        ]

    DWM_TNP_RECTDESTINATION = 0x00000001
    DWM_TNP_VISIBLE = 0x00000008
    DWM_TNP_OPACITY = 0x00000004

    props = DWM_THUMBNAIL_PROPERTIES()
    props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY
    props.rcDestination = RECT(0, 0, thumb_w, thumb_h)
    props.opacity = 255
    props.fVisible = True
    props.fSourceClientAreaOnly = False

    res = dwmapi.DwmUpdateThumbnailProperties(hthumb, ctypes.byref(props))
    if res != 0:
        try:
            dwmapi.DwmUnregisterThumbnail(hthumb)
        except Exception:
            pass
        root.destroy()
        raise RuntimeError(f'DwmUpdateThumbnailProperties failed (HRESULT={res})')

    # Let the compositor paint
    root.update_idletasks()
    root.update()
    time.sleep(wait)

    # Capture the client area of the host window via screen BitBlt
    sx = root.winfo_rootx()
    sy = root.winfo_rooty()
    sw = root.winfo_width()
    sh = root.winfo_height()

    try:
        raw, w, h = capture_region(sx, sy, sw, sh)
    finally:
        try:
            dwmapi.DwmUnregisterThumbnail(hthumb)
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    return raw, w, h


def mean_brightness_from_raw(raw, width, height):
    # raw is bytes in B,G,R,? order
    total = 0.0
    count = width * height
    mv = memoryview(raw)
    # sum brightness per pixel
    s = 0.0
    for i in range(0, len(raw), 4):
        b = mv[i]
        g = mv[i + 1]
        r = mv[i + 2]
        s += (0.299 * r + 0.587 * g + 0.114 * b)
    return s / count


def pixel_stats_from_raw(raw, width, height, black_threshold=5.0):
    """Compute statistics (mean, variance, std, min, max, black count) over
    pixel luminance for a single captured frame.
    """
    mv = memoryview(raw)
    n = width * height
    if n == 0:
        return {
            'mean': float('nan'),
            'variance': float('nan'),
            'std': float('nan'),
            'min': float('nan'),
            'max': float('nan'),
            'black_count': 0,
            'pixels': 0,
        }

    # Welford's online algorithm for numerical stability
    mean = 0.0
    M2 = 0.0
    minv = float('inf')
    maxv = float('-inf')
    black_count = 0

    idx = 0
    for i in range(n):
        b = mv[idx]
        g = mv[idx + 1]
        r = mv[idx + 2]
        idx += 4
        x = 0.299 * r + 0.587 * g + 0.114 * b
        if x < black_threshold:
            black_count += 1
        if x < minv:
            minv = x
        if x > maxv:
            maxv = x
        count = i + 1
        delta = x - mean
        mean += delta / count
        M2 += delta * (x - mean)

    variance = M2 / n
    std = math.sqrt(variance)
    return {
        'mean': mean,
        'variance': variance,
        'std': std,
        'min': minv,
        'max': maxv,
        'black_count': black_count,
        'pixels': n,
    }


def run_test(hwnd, methods, frames=20, delay=0.1, thumb_w=320, thumb_h=200, pixel_variance=False):
    left, top, right, bottom = get_window_rect(hwnd)
    win_w = right - left
    win_h = bottom - top

    results = {}
    for method in methods:
        print(f'Running method: {method} (frames={1 if pixel_variance else frames})')
        if pixel_variance:
            # Capture exactly one frame and compute pixel variance
            try:
                if method == 'gdi':
                    raw, w, h = capture_region(left, top, win_w, win_h)
                elif method == 'printwindow':
                    raw, w, h = capture_printwindow(hwnd)
                elif method == 'dwm':
                    raw, w, h = capture_dwm_thumbnail(hwnd, thumb_w=thumb_w, thumb_h=thumb_h)
                else:
                    raise ValueError('unknown method ' + method)

                stats = pixel_stats_from_raw(raw, w, h)
                results[method] = {'pixel_stats': stats, 'error': None}
            except Exception as e:
                print(f'  capture error:', e)
                results[method] = {'pixel_stats': None, 'error': str(e)}
        else:
            samples = []
            black_frames = 0
            for i in range(frames):
                try:
                    if method == 'gdi':
                        raw, w, h = capture_region(left, top, win_w, win_h)
                    elif method == 'printwindow':
                        raw, w, h = capture_printwindow(hwnd)
                    elif method == 'dwm':
                        raw, w, h = capture_dwm_thumbnail(hwnd, thumb_w=thumb_w, thumb_h=thumb_h)
                    else:
                        raise ValueError('unknown method ' + method)

                    mb = mean_brightness_from_raw(raw, w, h)
                    samples.append(mb)
                    # Consider a frame black if mean brightness is very low
                    if mb < 5.0:
                        black_frames += 1
                except Exception as e:
                    print(f'  capture error on frame {i}:', e)
                    # append a NaN to indicate failure
                    samples.append(float('nan'))

                time.sleep(delay)

            # Filter out NaN samples
            valid = [v for v in samples if not (isinstance(v, float) and math.isnan(v))]
            if valid:
                mean = sum(valid) / len(valid)
                var = sum((x - mean) ** 2 for x in valid) / len(valid)
                std = math.sqrt(var)
            else:
                mean = float('nan')
                var = float('nan')
                std = float('nan')

            results[method] = {
                'samples': samples,
                'mean_brightness': mean,
                'variance': var,
                'stddev': std,
                'black_frames': black_frames,
                'frames': frames,
            }

    return results


def format_hresult(hr):
    """Return a textual description for an HRESULT if available."""
    try:
        FORMAT_MESSAGE_FROM_SYSTEM = 0x00001000
        FORMAT_MESSAGE_IGNORE_INSERTS = 0x00000200
        buf = ctypes.create_unicode_buffer(2048)
        res = kernel32.FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                                      None, ctypes.c_uint(hr & 0xFFFFFFFF), 0, buf, ctypes.sizeof(buf), None)
        if res:
            return buf.value.strip()
    except Exception:
        pass
    return f'0x{hr & 0xFFFFFFFF:08X}'


def print_window_diagnostics(hwnd):
    """Print helpful diagnostics about the target window and DWM state."""
    print('\nWindow diagnostics:')
    # Title
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    print(f'  title: "{title}"')

    # PID
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    print(f'  hwnd: 0x{hwnd:08X}, thread id: {tid}, pid: {pid.value}')

    # Try to get process image name
    proc_name = '<unknown>'
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if hproc:
            buf_len = wintypes.DWORD(1024)
            buf2 = ctypes.create_unicode_buffer(1024)
            qfn = getattr(kernel32, 'QueryFullProcessImageNameW', None)
            if qfn:
                if qfn(hproc, 0, buf2, ctypes.byref(buf_len)):
                    proc_name = buf2.value
                else:
                    proc_name = f'<QueryFullProcessImageNameW failed: {ctypes.GetLastError()}>'
            kernel32.CloseHandle(hproc)
    except Exception as e:
        proc_name = f'<error: {e}>'
    print(f'  process image: {proc_name}')

    # Visibility / iconic
    try:
        is_iconic = bool(user32.IsIconic(hwnd))
        is_visible = bool(user32.IsWindowVisible(hwnd))
        print(f'  IsIconic (minimized): {is_iconic}, IsWindowVisible: {is_visible}')
    except Exception:
        pass

    # Rect
    try:
        l, t, r, b = get_window_rect(hwnd)
        print(f'  rect: left={l}, top={t}, right={r}, bottom={b}, size=({r-l}x{b-t})')
    except Exception:
        pass

    # Styles
    try:
        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        try:
            GetWindowLongPtr = user32.GetWindowLongPtrW
        except AttributeError:
            GetWindowLongPtr = user32.GetWindowLongW
        GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
        GetWindowLongPtr.restype = ctypes.c_longlong
        style = int(GetWindowLongPtr(hwnd, GWL_STYLE) & 0xFFFFFFFF)
        exstyle = int(GetWindowLongPtr(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF)
        print(f'  style: 0x{style:08X}, exstyle: 0x{exstyle:08X}')
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOREDIRECTIONBITMAP = 0x00200000
        if exstyle & WS_EX_LAYERED:
            print('    WS_EX_LAYERED set')
        if exstyle & WS_EX_NOREDIRECTIONBITMAP:
            print('    WS_EX_NOREDIRECTIONBITMAP set (prevents DWM redirection)')
    except Exception:
        pass

    # DWM composition
    if dwmapi:
        try:
            enabled = wintypes.BOOL()
            res = dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
            if res == 0:
                print(f'  DWM composition enabled: {bool(enabled.value)}')
            else:
                print(f'  DwmIsCompositionEnabled HRESULT: {format_hresult(res)}')
        except Exception as e:
            print('  DwmIsCompositionEnabled error:', e)
    else:
        print('  dwmapi not available')

    # Quick PrintWindow test
    try:
        raw, w, h = capture_printwindow(hwnd)
        mb = mean_brightness_from_raw(raw, w, h)
        print(f'  PrintWindow mean brightness: {mb:.3f} (0==black)')
    except Exception as e:
        print('  PrintWindow test failed:', e)



def print_results(results):
    print('\nCapture variance results:')
    for method, r in results.items():
        print(f"\nMethod: {method}")
        print(f"  frames: {r['frames']}")
        print(f"  black frames: {r['black_frames']}")
        print(f"  mean brightness: {r['mean_brightness']:.3f}")
        print(f"  variance: {r['variance']:.6f}")
        print(f"  stddev: {r['stddev']:.3f}")


def main():
    ap = argparse.ArgumentParser(description='Screen capture variance test')
    ap.add_argument('--title', default='StarCraft', help='Window title substring to find')
    ap.add_argument('--frames', type=int, default=20, help='Frames per method')
    ap.add_argument('--delay', type=float, default=0.1, help='Seconds between frames')
    ap.add_argument('--methods', default='gdi,printwindow,dwm', help='Comma-separated methods to test (gdi,printwindow,dwm)')
    ap.add_argument('--thumb-w', type=int, default=320, help='Thumbnail width for DWM method')
    ap.add_argument('--thumb-h', type=int, default=200, help='Thumbnail height for DWM method')
    args = ap.parse_args()

    hwnd = find_window_by_title_substring(args.title)
    if not hwnd:
        print(f'Could not find a window with title containing "{args.title}"')
        sys.exit(2)

    methods = [m.strip() for m in args.methods.split(',') if m.strip()]

    print(f'Found window HWND=0x{hwnd:08X}')
    results = run_test(hwnd, methods, frames=args.frames, delay=args.delay, thumb_w=args.thumb_w, thumb_h=args.thumb_h)
    print_results(results)


if __name__ == '__main__':
    main()
