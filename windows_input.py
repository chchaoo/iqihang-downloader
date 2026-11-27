"""Prefer an English keyboard while a Windows password field has focus."""

import ctypes
import os
from ctypes import wintypes


def english_password(entry):
    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32")
    imm32 = ctypes.WinDLL("imm32")
    signatures = [
        (user32.LoadKeyboardLayoutW, wintypes.HANDLE, (wintypes.LPCWSTR, wintypes.UINT)),
        (user32.ActivateKeyboardLayout, wintypes.HANDLE, (wintypes.HANDLE, wintypes.UINT)),
        (user32.GetKeyboardLayout, wintypes.HANDLE, (wintypes.DWORD,)),
        (user32.GetFocus, wintypes.HWND, ()),
        (imm32.ImmAssociateContext, wintypes.HANDLE, (wintypes.HWND, wintypes.HANDLE)),
    ]
    for function, result, args in signatures:
        function.restype, function.argtypes = result, args

    active = None

    def switch():
        nonlocal active
        if active is not None or entry.focus_get() != entry:
            return
        hwnd = user32.GetFocus() or entry.winfo_id()
        previous = user32.GetKeyboardLayout(0)
        context = imm32.ImmAssociateContext(hwnd, None)
        active = (hwnd, previous, context)
        english = user32.LoadKeyboardLayoutW("00000409", 0)
        if english:
            user32.ActivateKeyboardLayout(english, 0)

    def restore(_event):
        nonlocal active
        if active is None:
            return
        hwnd, previous, context = active
        active = None
        imm32.ImmAssociateContext(hwnd, context)
        if previous:
            user32.ActivateKeyboardLayout(previous, 0)

    entry.bind("<FocusIn>", lambda _event: entry.after_idle(switch), add="+")
    entry.bind("<FocusOut>", restore, add="+")
    entry.bind("<Destroy>", restore, add="+")
