"""Run a transfer in a process that can be stopped along with its engines."""

import ctypes
import json
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock, Thread

from downloader import Item, Transfer, file_size, run_transfer
from download_courses import HIDE_WINDOW


class WindowsJob:
    def __init__(self, pid):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": (wintypes.HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)),
            "OpenProcess": (wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
            "AssignProcessToJobObject": (wintypes.BOOL, (wintypes.HANDLE, wintypes.HANDLE)),
            "TerminateJobObject": (wintypes.BOOL, (wintypes.HANDLE, wintypes.UINT)),
            "CloseHandle": (wintypes.BOOL, (wintypes.HANDLE,)),
        }
        for name, (result, args) in signatures.items():
            function = getattr(kernel, name)
            function.restype, function.argtypes = result, args
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = kernel.OpenProcess(0x0101, False, pid)
        try:
            if not process or not kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            self.close()
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def stop(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.kernel.CloseHandle(self.handle)


class TransferProcess:
    def __init__(self):
        self.cancelled = Event()
        self.lock = Lock()
        self.process = None
        self.job = None

    def stop(self):
        self.cancelled.set()
        with self.lock:
            if self.process is None:
                return
            try:
                if self.job is not None:
                    self.job.stop()
                elif os.name != "nt":
                    os.killpg(self.process.pid, signal.SIGKILL)
            except OSError:
                if self.process.poll() is None:
                    self.process.kill()

    def run(self, task, token, user_id, ffmpeg, connections, use_aria2):
        payload = {
            "item": asdict(task.item), "path": str(task.path),
            "quality": task.selected_quality, "token": token, "user_id": user_id,
            "ffmpeg": ffmpeg, "connections": connections, "use_aria2": use_aria2,
        }
        try:
            with self.lock:
                if self.cancelled.is_set():
                    task.status = "已取消"
                    return
                if getattr(sys, "frozen", False):
                    command = [sys.executable, "--transfer-worker"]
                else:
                    command = [sys.executable, "-B", str(Path(__file__).resolve())]
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", creationflags=HIDE_WINDOW,
                    start_new_session=os.name != "nt",
                )
                try:
                    if os.name == "nt":
                        self.job = WindowsJob(self.process.pid)
                except OSError:
                    self.process.kill()
                    raise
            with self.process as process:
                with process.stdin:
                    process.stdin.write(json.dumps(payload) + "\n")
                for line in process.stdout:
                    state = json.loads(line)
                    task.total, task.fraction, task.engine_bytes, task.status, task.error = state
                code = process.wait()
            if code or task.status not in {"失败", "完成"}:
                raise ValueError(f"下载进程意外退出（{code}）")
        except (OSError, ValueError) as error:
            task.status = "失败"
            task.error = f"下载进程启动或通信失败（{getattr(error, 'winerror', None) or type(error).__name__}）"
        finally:
            with self.lock:
                if self.job is not None:
                    self.job.close()
                    self.job = None
                if self.process is not None:
                    self.process.wait()
                    self.process = None
            if self.cancelled.is_set():
                task.status = "完成" if file_size(task.path) else "已取消"
                task.error = ""


def worker():
    data = json.loads(sys.stdin.readline())
    item = Item(**data["item"])
    task = Transfer(item, Path(data["path"]), selected_quality=data["quality"])
    finished = Event()

    def download():
        try:
            run_transfer(task, data["token"], data["user_id"], data["ffmpeg"],
                         data["connections"], data["use_aria2"])
        except Exception as error:
            task.error = f"下载进程内部错误（{type(error).__name__}）"
            task.status = "失败"
        finally:
            finished.set()

    Thread(target=download, daemon=True).start()
    while True:
        done = finished.wait(0.25)
        print(json.dumps([task.total, task.fraction, task.size(), task.status, task.error]),
              flush=True)
        if done:
            return


if __name__ == "__main__":
    worker()
