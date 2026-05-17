# StarCraft II Live Thumbnail

This folder contains small utilities to show a live thumbnail preview of the
StarCraft II window. The fastest method uses the Windows DWM (Aero) thumbnail
API so the compositor renders a live low-CPU preview.

Quick overview
- display_aero_preview.py  — Fast DWM/Aero thumbnail (preferred). Borderless,
  draggable, always-on-top preview. Press Esc to quit. If the first positional
  argument is a plain number it is treated as the desired height and the
  script will infer the width from the target window's aspect ratio.
- display_gdi_frame.py    — GDI capture fallback (per-frame capture).
- display_printwindow_frame.py — PrintWindow fallback (may produce black
  frames for GPU-exclusive surfaces).
- display_dwm_frame.py    — alternate DWM thumbnail host implementation.
- display_dxcam_frame.py  — dxcam / Desktop Duplication fallback for
  fullscreen/exclusive swapchains.
- test_screencapture_variance.py — diagnostic script to measure capture
  brightness/variance and help choose a fallback.

Usage (fast Aero preview)
- Run on Windows (needs DWM / dwmapi):
  `python display_aero_preview.py [HEIGHT] [--title "StarCraft II"] [--width W] [--height H]`

- Examples:
  - `python display_aero_preview.py` — 320x200 default preview
  - `python display_aero_preview.py 250` — height=250, width inferred to
    preserve the StarCraft window aspect ratio

Important note about StarCraft mode
- The DWM/Aero approach requires the StarCraft window to be a regular window
  or "Windowed (Fullscreen)" mode. If StarCraft is running in exclusive
  non-windowed fullscreen mode (plain fullscreen that bypasses the compositor)
  DwmRegisterThumbnail will often fail or produce a black frame. In that
  situation use the `display_dxcam_frame.py` (desktop duplication) fallback.

Behavior
- The Aero preview is borderless, draggable (click-and-drag), always-on-top
  and listens for Esc to quit. When the target window becomes the foreground
  window the preview hides and polls less frequently; it reappears when the
  target loses foreground.

Dependencies & platform
- Windows only. Requires dwmapi.dll for the Aero thumbnail path. Optional
  packages used by some scripts: Pillow, numpy, dxcam (desktop duplication).

Saved captures
- When run, some scripts write sample captures to files named
  `sc_capture.bmp` and `sc_capture_window.bmp` in this directory for
  debugging.

If you want, I can also add usage examples, copyable command lines, or
improve troubleshooting diagnostics (HRESULT decoding for DwmRegisterThumbnail,
etc.).
