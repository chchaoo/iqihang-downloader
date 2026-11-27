"""Tk desktop entry point."""

import json
import queue
import shutil
import time
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from tkinter import filedialog, messagebox, simpledialog, ttk

from display import SCALE_OPTIONS, Display, ResponsiveRow, enable_dpi_awareness
from downloader import AutoWorkers, Transfer, catalogue, discard_incomplete, existing, login, lower_qualities

from project_link import ProjectLink, open_project
from segment_download import MAX_CONNECTIONS
from settings import PROGRAM_DIRECTORY, STATE_PATH, protect_credentials, read_settings, restore_credentials, write_settings
from transfer_process import TransferProcess
from windows_input import english_password

VERSION = "2.4.0"
JOURNAL = ".iqihang-transfer-log.json"


def valid_number(value, maximum, default):
    if type(value) is int and 1 <= value <= maximum:
        return value
    return default


def size_text(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class QualityDialog(simpledialog.Dialog):
    def __init__(self, parent, name, options, display):
        self.display = display
        self.name = name
        self.options = options
        self.result = None
        super().__init__(parent, title="选择较低画质")

    def body(self, frame):
        ttk.Label(frame, text=self.name, wraplength=self.display.px(420)).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="请选择这次重试要下载的画质").pack(anchor="w")
        self.choice = ttk.Combobox(frame, state="readonly", width=32,
                                   values=[label for _, label in self.options])
        self.choice.pack(anchor="w", pady=6)
        return self.choice

    def buttonbox(self):
        buttons = ttk.Frame(self, padding=6)
        ttk.Button(buttons, text="下载", command=self.ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="取消", command=self.cancel).pack(side="left", padx=4)
        buttons.pack()
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        if self.choice.current() < 0:
            messagebox.showinfo("请选择画质", "请先在列表中选定一档画质。", parent=self)
            return False
        return True

    def apply(self):
        self.result = self.options[self.choice.current()][0]


class App:
    def __init__(self, window, settings_path=STATE_PATH):
        self.window = window
        self.window.title(f"爱启航课程下载器 {VERSION}")
        self.events = queue.Queue()
        self.token = None
        self.loading = False
        self.items = {}
        self.active = {}
        self.batch = None
        self.paused = False
        self.stopping = False
        self.closed = False
        self.close_after_stop = False
        self.history = {}
        self.root_path = None
        self.settings_path = settings_path
        local_downloads = PROGRAM_DIRECTORY.parent / "downloads"
        default_folder = local_downloads if local_downloads.is_dir() else Path.home() / "Downloads" / "爱启航课程"
        self.defaults = {"location": str(default_folder), "auto": True, "aria2": True,
                         "workers": 32, "connections": MAX_CONNECTIONS, "scale": SCALE_OPTIONS[0]}
        loaded = read_settings(settings_path)
        self.settings = self.defaults.copy()
        for name, default in self.defaults.items():
            value = loaded.get(name)
            if type(value) is type(default):
                self.settings[name] = value
        self.settings["workers"] = valid_number(self.settings["workers"], 32, 32)
        self.settings["connections"] = valid_number(self.settings["connections"],
                                                      MAX_CONNECTIONS, MAX_CONNECTIONS)
        if self.settings["scale"] not in SCALE_OPTIONS:
            self.settings["scale"] = SCALE_OPTIONS[0]
        self.zoom = tk.StringVar(value=self.settings["scale"])
        self.display = Display(window)
        self.display.apply(self.zoom.get())
        self.frame_padding = []
        self.settings_save = None
        self.restoring_settings = False
        self.settings_warning = ""
        saved = loaded.get("credentials")
        credentials = restore_credentials(saved)
        self.sealed_credentials = saved if credentials else None

        header = self.toolbar((12, 12, 12, 6))
        credentials_panel = header.left
        account_actions = header.right
        ttk.Label(credentials_panel, text="账号").pack(side="left")
        self.account = ttk.Entry(credentials_panel, width=16)
        self.account.pack(side="left", padx=(5, 12))
        ttk.Label(credentials_panel, text="密码").pack(side="left")
        self.password = ttk.Entry(credentials_panel, show="*", width=16)
        self.password.pack(side="left", padx=(5, 12))
        english_password(self.password)
        self.login_button = ttk.Button(credentials_panel, text="登录并加载目录", command=self.sign_in)
        self.login_button.pack(side="left")
        self.reload_button = ttk.Button(credentials_panel, text="刷新目录", command=self.reload, state="disabled")
        self.reload_button.pack(side="left", padx=5)
        self.password.bind("<Return>", lambda event: self.sign_in())
        self.reset_button = ttk.Button(account_actions, text="恢复默认", command=self.reset_settings)
        self.reset_button.pack(side="left")
        if credentials:
            self.account.insert(0, credentials[0])
            self.password.insert(0, credentials[1])

        settings = self.toolbar((12, 3, 12, 7))
        destination = settings.left
        concurrency = settings.right
        self.location = tk.StringVar(value=self.settings["location"])
        ttk.Label(destination, text="保存到").pack(side="left")
        self.location_entry = ttk.Entry(destination, textvariable=self.location, width=26)
        self.location_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.location_entry.bind("<Return>", lambda event: self.refresh_files())
        self.location_entry.bind("<FocusOut>", lambda event: self.refresh_files())
        self.folder_button = ttk.Button(destination, text="选择目录", command=self.choose_folder)
        self.folder_button.pack(side="left", padx=(0, 12))
        self.auto = tk.BooleanVar(value=self.settings["auto"])
        self.auto_checkbox = ttk.Checkbutton(concurrency, text="自动并发", variable=self.auto,
                                            command=self.update_mode)
        self.auto_checkbox.pack(side="left")
        self.worker_label = ttk.Label(concurrency)
        self.worker_label.pack(side="left", padx=(8, 4))
        self.worker_count = tk.StringVar(value=str(self.settings["workers"]))
        self.workers = ttk.Spinbox(concurrency, from_=1, to=32, width=4,
                                   textvariable=self.worker_count)
        self.workers.pack(side="left")
        self.update_mode()

        segments = self.toolbar((12, 0, 12, 7))
        connection_options = segments.left
        appearance = segments.right
        self.use_aria2 = tk.BooleanVar(value=self.settings["aria2"])
        self.aria_checkbox = ttk.Checkbutton(connection_options, text="使用 aria2 分段", variable=self.use_aria2,
                                             command=self.update_engine_label)
        self.aria_checkbox.pack(side="left", padx=(0, 12))
        ttk.Label(connection_options, text="单文件并发上限").pack(side="left")
        self.connection_count = tk.StringVar(value=str(self.settings["connections"]))
        self.connections = ttk.Spinbox(connection_options, from_=1, to=MAX_CONNECTIONS, width=4,
                                       textvariable=self.connection_count)
        self.connections.pack(side="left", padx=(8, 14))
        self.engine_label = ttk.Label(connection_options)
        self.engine_label.pack(side="left")
        ttk.Label(appearance, text="界面缩放").pack(side="left", padx=(5, 4))
        zoom_picker = ttk.Combobox(appearance, textvariable=self.zoom, values=SCALE_OPTIONS,
                                   state="readonly", width=10)
        zoom_picker.pack(side="left")
        zoom_picker.bind("<<ComboboxSelected>>", lambda _event: self.refresh_display())
        self.update_engine_label()

        toolbar = self.toolbar((12, 0, 12, 8))
        actions, tools = toolbar.left, toolbar.right
        self.selected_button = ttk.Button(actions, text="下载所选未完成", command=lambda: self.start(False))
        self.selected_button.pack(side="left")
        self.all_button = ttk.Button(actions, text="下载全部未完成", command=lambda: self.start(True))
        self.all_button.pack(side="left", padx=6)
        self.pause_button = ttk.Button(actions, text="暂停排队", command=self.toggle_pause)
        self.pause_button.pack(side="left")
        self.end_button = ttk.Button(actions, text="结束并删除未完成文件", command=self.end_batch)
        self.end_button.pack(side="left", padx=6)
        self.quality_button = ttk.Button(actions, text="选择较低画质", command=self.choose_quality)
        self.quality_button.pack(side="left")
        ttk.Button(tools, text="清除已完成日志", command=self.clear_history).pack(side="left")

        panel = self.padded_frame((12, 0, 12, 0))
        panel.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(panel, columns=("type", "status", "progress", "size"),
                                 selectmode="extended")
        self.tree.bind("<Button-1>", self.toggle_branch)
        self.tree.bind("<<TreeviewOpen>>", lambda _event: self.set_branch_icon("open"))
        self.tree.bind("<<TreeviewClose>>", lambda _event: self.set_branch_icon("closed"))
        self.columns = (("#0", "课程目录", 415), ("type", "类型", 64),
                        ("status", "状态", 225), ("progress", "进度", 90),
                        ("size", "已保存 / 总大小", 168))
        for key, label, _ in self.columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, stretch=key in ("#0", "status"))
        scroll = ttk.Scrollbar(panel, command=self.tree.yview)
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)

        footer = self.padded_frame((12, 10, 12, 12))
        footer.pack(fill="x")
        prompt = "请点击登录以读取课程目录" if credentials else "请登录以读取课程目录"
        self.message = tk.StringVar(value=prompt)
        for variable in (self.location, self.auto, self.use_aria2, self.worker_count,
                         self.connection_count, self.zoom):
            variable.trace_add("write", self.schedule_settings_save)
        status_row = ResponsiveRow(footer)
        status_row.pack(fill="x")
        self.status_label = ttk.Label(status_row.left, textvariable=self.message, justify="left")
        self.status_label.pack(anchor="w")
        status_row.left.bind("<Configure>", self.wrap_status)
        project_button = ttk.Button(status_row.right, text="访问开源地址（源码和版本更新）",
                                    compound="left", command=self.visit_project)
        project_button.pack(side="right")
        self.project_link = ProjectLink(self.window, project_button)
        self.bar = ttk.Progressbar(footer)
        self.bar.pack(fill="x", pady=(6, 0))
        self.refresh_display(initial=True)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.update_buttons()
        self.tick_request = window.after(350, self.tick)

    def padded_frame(self, padding, row=False):
        frame = ResponsiveRow(self.window) if row else ttk.Frame(self.window)
        self.frame_padding.append((frame, padding))
        return frame

    def toolbar(self, padding):
        frame = self.padded_frame(padding, row=True)
        frame.pack(fill="x")
        return frame

    def wrap_status(self, event):
        width = max(100, event.width - self.display.px(12))
        if int(self.status_label.cget("wraplength") or 0) != width:
            self.status_label.configure(wraplength=width)

    def refresh_display(self, initial=False):
        previous = self.display.scale
        changed = self.display.apply(self.zoom.get())
        if not changed and not initial:
            return
        px = self.display.px
        self.project_link.resize(px(20))
        for frame, padding in self.frame_padding:
            frame.configure(padding=tuple(px(value) for value in padding))
        for key, _, width in self.columns:
            old = self.tree.column(key, "width")
            new = px(width) if initial else round(old * self.display.scale / previous)
            self.tree.column(key, width=new, minwidth=px(50))
        limits = self.display.fit(px(715), px(445))
        self.window.minsize(*limits)
        if self.window.state() == "normal":
            ratio = self.display.scale / previous
            dimensions = (px(1140), px(670)) if initial else (
                round(self.window.winfo_width() * ratio), round(self.window.winfo_height() * ratio))
            width, height = self.display.fit(*dimensions)
            self.window.geometry(f"{max(width, limits[0])}x{max(height, limits[1])}")

    def visit_project(self):
        try:
            if open_project():
                return
        except OSError:
            pass
        self.message.set("无法打开默认浏览器，请检查 Windows 的浏览器设置。")

    def schedule_settings_save(self, *_):
        if self.restoring_settings:
            return
        if self.settings_save is not None:
            self.window.after_cancel(self.settings_save)
        self.settings_save = self.window.after(400, self.save_settings)

    def save_settings(self):
        if self.settings_save is not None:
            self.window.after_cancel(self.settings_save)
            self.settings_save = None
        self.settings.update(location=self.location.get(), auto=self.auto.get(),
                             aria2=self.use_aria2.get(), scale=self.zoom.get())
        for key, variable, maximum in (
                ("workers", self.worker_count, 32),
                ("connections", self.connection_count, MAX_CONNECTIONS)):
            try:
                number = int(variable.get())
            except ValueError:
                continue
            self.settings[key] = valid_number(number, maximum, self.settings[key])
        data = self.settings.copy()
        if self.sealed_credentials:
            data["credentials"] = self.sealed_credentials
        try:
            write_settings(self.settings_path, data)
            self.settings_warning = ""
        except OSError:
            self.settings_warning = "无法保存配置，请检查程序目录的写入权限。"
            self.message.set(self.settings_warning)

    def reset_settings(self):
        if self.loading or self.batch:
            return
        try:
            self.settings_path.unlink(missing_ok=True)
        except OSError:
            self.message.set("无法删除配置文件，请检查程序目录的写入权限。")
            return
        if self.settings_save is not None:
            self.window.after_cancel(self.settings_save)
            self.settings_save = None
        self.sealed_credentials = None
        self.settings_warning = ""
        self.settings = self.defaults.copy()
        self.restoring_settings = True
        try:
            self.location.set(self.defaults["location"])
            self.auto.set(self.defaults["auto"])
            self.use_aria2.set(self.defaults["aria2"])
            self.worker_count.set(str(self.defaults["workers"]))
            self.connection_count.set(str(self.defaults["connections"]))
            self.zoom.set(self.defaults["scale"])
        finally:
            self.restoring_settings = False
        self.account.delete(0, "end")
        self.password.delete(0, "end")
        self.token = None
        self.populate([])
        self.update_mode()
        self.update_engine_label()
        self.update_buttons()
        self.refresh_display()
        self.message.set("已恢复默认并清除记住的账号和密码。")

    def update_mode(self):
        self.worker_label.configure(text="并发上限" if self.auto.get() else "固定路数")

    def update_engine_label(self):
        if not self.use_aria2.get():
            http_mode = "单连接（已关闭 aria2）"
        else:
            http_mode = "aria2" if shutil.which("aria2c") else "单连接（未找到 aria2c）"
        hls_mode = "yt-dlp / FFmpeg" if shutil.which("yt-dlp") else "单连接（未找到 yt-dlp）"
        self.engine_label.configure(text=f"HTTP {http_mode}   ·   流媒体 {hls_mode}")

    def update_buttons(self):
        busy = self.loading or self.batch is not None
        available = bool(self.items) and not busy
        self.login_button.configure(state="disabled" if busy else "normal")
        self.reload_button.configure(state="normal" if self.token and not busy else "disabled")
        for widget in (self.location_entry, self.folder_button, self.workers, self.connections,
                       self.auto_checkbox, self.aria_checkbox, self.reset_button):
            widget.configure(state="disabled" if busy else "normal")
        for button in (self.selected_button, self.all_button, self.quality_button):
            button.configure(state="normal" if available else "disabled")
        self.pause_button.configure(state="normal" if self.batch and self.pending and not self.stopping else "disabled",
                                    text="继续排队" if self.paused else "暂停排队")
        self.end_button.configure(state="normal" if self.batch and not self.stopping else "disabled")

    def load(self, credentials=None):
        self.loading = True
        token = self.token
        self.message.set("正在读取课程目录…")
        self.update_buttons()

        def work():
            try:
                active_token = login(*credentials) if credentials else token
            except (OSError, ValueError, KeyError):
                self.events.put(("error", "登录失败，请检查账号密码及网络。"))
                return
            if credentials:
                try:
                    encrypted = protect_credentials(*credentials)
                except OSError:
                    encrypted = None
                self.events.put(("authenticated", encrypted))
            try:
                self.events.put(("catalogue", (active_token, catalogue(active_token))))
            except (OSError, ValueError, KeyError, TypeError):
                self.events.put(("error", "读取课程列表失败，请检查网络或重新登录。"))

        Thread(target=work, daemon=True).start()

    def sign_in(self):
        if self.loading or self.batch:
            return
        account, password = self.account.get().strip(), self.password.get()
        if not account or not password:
            self.message.set("请输入账号和密码。")
            return
        self.account.delete(0, "end")
        self.password.delete(0, "end")
        self.token = None
        self.populate([])
        self.load((account, password))

    def reload(self):
        if self.token and not self.loading and not self.batch:
            self.load()

    def set_branch_icon(self, state):
        row = self.tree.focus()
        if row and self.tree.get_children(row):
            self.tree.item(row, image=self.display.indicators.images[state])

    def toggle_branch(self, event):
        if self.tree.identify_element(event.x, event.y) != "image":
            return
        row = self.tree.identify_row(event.y)
        if row and self.tree.get_children(row):
            self.tree.focus_set()
            self.tree.tk.call("ttk::treeview::Toggle", self.tree, row)
            return "break"

    def populate(self, items):
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.root_path = None
        parents = {(): ""}
        icons = self.display.indicators.images
        for item in items:
            parent = item.parts[:-1]
            for depth in range(1, len(parent) + 1):
                prefix = parent[:depth]
                if prefix not in parents:
                    parents[prefix] = self.tree.insert(parents[prefix[:-1]], "end",
                                                       text=prefix[-1], open=False, image=icons["closed"])
            row = self.tree.insert(parents[parent], "end", text=item.parts[-1], image=icons["leaf"])
            self.items[row] = item
        self.refresh_files()

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.location.set(folder)
            self.refresh_files()

    def history_key(self, item):
        return "/".join(item.parts)

    def refresh_files(self):
        if self.batch or not self.location.get().strip():
            return False
        try:
            root = Path(self.location.get().strip()).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            self.message.set("保存路径无效，请重新选择目录。")
            return False
        if root != self.root_path:
            self.root_path = root
            try:
                data = json.loads((root / JOURNAL).read_text(encoding="utf-8"))
                self.history = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self.history = {}
        for row, item in self.items.items():
            saved = existing(item, root) if item.kind else 0
            record = self.history.get(self.history_key(item), {})
            failure = record.get("error", "") if isinstance(record, dict) else ""
            status = "已有文件" if saved else item.reason or failure or "待下载"
            self.tree.item(row, values=(item.kind, status, "100%" if saved else "",
                                         size_text(saved) if saved else ""))
        self.summarize_tree()
        return True

    def summarize_tree(self, parent=""):
        total = done = 0
        for row in self.tree.get_children(parent):
            if row in self.items:
                if self.items[row].kind:
                    total += 1
                    done += self.tree.set(row, "progress") == "100%"
            else:
                group_done, group_total = self.summarize_tree(row)
                self.tree.set(row, "status", f"{group_done} / {group_total} 项已保存")
                done += group_done
                total += group_total
        return done, total

    def select_rows(self, selected):
        rows = []
        for row in self.items:
            ancestor = row
            while ancestor:
                if ancestor in selected:
                    rows.append(row)
                    break
                ancestor = self.tree.parent(ancestor)
        return rows

    def choose_quality(self):
        selected = self.tree.selection()
        item = self.items.get(selected[0]) if len(selected) == 1 else None
        if self.batch or self.loading or not self.token or not self.refresh_files():
            return
        if not item or item.kind not in {"点播", "回放"}:
            self.message.set("请选中一个下载失败的点播或回放视频。")
            return
        record = self.history.get(self.history_key(item), {})
        if not isinstance(record, dict) or record.get("status") != "失败" or existing(item, self.root_path):
            self.message.set("请选中一个尚未保存且下载失败的视频。")
            return
        self.loading = True
        self.update_buttons()
        self.message.set("正在查询可选画质…")
        token = self.token

        def work():
            try:
                options = lower_qualities(item, token)
                self.events.put(("quality", (selected[0], item.parts[-1], options)))
            except ValueError as error:
                self.events.put(("error", f"无法获取较低画质 · {error}"))
            except (OSError, KeyError, TypeError):
                self.events.put(("error", "读取画质失败，请检查网络或重新登录。"))

        Thread(target=work, daemon=True).start()

    def start(self, all_items, override=None):
        if self.batch or self.loading or not self.token:
            return
        if not self.location.get().strip():
            self.message.set("请选择下载位置。")
            return
        if not self.refresh_files():
            return
        if override is not None:
            rows = [override[0]]
        else:
            rows = list(self.items) if all_items else self.select_rows(set(self.tree.selection()))
        if not rows:
            self.message.set("请先选择要下载的课程、章节或文件。")
            return
        pending = {}
        destinations = set()
        for row in rows:
            item = self.items[row]
            destination = self.root_path.joinpath(*item.parts)
            if item.kind and destination not in destinations and not existing(item, self.root_path):
                pending[row] = Transfer(item, destination,
                                        selected_quality=override[1] if override is not None else None)
                destinations.add(destination)
        if not pending:
            self.message.set("所选范围没有待下载文件。")
            return
        ffmpeg = shutil.which("ffmpeg")
        if any(task.item.kind != "资料" for task in pending.values()) and not ffmpeg:
            self.message.set("下载视频需要将 FFmpeg 加入 PATH，然后重新打开工具。")
            return
        if any(task.item.kind == "回放" for task in pending.values()) and not shutil.which("ffprobe"):
            self.message.set("下载回放需要将 FFprobe 加入 PATH，然后重新打开工具。")
            return
        try:
            ceiling = int(self.workers.get())
            if not 1 <= ceiling <= 32:
                raise ValueError
        except ValueError:
            self.message.set("文件并发数量须为 1 到 32。")
            return
        try:
            connections = int(self.connections.get())
            if not 1 <= connections <= MAX_CONNECTIONS:
                raise ValueError
        except ValueError:
            self.message.set(f"单文件并发上限须为 1 到 {MAX_CONNECTIONS}。")
            return
        try:
            self.root_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.message.set("无法创建下载目录，请检查路径和写入权限。")
            return
        self.begin(pending, ceiling, self.auto.get(), ffmpeg, connections, self.use_aria2.get())

    def begin(self, pending, ceiling, automatic, ffmpeg, connections, use_aria2):
        self.batch = pending
        self.pending = deque(pending)
        self.executor = ThreadPoolExecutor(max_workers=ceiling)
        self.auto_control = AutoWorkers(ceiling) if automatic else None
        self.limit = self.auto_control.limit if automatic else ceiling
        self.ffmpeg = ffmpeg
        self.segment_limit = connections
        self.batch_aria2 = use_aria2
        self.last_time = time.monotonic()
        self.last_sizes = {}
        self.speed_samples = deque()
        self.paused = False
        self.bar.configure(value=0)
        self.update_buttons()

    def toggle_pause(self):
        self.paused = not self.paused
        self.update_buttons()

    def end_batch(self):
        if not self.batch or self.stopping:
            return
        self.stopping = True
        self.pending.clear()
        for process, _ in self.active.values():
            process.stop()
        self.message.set("正在结束下载并删除未完成文件…")
        self.update_buttons()

    def write_history(self):
        path = self.root_path / JOURNAL
        try:
            if self.history:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.history, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                temporary.replace(path)
            else:
                path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def clear_history(self):
        if self.batch:
            root = self.root_path
        elif self.refresh_files():
            root = self.root_path
        else:
            return
        if root:
            self.history = {key: record for key, record in self.history.items()
                            if isinstance(record, dict) and record.get("status") != "完成"}
            ok = self.write_history()
            self.message.set("已清理完成记录。" if ok else "日志清理失败，请检查目录权限。")

    def finish(self, stopped=False):
        tasks = self.batch
        done = sum(task.status == "完成" for task in tasks.values())
        failed = sum(task.status == "失败" for task in tasks.values())
        self.executor.shutdown(wait=False)
        cleanup_failures = 0
        if stopped:
            for task in tasks.values():
                try:
                    discard_incomplete(task)
                except OSError:
                    cleanup_failures += 1
        self.batch = None
        self.paused = False
        self.stopping = False
        if stopped:
            self.refresh_files()
        if done == len(tasks):
            self.history = {key: record for key, record in self.history.items()
                            if isinstance(record, dict) and record.get("status") != "完成"}
        ok = self.write_history()
        written = sum(task.size() for task in tasks.values() if task.status == "完成")
        self.message.set(
            f"{'已结束下载' if stopped else '本批结束'} · 完成 {done}/{len(tasks)} 项 · "
            f"失败 {failed} 项 · 已保存 {size_text(written)}"
            + (" · 视频失败后可选中该文件并点击“选择较低画质”"
               if any(task.status == "失败" and task.item.kind in {"点播", "回放"}
                      for task in tasks.values()) else "")
            + (f" · {cleanup_failures} 项临时文件无法删除" if cleanup_failures
               else " · 已清理未完成文件" if stopped else "")
            + ("" if ok else " · 日志写入失败"))
        self.update_buttons()

    def update_downloads(self):
        now = time.monotonic()
        elapsed = max(0.01, now - self.last_time)
        measured_workers = len(self.active)
        saturated = measured_workers == self.limit and bool(self.pending) and not self.paused
        delta = 0
        for row, (_, future) in list(self.active.items()):
            task = self.batch[row]
            size = task.size()
            delta += max(0, size - self.last_sizes.get(row, size))
            self.last_sizes[row] = size
            if not future.done():
                saturated = saturated and task.status.startswith("下载中")
                continue
            try:
                future.result()
            except Exception as error:
                task.status = "失败"
                task.error = f"下载失败（{type(error).__name__}）"
            if task.status in {"完成", "失败"}:
                self.history[self.history_key(task.item)] = {
                    "status": task.status, "error": task.error if task.status == "失败" else "",
                }
                self.write_history()
            del self.active[row]
        rate = delta / elapsed
        self.last_time = now
        if self.auto_control:
            self.auto_control.sample(rate, elapsed, saturated)
            self.limit = self.auto_control.limit
        while not self.paused and not self.stopping and self.pending and len(self.active) < self.limit:
            row = self.pending.popleft()
            task = self.batch[row]
            self.last_sizes[row] = task.size()
            process = TransferProcess()
            future = self.executor.submit(process.run, task, self.token, None, self.ffmpeg,
                                          self.segment_limit, self.batch_aria2)
            self.active[row] = (process, future)

        completed = failed = known_total = known_saved = unknown = total_saved = 0
        for row, task in self.batch.items():
            size = task.size()
            total_saved += size
            complete = task.status == "完成"
            completed += complete
            failed += task.status == "失败"
            total = size if complete else task.total
            if total:
                known_total += total
                known_saved += min(size, total)
            else:
                unknown += 1
            percent = (min(99, 100 * size / total) if total else min(99, 100 * task.fraction))
            progress = "100%" if complete else f"{percent:.0f}%" if percent or total else "—"
            status = task.status if task.status != "失败" else f"失败 · {task.error}"
            self.tree.item(row, values=(task.item.kind, status, progress,
                                         size_text(size) + (f" / {size_text(total)}" if total else "")))
        self.bar.configure(value=100 * completed / len(self.batch))
        self.summarize_tree()
        self.speed_samples.append((now, delta, elapsed))
        while self.speed_samples[0][0] < now - 4:
            self.speed_samples.popleft()
        average = sum(sample[1] for sample in self.speed_samples) / sum(
            sample[2] for sample in self.speed_samples
        )
        speed = f"{average * 8 / 1_000_000:.1f} Mbps"
        known = f"{size_text(known_saved)} / {size_text(known_total)}" if known_total else "待确认"
        mode = "正在结束并清理" if self.stopping else "暂停排队" if self.paused else "自动测速" if self.auto_control and not self.auto_control.stable else "下载"
        self.message.set(f"{mode} · 文件 {len(self.active)}/{self.limit} 路 · 单文件上限 {self.segment_limit} · 完成 {completed}/{len(self.batch)} 项"
                         f" · 失败 {failed} · {speed} · 已保存 {size_text(total_saved)}\n"
                         f"已知总量进度 {known} · 总大小未知 {unknown} 项")
        if not self.pending and not self.active:
            self.finish(stopped=self.stopping)
            if self.close_after_stop:
                self.close()
        else:
            self.update_buttons()

    def tick(self):
        self.tick_request = None
        self.refresh_display()
        while True:
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "authenticated":
                if data is None:
                    self.settings_warning = "Windows 加密失败，未记住本次登录信息。"
                    self.message.set(self.settings_warning)
                else:
                    self.sealed_credentials = data
                    self.save_settings()
                continue
            self.loading = False
            if event == "catalogue":
                self.token, items = data
                self.populate(items)
                status = f"目录已加载 · {sum(bool(item.kind) for item in items)} 个可下载文件"
                self.message.set(f"{status} · {self.settings_warning}" if self.settings_warning else status)
            elif event == "quality":
                row, name, options = data
                if options:
                    quality = QualityDialog(self.window, name, options, self.display).result
                    if quality is None:
                        self.message.set("已取消选择画质。")
                    else:
                        self.start(False, override=(row, quality))
                else:
                    self.message.set("此视频没有可选的较低画质。")
            else:
                self.message.set(data)
            self.update_buttons()
        if self.batch:
            self.update_downloads()
        if not self.closed:
            self.tick_request = self.window.after(400, self.tick)

    def close(self):
        if self.batch:
            if self.stopping or messagebox.askyesno(
                    "结束下载", "关闭后会停止本批下载并删除未完成的临时文件。确定关闭？",
                    parent=self.window):
                self.close_after_stop = True
                self.end_batch()
            return
        if self.settings_save is not None:
            self.save_settings()
        if self.tick_request is not None:
            self.window.after_cancel(self.tick_request)
        self.closed = True
        self.window.destroy()



def main():
    enable_dpi_awareness()
    window = tk.Tk()
    App(window)
    window.mainloop()


if __name__ == "__main__":
    main()


