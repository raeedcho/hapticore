"""X11 utilities for PsychoPy window management.

Used by DisplayProcess and WorkspaceMirrorProcess to restore keyboard
focus after PsychoPy/pyglet steals it via XSetInputFocus.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def restore_pointer_focus() -> None:
    """Restore X11 keyboard focus to follow the mouse pointer.

    After creating a PsychoPy window on a WM-less Zaphod screen, pyglet
    calls XSetInputFocus on the new window, which moves keyboard focus
    away from the control-room screen. Without a window manager on the
    rig screen, nothing returns focus when the operator interacts with
    the control room. Setting focus to PointerRoot causes keyboard
    input to follow the mouse pointer across X screens.

    No-op on non-Linux platforms or when libX11 / a display connection
    is unavailable (e.g. headless CI).
    """
    if sys.platform != "linux":
        return
    import ctypes
    import ctypes.util

    try:
        x11_path = ctypes.util.find_library("X11")
        if not x11_path:
            return
        x11 = ctypes.cdll.LoadLibrary(x11_path)

        # Declare C signatures — without these, ctypes assumes c_int for
        # all args/returns, truncating 64-bit pointers on x86_64.
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XSetInputFocus.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_long,    # focus (Window / PointerRoot)
            ctypes.c_int,     # revert_to
            ctypes.c_ulong,   # time
        ]
        x11.XSetInputFocus.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int

        display = x11.XOpenDisplay(None)
        if not display:
            return
        POINTER_ROOT = 1
        REVERT_TO_POINTER_ROOT = 1
        CURRENT_TIME = 0
        try:
            x11.XSetInputFocus(
                display, POINTER_ROOT, REVERT_TO_POINTER_ROOT, CURRENT_TIME,
            )
            x11.XFlush(display)
        finally:
            x11.XCloseDisplay(display)
    except (OSError, AttributeError):
        logger.debug("Could not restore X11 keyboard focus to PointerRoot")
