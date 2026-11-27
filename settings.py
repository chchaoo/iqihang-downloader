"""Portable application settings with Windows user-bound credentials."""

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile

PROGRAM_DIRECTORY = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                     else Path(__file__).resolve().parent)
STATE_PATH = PROGRAM_DIRECTORY / "iqihang-settings.json"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, decrypt=False):
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(DataBlob)]
    operation.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = DataBlob(len(data), buffer), DataBlob()
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        local_free(outgoing.data)


def protect_credentials(account, password):
    plaintext = json.dumps([account, password], ensure_ascii=False).encode("utf-8")
    return base64.b64encode(dpapi(plaintext)).decode("ascii")


def restore_credentials(ciphertext):
    if not isinstance(ciphertext, str) or not ciphertext:
        return None
    try:
        sealed = base64.b64decode(ciphertext, validate=True)
        value = json.loads(dpapi(sealed, decrypt=True).decode("utf-8"))
        if isinstance(value, list) and len(value) == 2 and all(
                isinstance(entry, str) and entry for entry in value):
            return value
    except (OSError, ValueError):
        pass
    return None


def read_settings(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(path, settings):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=path.name + ".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(settings, file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
