#!/usr/bin/env python3
"""
starcraft_thumbnail.py

Create a DWM live thumbnail (320x200) of the first top-level window
tkinter window.

Usage:
  winpython starcraft_thumbnail.py

Notes:
- Windows only (DWM required: Vista+). Tested conceptually; may need
  tweaks for non-standard DPI settings.
"""

import sys
import ctypes
from ctypes import wintypes
import time
import tkinter as tk


if sys.platform != "win32":
    print("This script only runs on Windows.")
    sys.exit(1)


# Try to make the process DPI aware so coordinates are consistent.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    # Not critical; continue anyway.
    pass


user32 = ctypes.windll.user32
try:
    dwmapi = ctypes.windll.dwmapi
except Exception:
    print("dwmapi.dll not available. DWM thumbnails require Windows DWM.")
    sys.exit(1)


# Useful Win32 types and structures
class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", RECT),
        ("rcSource", RECT),
        ("opacity", wintypes.BYTE),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


# DWM flags
DWM_TNP_RECTDESTINATION = 0x00000001
DWM_TNP_RECTSOURCE = 0x00000002
DWM_TNP_OPACITY = 0x00000004
DWM_TNP_VISIBLE = 0x00000008
DWM_TNP_SOURCECLIENTAREAONLY = 0x00000010


# Prototype adjustments (use simple restypes/argtypes to help ctypes)
dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND,
                                        ctypes.POINTER(wintypes.HANDLE)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long

dwmapi.DwmUnregisterThumbnail.argtypes = [wintypes.HANDLE]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long

dwmapi.DwmUpdateThumbnailProperties.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long

dwmapi.DwmQueryThumbnailSourceSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(SIZE)]
dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long


def find_window_by_title_substring(substring, exclude_hwnd=None):
    """Return the HWND of the first top-level visible window whose title
    contains substring (case-insensitive). Optionally exclude a window by
    handle (e.g. the host window).
    """
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
                return False  # stop enumeration
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc, 0)
    return found['hwnd']


def screen_to_client(hwnd, x, y):
    pt = POINT(x, y)
    if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
        raise ctypes.WinError()
    return pt.x, pt.y


class StarcraftThumbnailApp:
    def __init__(self, root, target_substr='starcraft', thumb_w=320, thumb_h=200,
                 poll_ms=300):
        self.root = root
        self.target_substr = target_substr
        self.thumb_w = thumb_w
        self.thumb_h = thumb_h
        self.poll_ms = poll_ms

        root.title('StarCraft Thumbnail')
        root.resizable(False, False)

        # Small border so it's obvious where the thumbnail is
        self.canvas = tk.Canvas(root, width=thumb_w, height=thumb_h, bg='black',
                                highlightthickness=2, highlightbackground='gray')
        self.canvas.pack(padx=6, pady=6)

        # Ensure the window is realized so winfo_id() returns a real HWND
        root.update_idletasks()
        root.update()

        self.host_hwnd = wintypes.HWND(root.winfo_id())
        self.src_hwnd = None
        self.hthumb = wintypes.HANDLE(0)
        self.src_size = None

        # Bind configure so we update quickly on resize/move
        self.root.bind('<Configure>', lambda e: self._schedule_update())

        # Start polling to find the StarCraft window and maintain the thumbnail
        self._scheduled = False
        self._closed = False
        self._start_polling()

        root.protocol('WM_DELETE_WINDOW', self.close)

    def _start_polling(self):
        self._scheduled = True
        self.root.after(self.poll_ms, self._poll)

    def _schedule_update(self):
        # Debounce frequent configure events
        if not self._scheduled:
            self._start_polling()

    def _poll(self):
        self._scheduled = False
        if self._closed:
            return

        # If we don't have a source HWND, try to find it
        if not self.src_hwnd or not user32.IsWindow(self.src_hwnd):
            self._unregister_thumbnail()
            src = find_window_by_title_substring(self.target_substr, exclude_hwnd=self.host_hwnd)
            if src:
                print('Found StarCraft window: HWND=0x{:08X}'.format(src))
                self._register_thumbnail(src)
            else:
                # keep polling until found
                self.root.after(self.poll_ms, self._poll)
                return

        # Update thumbnail destination rectangle (keeps aspect)
        try:
            self._update_thumbnail_rect()
        except Exception as e:
            # If something goes wrong, unregister and try again later
            print('Thumbnail update error:', e)
            self._unregister_thumbnail()

        # Continue polling to react to source/host moves
        self.root.after(self.poll_ms, self._poll)

    def _register_thumbnail(self, src_hwnd):
        # Register thumbnail with DWM
        hthumb = wintypes.HANDLE()
        res = dwmapi.DwmRegisterThumbnail(self.host_hwnd, src_hwnd, ctypes.byref(hthumb))
        if res != 0 or not hthumb.value:
            print('DwmRegisterThumbnail failed (HRESULT={})'.format(res))
            return

        self.src_hwnd = src_hwnd
        self.hthumb = hthumb

        # Query source size for aspect preserving scaling
        size = SIZE()
        res = dwmapi.DwmQueryThumbnailSourceSize(self.hthumb, ctypes.byref(size))
        if res == 0:
            self.src_size = (size.cx, size.cy)
            # print('Source size:', self.src_size)
        else:
            self.src_size = None

        # Do an immediate update
        try:
            self._update_thumbnail_rect()
        except Exception as e:
            print('Initial thumbnail update failed:', e)

    def _unregister_thumbnail(self):
        if getattr(self, 'hthumb', None) and self.hthumb and self.hthumb.value:
            try:
                dwmapi.DwmUnregisterThumbnail(self.hthumb)
            except Exception:
                pass
        self.hthumb = wintypes.HANDLE(0)
        self.src_hwnd = None
        self.src_size = None

    def _update_thumbnail_rect(self):
        if not getattr(self, 'hthumb', None) or not self.hthumb.value:
            return

        # Determine canvas position in screen coordinates
        self.root.update_idletasks()
        cx = self.canvas.winfo_rootx()
        cy = self.canvas.winfo_rooty()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        # Convert screen coords to client coords of the host window
        client_x, client_y = screen_to_client(self.host_hwnd, cx, cy)

        # Compute destination rectangle (preserve aspect ratio of source)
        if self.src_size and self.src_size[0] > 0 and self.src_size[1] > 0:
            src_w, src_h = self.src_size
            scale = min(self.thumb_w / src_w, self.thumb_h / src_h)
            new_w = max(1, int(src_w * scale))
            new_h = max(1, int(src_h * scale))
            offset_x = (self.thumb_w - new_w) // 2
            offset_y = (self.thumb_h - new_h) // 2
            left = client_x + offset_x
            top = client_y + offset_y
            right = left + new_w
            bottom = top + new_h
        else:
            left = client_x
            top = client_y
            right = client_x + cw
            bottom = client_y + ch

        props = DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY
        props.rcDestination = RECT(left, top, right, bottom)
        # Display fully opaque
        props.opacity = 255
        props.fVisible = True
        props.fSourceClientAreaOnly = False

        res = dwmapi.DwmUpdateThumbnailProperties(self.hthumb, ctypes.byref(props))
        if res != 0:
            raise RuntimeError('DwmUpdateThumbnailProperties failed (HRESULT={})'.format(res))

    def close(self):
        self._closed = True
        self._unregister_thumbnail()
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    root = tk.Tk()
    app = StarcraftThumbnailApp(root)
    print('Looking for StarCraft window (title contains "StarCraft")...')
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.close()


if __name__ == '__main__':
    main()
