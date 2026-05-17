#!/usr/bin/env python3
"""
display_aero_preview.py

Create a lightweight native Win32 window and ask DWM (Aero) to render a
live thumbnail of the target window. This uses DwmRegisterThumbnail so the
compositor provides the live preview (very low CPU compared to per-frame
GDI captures).

Behavior:
- Borderless topmost 320x200 window
- Drag by left-click (uses WM_NCLBUTTONDOWN/HTCAPTION trick)
- Press Escape to quit (global keyboard hook if available)
- If the target window is foreground, the thumbnail hides and the script
  polls once per second until it is no longer foreground (then shows again).

Limitations:
- DWM thumbnails won't work for some fullscreen/exclusive GPU surfaces.
  If DwmRegisterThumbnail fails for the target window, the script exits
  with an error message.
"""

import sys
import ctypes
from ctypes import wintypes
import time
import argparse

if sys.platform != 'win32':
    print('This script only runs on Windows.')
    sys.exit(1)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    dwmapi = None

# LRESULT: pointer-sized signed integer used by Win32 APIs. Use a local alias
# rather than relying on ctypes.wintypes having it defined in all Python builds.
if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_long):
    LRESULT = ctypes.c_long
else:
    LRESULT = ctypes.c_longlong


# Useful Win32 constants
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_OVERLAPPED = 0x00000000
SW_SHOW = 5
SW_HIDE = 0
HTCAPTION = 2
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_SIZE = 0x0005
WM_TIMER = 0x0113
WM_CLOSE = 0x0010
WM_LBUTTONDOWN = 0x0201
WM_KEYDOWN = 0x0100
WM_NCLBUTTONDOWN = 0x00A1
WM_PAINT = 0x000F
WM_QUIT = 0x0012

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
# SetWindowPos flags
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040

# DWM flags and structs
class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [('cx', wintypes.LONG), ('cy', wintypes.LONG)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ('dwFlags', wintypes.DWORD),
        ('rcDestination', RECT),
        ('rcSource', RECT),
        ('opacity', wintypes.BYTE),
        ('fVisible', wintypes.BOOL),
        ('fSourceClientAreaOnly', wintypes.BOOL),
    ]


DWM_TNP_RECTDESTINATION = 0x00000001
DWM_TNP_RECTSOURCE = 0x00000002
DWM_TNP_OPACITY = 0x00000004
DWM_TNP_VISIBLE = 0x00000008
DWM_TNP_SOURCECLIENTAREAONLY = 0x00000010


# Prototypes
user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wintypes.HWND, wintypes.UINT]

user32.GetForegroundWindow.restype = wintypes.HWND

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# Additional user32 prototypes used later
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

# DWM API prototypes (if available)
if dwmapi:
    # HRESULT DwmIsCompositionEnabled(BOOL* pfEnabled)
    try:
        dwmapi.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
        dwmapi.DwmIsCompositionEnabled.restype = ctypes.c_long
    except Exception:
        pass
    try:
        # HRESULT DwmRegisterThumbnail(HWND dst, HWND src, PHTHUMBNAIL phThumb)
        dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.HANDLE)]
        dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
        # HRESULT DwmUnregisterThumbnail(HTHUMBNAIL hThumb)
        dwmapi.DwmUnregisterThumbnail.argtypes = [wintypes.HANDLE]
        dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long
        # HRESULT DwmQueryThumbnailSourceSize(HTHUMBNAIL hThumb, PSIZE pSize)
        dwmapi.DwmQueryThumbnailSourceSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(SIZE)]
        dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long
        # HRESULT DwmUpdateThumbnailProperties(HTHUMBNAIL hThumb, DWM_THUMBNAIL_PROPERTIES* ptnp)
        dwmapi.DwmUpdateThumbnailProperties.argtypes = [wintypes.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
        dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long
    except Exception:
        pass

# Some Python builds don't expose all HWND-related types; provide
# safe fallbacks for HICON/HCURSOR/HBRUSH used by WNDCLASSEX.
HICON = getattr(wintypes, 'HICON', wintypes.HANDLE)
HCURSOR = getattr(wintypes, 'HCURSOR', wintypes.HANDLE)
HBRUSH = getattr(wintypes, 'HBRUSH', wintypes.HANDLE)


def find_window_by_title_substring(substring):
    substring = substring.lower()
    found = {'hwnd': None}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, lparam):
        try:
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


def is_foreground_for_pid(pid):
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    fg_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
    return fg_pid.value == pid


# Module-level callback types and state for the WndProc and keyboard hook.
WNDPROCTYPE = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# Shared mutable state used by the callbacks. main() will populate the
# hwnd_target and polling intervals before creating the window.
STATE = {
    'hthumb': wintypes.HANDLE(0),
    'thumb_size': (0, 0),
    'idle': False,
    'active_interval': 150,
    'idle_interval': 1000,
    'hwnd_target': None,
}


def update_thumbnail_props(hwnd, client_w, client_h):
    """Update the registered DWM thumbnail properties to fit the client rect."""
    if not STATE.get('hthumb') or not STATE['hthumb'].value:
        return
    src_size = STATE.get('thumb_size')
    if src_size and src_size[0] > 0 and src_size[1] > 0:
        src_w, src_h = src_size
        scale = min(client_w / src_w, client_h / src_h)
        new_w = max(1, int(src_w * scale))
        new_h = max(1, int(src_h * scale))
        offset_x = (client_w - new_w) // 2
        offset_y = (client_h - new_h) // 2
        left = offset_x
        top = offset_y
        right = left + new_w
        bottom = top + new_h
    else:
        left, top, right, bottom = 0, 0, client_w, client_h

    props = DWM_THUMBNAIL_PROPERTIES()
    props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY
    props.rcDestination = RECT(left, top, right, bottom)
    props.opacity = 255
    props.fVisible = True
    props.fSourceClientAreaOnly = False
    try:
        dwmapi.DwmUpdateThumbnailProperties(STATE['hthumb'], ctypes.byref(props))
    except Exception:
        pass


@WNDPROCTYPE
def WndProc(hwnd, msg, wParam, lParam):
    # Keep this function at module scope (not nested) so ctypes callbacks
    # don't lose access to globals.
    if msg == WM_CREATE:
        hthumb = wintypes.HANDLE()
        res = dwmapi.DwmRegisterThumbnail(hwnd, STATE['hwnd_target'], ctypes.byref(hthumb))
        if res != 0 or not hthumb.value:
            print('DwmRegisterThumbnail failed (HRESULT={})'.format(res))
            user32.PostQuitMessage(1)
            return 0
        STATE['hthumb'] = hthumb

        size = SIZE()
        res = dwmapi.DwmQueryThumbnailSourceSize(STATE['hthumb'], ctypes.byref(size))
        if res == 0:
            STATE['thumb_size'] = (size.cx, size.cy)

        client = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client))
        update_thumbnail_props(hwnd, client.right - client.left, client.bottom - client.top)

        user32.SetTimer(hwnd, 1, STATE['active_interval'], None)
        # Ensure the window is topmost once created
        try:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        except Exception:
            pass
        return 0

    elif msg == WM_SIZE:
        client = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client))
        w = client.right - client.left
        h = client.bottom - client.top
        update_thumbnail_props(hwnd, w, h)
        return 0

    elif msg == WM_LBUTTONDOWN:
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return 0

    elif msg == WM_TIMER:
        try:
            fg = user32.GetForegroundWindow()
            in_foreground = False
            if fg:
                if fg == STATE['hwnd_target']:
                    in_foreground = True
                else:
                    fg_pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
                    in_foreground = (fg_pid.value == STATE.get('target_pid'))

            if in_foreground:
                if not STATE['idle']:
                    user32.ShowWindow(hwnd, SW_HIDE)
                    STATE['idle'] = True
                    user32.KillTimer(hwnd, 1)
                    user32.SetTimer(hwnd, 1, STATE['idle_interval'], None)
            else:
                if STATE['idle']:
                    user32.ShowWindow(hwnd, SW_SHOW)
                    # Restore topmost after showing
                    try:
                        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                    except Exception:
                        pass
                    STATE['idle'] = False
                    user32.KillTimer(hwnd, 1)
                    user32.SetTimer(hwnd, 1, STATE['active_interval'], None)
        except Exception:
            pass
        return 0

    elif msg == WM_DESTROY:
        try:
            if STATE.get('hthumb') and STATE['hthumb'].value:
                dwmapi.DwmUnregisterThumbnail(STATE['hthumb'])
        except Exception:
            pass
        user32.PostQuitMessage(0)
        return 0

    elif msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        return 0

    elif msg == WM_KEYDOWN:
        if wParam == 0x1B:  # VK_ESCAPE
            user32.DestroyWindow(hwnd)
            return 0

    return user32.DefWindowProcW(hwnd, msg, wParam, lParam)


@HOOKPROC
def keyboard_hook(nCode, wParam, lParam):
    try:
        if nCode == 0 and wParam == WM_KEYDOWN:
            kbd = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_uint64)).contents
            vk = kbd.value & 0xFFFFFFFF
            if vk == 0x1B:  # ESC
                # Post close to the window message queue
                # We don't know the hwnd here; post quit instead
                user32.PostQuitMessage(0)
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lParam)



def main():
    # Support a numeric first positional argument: treat it as height and
    # remove it from argv so argparse only sees option flags.
    height_override = None
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        try:
            height_override = int(sys.argv[1])
            # remove the numeric arg so argparse doesn't treat it as an unknown
            del sys.argv[1]
        except Exception:
            height_override = None

    ap = argparse.ArgumentParser()
    ap.add_argument('--title', default='StarCraft II')
    ap.add_argument('--width', type=int, default=320)
    ap.add_argument('--height', type=int, default=200)
    ap.add_argument('--interval', type=int, default=150, help='active update/poll interval in ms')
    ap.add_argument('--idle-interval', type=int, default=1000, help='idle poll interval when target is foreground')
    args = ap.parse_args()

    if not dwmapi:
        print('dwmapi.dll not available. DWM thumbnailing is not supported here.')
        sys.exit(1)

    hwnd_target = find_window_by_title_substring(args.title)
    if not hwnd_target:
        print(f'Could not find window with title containing "{args.title}"')
        sys.exit(2)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd_target, ctypes.byref(pid))
    target_pid = pid.value

    # If the user passed a standalone numeric first argument, treat it as
    # height and infer width to preserve the target window's aspect ratio.
    if height_override is not None:
        # Query the target window rect to get its aspect ratio
        rect = RECT()
        try:
            if user32.GetWindowRect(hwnd_target, ctypes.byref(rect)):
                src_w = rect.right - rect.left
                src_h = rect.bottom - rect.top
                if src_h > 0 and src_w > 0:
                    aspect = float(src_w) / float(src_h)
                    args.height = int(height_override)
                    args.width = max(1, int(round(args.height * aspect)))
                    print(f'Inferred width {args.width} from height {args.height} to preserve aspect {aspect:.3f}')
        except Exception:
            # If anything fails, just use the provided height and default width
            args.height = int(height_override)

    # Window class
    # Reuse the module-scope WNDPROCTYPE to ensure the callback type matches
    # the WndProc declared earlier and avoid mismatched prototype issues.
    class WNDCLASSEX(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.UINT),
            ('style', wintypes.UINT),
            ('lpfnWndProc', WNDPROCTYPE),
            ('cbClsExtra', ctypes.c_int),
            ('cbWndExtra', ctypes.c_int),
            ('hInstance', wintypes.HINSTANCE),
            ('hIcon', HICON),
            ('hCursor', HCURSOR),
            ('hbrBackground', HBRUSH),
            ('lpszMenuName', wintypes.LPCWSTR),
            ('lpszClassName', wintypes.LPCWSTR),
            ('hIconSm', HICON),
        ]

    hInstance = kernel32.GetModuleHandleW(None)

    # Configure the module-level STATE used by the callbacks
    STATE['active_interval'] = args.interval
    STATE['idle_interval'] = args.idle_interval
    STATE['hwnd_target'] = hwnd_target
    STATE['target_pid'] = target_pid
    STATE['idle'] = False

    # Register a window class
    wndclass = WNDCLASSEX()
    wndclass.cbSize = ctypes.sizeof(WNDCLASSEX)
    wndclass.style = 0
    wndclass.lpfnWndProc = WndProc
    wndclass.cbClsExtra = 0
    wndclass.cbWndExtra = 0
    wndclass.hInstance = hInstance
    wndclass.hIcon = None
    wndclass.hCursor = None
    wndclass.hbrBackground = None
    wndclass.lpszMenuName = None
    class_name = 'AeroThumbClass'
    wndclass.lpszClassName = class_name
    wndclass.hIconSm = None

    if not user32.RegisterClassExW(ctypes.byref(wndclass)):
        err = kernel32.GetLastError()
        print('RegisterClassEx failed', err)
        sys.exit(1)

    # Create window
    width = args.width
    height = args.height
    x = 100
    y = 100
    # Use WS_POPUP | WS_VISIBLE to create a borderless visible window. We'll
    # make it topmost below using SetWindowPos.
    hwnd = user32.CreateWindowExW(0, class_name, 'AeroPreview', WS_POPUP | WS_VISIBLE,
                                   x, y, width, height, None, None, hInstance, None)
    if not hwnd:
        print('CreateWindowEx failed')
        sys.exit(1)

    # Make sure window is topmost and set requested size/position
    # Use HWND_TOPMOST and SWP_SHOWWINDOW so the window becomes topmost.
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW)

    # Show and update
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow = user32.UpdateWindow
    user32.UpdateWindow(hwnd)

    # Start foreground timer by sending WM_CREATE manually
    user32.SendMessageW(hwnd, WM_CREATE, 0, 0)

    # Global Esc hook (low-level keyboard) to ensure Esc quits even when not focused
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    @HOOKPROC
    def keyboard_hook(nCode, wParam, lParam):
        try:
            if nCode == 0 and wParam == WM_KEYDOWN:
                kbd = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_uint64)).contents
                vk = kbd.value & 0xFFFFFFFF
                if vk == 0x1B:  # ESC
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    hook_handle = None
    try:
        hmod = kernel32.GetModuleHandleW(None)
        hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hmod, 0)
    except Exception:
        hook_handle = None

    # Standard message loop
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    # Cleanup hook
    try:
        if hook_handle:
            user32.UnhookWindowsHookEx(hook_handle)
    except Exception:
        pass


if __name__ == '__main__':
    main()
