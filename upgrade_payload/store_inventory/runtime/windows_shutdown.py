"""Receive confirmed Windows session shutdown from the server window."""
from contextlib import contextmanager
import os


@contextmanager
def subscribe_shutdown(root, on_end):
    if os.name != "nt":
        yield
        return

    import ctypes
    from ctypes import wintypes

    address = ctypes.c_size_t
    signed_address = ctypes.c_ssize_t
    callback_type = ctypes.WINFUNCTYPE(
        signed_address, wintypes.HWND, wintypes.UINT,
        address, signed_address, address, address,
    )
    controls = ctypes.WinDLL("comctl32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    controls.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, address, address]
    controls.SetWindowSubclass.restype = wintypes.BOOL
    controls.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, address]
    controls.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, address, signed_address]
    controls.DefSubclassProc.restype = signed_address

    @callback_type
    def dispatch(hwnd, message, wparam, lparam, subclass_id, _data):
        if message == 0x0011:  # WM_QUERYENDSESSION
            return 1
        if message == 0x0016 and wparam:  # WM_ENDSESSION
            try:
                on_end()
            except Exception:
                import logging
                logging.exception("Windows session shutdown cleanup failed")
        if message == 0x0082:  # WM_NCDESTROY
            controls.RemoveWindowSubclass(hwnd, dispatch, subclass_id)
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    hwnd = user.GetAncestor(root.winfo_id(), 2)
    installed = bool(hwnd and controls.SetWindowSubclass(hwnd, dispatch, 1, 0))
    if not installed:
        import logging
        logging.warning("Windows session shutdown hook could not be installed")
    try:
        yield
    finally:
        if installed:
            controls.RemoveWindowSubclass(hwnd, dispatch, 1)
