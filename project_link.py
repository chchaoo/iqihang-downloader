"""Public project link shared by the desktop UI and release documentation."""

from pathlib import Path
from tkinter import PhotoImage
import webbrowser

PROJECT_URL = "https://github.com/chchaoo/iqihang-downloader"
ICON_SIZES = (20, 25, 30, 35, 40, 45, 50)


class ProjectLink:
    def __init__(self, window, button):
        self.window = window
        self.button = button
        self.images = {}

    def resize(self, target):
        size = min(ICON_SIZES, key=lambda value: abs(value - target))
        if size not in self.images:
            path = Path(__file__).resolve().parent / "assets" / f"github-{size}.png"
            self.images[size] = PhotoImage(master=self.window, file=str(path))
        self.button.configure(image=self.images[size])


def open_project():
    return webbrowser.open(PROJECT_URL, new=2)
