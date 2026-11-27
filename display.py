"""Windows display metrics and DPI-aware Tk styling."""

import ctypes
import os
from ctypes import wintypes
from tkinter import PhotoImage, font, ttk

SCALE_OPTIONS = ("跟随系统", "100%", "125%", "150%", "175%", "200%", "225%", "250%")


def enable_dpi_awareness():
    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        enable = user32.SetProcessDpiAwarenessContext
        enable.argtypes = (ctypes.c_void_p,)
        enable.restype = wintypes.BOOL
        if not enable(ctypes.c_void_p(-4)):
            # The portable EXE declares the mode in its manifest. For a Python
            # host with a different manifest, set the GUI thread before Tk starts.
            set_thread = user32.SetThreadDpiAwarenessContext
            set_thread.argtypes = (ctypes.c_void_p,)
            set_thread.restype = ctypes.c_void_p
            set_thread(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("bounds", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


def indicator_layout(layout, replacement=None):
    updated = []
    for name, options in layout:
        options = options.copy()
        if "children" in options:
            options["children"] = indicator_layout(options["children"], replacement)
        if name.endswith(".indicator"):
            if replacement is not None:
                updated.append((replacement, options))
        else:
            updated.append((name, options))
    return updated


class Indicators:
    """Keep familiar checkbox and tree glyphs legible at any chosen scale."""

    def __init__(self, window, style):
        names = ("closed", "open", "leaf", "off", "on", "disabled_off", "disabled_on")
        self.images = {name: PhotoImage(master=window) for name in names}
        image = self.images
        checkbox = "Iqh.Checkbutton.indicator"
        self.tree_layout = indicator_layout(style.layout("Treeview.Item"))
        self.check_layout = indicator_layout(style.layout("TCheckbutton"), checkbox)
        style.element_create(checkbox, "image", image["off"],
                             ("disabled selected", image["disabled_on"]),
                             ("disabled", image["disabled_off"]), ("selected", image["on"]))

    def update(self, px, style):
        box = px(13)
        stroke = max(1, px(1))
        center = px(7)
        for name, image in self.images.items():
            tree = name in {"closed", "open", "leaf"}
            width = px(24 if tree else 20)
            height = px(22 if tree else 20)
            image.blank()
            image.configure(width=width, height=height)
            if name == "leaf":
                continue
            left = (width - box) // 2
            top = (height - box) // 2
            disabled = name.startswith("disabled")
            selected = name in {"on", "disabled_on"}
            edge = "#b6bcc3" if disabled else "#0078d4" if selected else "#7a8998"
            fill = "#f2f2f2" if disabled else "#ffffff"
            image.put(edge, to=(left, top, left + box, top + box))
            image.put(fill, to=(left + stroke, top + stroke, left + box - stroke,
                                top + box - stroke))
            if tree:
                mark = "#355368"
                image.put(mark, to=(left + px(3), top + center - stroke,
                                    left + box - px(3), top + center - stroke + stroke))
                if name == "closed":
                    image.put(mark, to=(left + center - stroke, top + px(3),
                                        left + center - stroke + stroke, top + box - px(3)))
            elif selected:
                mark = "#9aa3aa" if disabled else "#0067c0"
                for x1, y1, x2, y2 in ((3, 6, 5, 8), (5, 8, 10, 3)):
                    start_x, start_y, end_x, end_y = (px(n) for n in (x1, y1, x2, y2))
                    steps = max(end_x - start_x, abs(end_y - start_y), 1)
                    for step in range(steps + 1):
                        x = left + round(start_x + (end_x - start_x) * step / steps)
                        y = top + round(start_y + (end_y - start_y) * step / steps)
                        image.put(mark, to=(x, y, x + stroke, y + stroke))
        style.layout("Treeview.Item", self.tree_layout)
        style.layout("TCheckbutton", self.check_layout)


class Display:
    def __init__(self, window):
        self.window = window
        self.user32 = ctypes.WinDLL("user32") if os.name == "nt" else None
        if self.user32:
            monitor = self.user32.MonitorFromWindow
            monitor.argtypes = (wintypes.HWND, wintypes.DWORD)
            monitor.restype = wintypes.HANDLE
            info = self.user32.GetMonitorInfoW
            info.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
            info.restype = wintypes.BOOL
            self.get_dpi = getattr(self.user32, "GetDpiForWindow", None)
            if self.get_dpi:
                self.get_dpi.argtypes = (wintypes.HWND,)
                self.get_dpi.restype = wintypes.UINT
        self.style = ttk.Style(window)
        if os.name == "nt" and "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        family = font.nametofont("TkDefaultFont", root=window).actual("family")
        self.text = font.Font(window, family=family)
        self.indicators = Indicators(window, self.style)
        self.scale = 0
        self.metrics = None

    def monitor_metrics(self):
        hwnd = self.window.winfo_id()
        dpi = self.get_dpi(hwnd) if self.user32 and self.get_dpi else 0
        if not dpi:
            dpi = round(self.window.winfo_fpixels("1i"))
        if self.user32:
            handle = self.user32.MonitorFromWindow(hwnd, 2)
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if handle and self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return dpi, info.work.right - info.work.left, info.work.bottom - info.work.top
        return dpi, self.window.winfo_screenwidth(), self.window.winfo_screenheight()

    def px(self, value):
        return max(0, round(value * self.scale))

    def fit(self, width, height):
        _, available_width, available_height = self.metrics
        return min(width, max(1, available_width - self.px(35))), min(
            height, max(1, available_height - self.px(55)))

    def apply(self, preference):
        metrics = self.monitor_metrics()
        dpi, _, _ = metrics
        scale = dpi / 96 if preference == SCALE_OPTIONS[0] else int(preference[:-1]) / 100
        if metrics == self.metrics and scale == self.scale:
            return False
        self.metrics = metrics
        self.scale = scale
        self.window.tk.call("tk", "scaling", self.scale * 96 / 72)
        self.text.configure(size=-self.px(13))
        self.indicators.update(self.px, self.style)
        for name in ("TLabel", "TButton", "TCheckbutton", "TEntry", "TSpinbox", "TCombobox"):
            self.style.configure(name, font=self.text)
        self.style.configure("TButton", padding=(self.px(6), self.px(3)))
        self.style.configure("TEntry", padding=self.px(2))
        self.style.configure("TSpinbox", padding=self.px(3), arrowsize=self.px(13))
        self.style.configure("TCombobox", padding=self.px(3), arrowsize=self.px(13))
        self.style.configure("Treeview", font=self.text, rowheight=max(self.px(24), self.text.metrics("linespace") + self.px(4)),
                             indent=self.px(20), arrowsize=self.px(12))
        self.style.configure("Treeview.Heading", font=self.text, padding=(self.px(3), self.px(3)))
        self.style.configure("Vertical.TScrollbar", arrowsize=self.px(14))
        self.style.configure("Horizontal.TScrollbar", arrowsize=self.px(14))
        self.style.configure("Horizontal.TProgressbar", thickness=self.px(10))
        self.window.option_add("*TCombobox*Listbox.font", str(self.text))
        return True


class ResponsiveRow(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.left = ttk.Frame(self)
        self.right = ttk.Frame(self)
        self.columnconfigure(0, weight=1)
        self.left.grid(row=0, column=0, sticky="ew")
        self.right.grid(row=0, column=1, sticky="e")
        self.bind("<Configure>", self.reflow)

    def reflow(self, _event=None):
        available = self.winfo_width()
        if available <= 1:
            return
        stacked = self.left.winfo_reqwidth() + self.right.winfo_reqwidth() > available
        row, column, anchor = (1, 0, "w") if stacked else (0, 1, "e")
        geometry = self.right.grid_info()
        if (geometry.get("row"), geometry.get("column")) != (row, column):
            self.right.grid_configure(row=row, column=column, sticky=anchor)
