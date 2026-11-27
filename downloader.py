"""Course catalogue and resumable transfers for the desktop downloader."""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from course_replays import replay_versions, save_replay
from segment_download import MAX_CONNECTIONS, acquire, clear_parts, parts_dir
from download_courses import SITE, api, fetch_video, safe_name, video_versions, videos


@dataclass(frozen=True)
class Item:
    parts: tuple
    kind: str
    source: object = field(default=None, repr=False)
    reason: str = ""


@dataclass
class Transfer:
    item: Item
    path: Path
    status: str = "等待"
    total: int = 0
    fraction: float = 0
    error: str = ""
    selected_quality: int | float | None = None
    engine_bytes: int | None = None

    @property
    def partial(self):
        return self.path.with_suffix(".part" + self.path.suffix)

    def size(self):
        if self.status == "完成":
            return file_size(self.path)
        if self.engine_bytes is not None:
            return self.engine_bytes
        return file_size(self.path) or file_size(self.partial)


def file_size(path):
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def discard_incomplete(transfer):
    partial = transfer.partial
    partial.unlink(missing_ok=True)
    partial.with_name(partial.name + ".json").unlink(missing_ok=True)
    directory = parts_dir(transfer.path)
    if directory.exists():
        shutil.rmtree(directory)


def existing(item, root):
    path = root.joinpath(*item.parts)
    return file_size(path) or file_size(
        path.with_name(path.stem + " [01]" + path.suffix)
    )


def login(account, password):
    return api("/api/ark/sso/login", payload={
        "loginType": 2, "clientType": 2, "registerPlatform": 2,
        "account": account, "password": password,
    })["token"]


def catalogue(token):
    courses = api("/api/ark/web/v1/user/course/course-list?isMarketingCourse=0&type=1",
                  token=token)
    items = []
    for course in courses:
        identifier = str(course["productCurriculumId"])
        folder = f"{safe_name(course['productName'])} [{safe_name(identifier)}]"
        try:
            nodes = api("/api/ark/web/v1/course/catalog/" + quote(identifier, safe=""),
                        token=token)["courseNodes"]
            if not nodes:
                items.append(Item((folder, "目录为空"), "", reason="暂无内容"))
                continue
        except (OSError, ValueError, KeyError):
            items.append(Item((folder, "读取目录失败"), "", reason="请重新加载目录"))
            continue
        for parts, resource in videos(nodes):
            stem = parts[-1].removesuffix(" [01]")
            parent = (folder, *parts[:-1])
            if resource.get("vid"):
                item = Item((*parent, stem + ".mp4"), "点播", resource["vid"])
            elif (resource.get("resourceType") == 2 and resource.get("resourceSource") == 1
                  and resource.get("clientReplayStatus") == 5):
                item = Item((*parent, stem + ".mp4"), "回放", (course, resource))
            elif resource.get("resourceType") == 4 and resource.get("lectureUrl"):
                url = urljoin(SITE + "/", resource["lectureUrl"])
                suffix = PurePosixPath(unquote(urlsplit(url).path)).suffix.lower()
                if urlsplit(url).scheme not in {"http", "https"}:
                    item = Item((*parent, parts[-1]), "", reason="资料地址无效")
                else:
                    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                        suffix = ".bin"
                    item = Item((*parent, stem + suffix), "资料", url)
            else:
                item = Item((*parent, parts[-1]), "", reason="暂无下载资源")
            items.append(item)
    return items


def lower_qualities(item, token, below=None):
    if item.kind == "点播":
        versions = video_versions(item.source)
    else:
        user_id = api("/api/ark/web/v1/user/info", token=token)["id"]
        versions = replay_versions(token, user_id, *item.source)
    ceiling = max(entry[0] for entry in versions) if below is None else below
    options = {rank: label for rank, label, _ in versions if rank < ceiling}
    return sorted(options.items(), reverse=True)


def save_material(transfer):
    url = transfer.item.source
    partial = transfer.partial
    metadata = partial.with_name(partial.name + ".json")
    headers = {"Referer": SITE + "/", "User-Agent": "Mozilla/5.0",
               "Accept-Encoding": "identity"}
    offset = file_size(partial)
    if offset and metadata.is_file():
        validator = metadata.read_text(encoding="utf-8").strip()
        if validator:
            headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
    try:
        response = urlopen(Request(url, headers=headers), timeout=30)
    except HTTPError as error:
        if error.code != 416 or "Range" not in headers:
            raise
        error.close()
        del headers["Range"], headers["If-Range"]
        response = urlopen(Request(url, headers=headers), timeout=30)
    with response:
        if "text/html" in response.headers.get("Content-Type", "").lower():
            raise ValueError("资料接口返回了网页")
        length = response.headers.get("Content-Length")
        expected = int(length) if length is not None else 0
        append = response.status == 206
        if append:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                 response.headers.get("Content-Range", ""))
            if ("Range" not in headers or not match or int(match[1]) != offset
                    or int(match[2]) + 1 != int(match[3])):
                metadata.unlink(missing_ok=True)
                raise ValueError("服务器返回的续传范围不完整")
            expected = int(match[3])
        validator = response.headers.get("ETag", "").strip()
        if not validator or validator.startswith("W/"):
            validator = response.headers.get("Last-Modified", "")
        if append and validator and validator != headers["If-Range"]:
            metadata.unlink(missing_ok=True)
            raise ValueError("资料版本已变化")
        transfer.total = expected
        with partial.open("ab" if append else "wb") as output:
            metadata.write_text(validator or (headers["If-Range"] if append else ""),
                                encoding="utf-8")
            while chunk := response.read(256 * 1024):
                output.write(chunk)
            if not output.tell() or expected and output.tell() != expected:
                raise ValueError("资料文件长度不完整")
        if transfer.path.suffix == ".pdf":
            with partial.open("rb") as source:
                if b"%PDF-" not in source.read(1024):
                    metadata.unlink(missing_ok=True)
                    raise ValueError("资料缺少 PDF 文件头")
    os.replace(partial, transfer.path)
    metadata.unlink(missing_ok=True)
    transfer.total = file_size(transfer.path)


def run_transfer(transfer, token, user_id, ffmpeg, connections=MAX_CONNECTIONS, use_aria2=True):
    def progress(fraction):
        transfer.fraction = fraction

    def engine_progress(done, total, fraction=0):
        transfer.engine_bytes = done
        transfer.total = total
        transfer.fraction = fraction

    def get_source(url):
        transfer.total = 0
        transfer.engine_bytes = None
        source = acquire(url, transfer.path, connections, engine_progress, ffmpeg=ffmpeg,
                         material=transfer.item.kind == "资料", use_aria2=use_aria2)
        if source is None:
            transfer.total = 0
            transfer.engine_bytes = None
            transfer.fraction = 0
            if transfer.item.kind != "资料":
                transfer.status += "（FFmpeg）"
        return source

    for attempt in range(1, 4):
        transfer.status = f"下载中 {attempt}/3"
        transfer.fraction = 0
        transfer.total = 0
        transfer.engine_bytes = None
        try:
            item = transfer.item
            transfer.path.parent.mkdir(parents=True, exist_ok=True)
            if item.kind == "点播":
                fetch_video(ffmpeg, item.source, transfer.path, progress=progress,
                            selected_quality=transfer.selected_quality, acquire=get_source)
            elif item.kind == "回放":
                if user_id is None:
                    user_id = api("/api/ark/web/v1/user/info", token=token)["id"]
                course, resource = item.source
                save_replay(ffmpeg, token, user_id, course, resource, transfer.path,
                            progress=progress, selected_quality=transfer.selected_quality, acquire=get_source)
            else:
                source = get_source(item.source) if not transfer.partial.exists() else None
                if source is None:
                    save_material(transfer)
                else:
                    with source.open("rb") as stream:
                        header = stream.read(1024)
                    if (re.match(rb"(?is)\s*(?:<!doctype\s+html|<html\b)", header)
                            or transfer.path.suffix == ".pdf" and b"%PDF-" not in header):
                        raise ValueError("资料接口未返回有效文件")
                    os.replace(source, transfer.path)
            clear_parts(transfer.path)
            transfer.status = "完成"
            return
        except HTTPError as error:
            transfer.error = f"服务器返回 HTTP {error.code}"
            if error.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (OSError, ValueError, KeyError, HTTPException, subprocess.SubprocessError) as error:
            if isinstance(error, ValueError):
                transfer.error = re.sub(r"https?://\S+", "[媒体地址]", str(error))[:200]
            else:
                transfer.error = f"下载失败（{type(error).__name__}）"
        if attempt < 3:
            transfer.status = f"等待重试 {attempt}/2"
            time.sleep(2 * attempt)
    if transfer.item.kind in {"点播", "回放"}:
        quality = "最高画质" if transfer.selected_quality is None else "所选画质"
        transfer.error = f"{quality}下载失败 · {transfer.error}"
    transfer.status = "失败"


class AutoWorkers:
    """Compare sustained throughput at successive concurrency levels."""

    def __init__(self, ceiling):
        self.ceiling = ceiling
        self.limit = min(4, ceiling)
        self.best_limit = self.limit
        self.best_rate = 0
        self.stable = False
        self.elapsed = 0
        self.samples = []

    def sample(self, rate, elapsed, saturated):
        if not saturated:
            self.samples.clear()
            self.elapsed = 0
            return
        self.elapsed += elapsed
        if self.stable:
            if self.elapsed >= 90:
                self.limit = max(1, self.best_limit - max(1, self.best_limit // 3))
                self.best_limit = self.limit
                self.best_rate = 0
                self.stable = False
                self.elapsed = 0
            return
        if self.elapsed >= 3:
            self.samples.append(rate)
        if self.elapsed < 12 or not self.samples:
            return
        measured = sum(self.samples) / len(self.samples)
        self.samples.clear()
        self.elapsed = 0
        if measured > self.best_rate * 1.08:
            self.best_rate = measured
            self.best_limit = self.limit
            if self.limit < self.ceiling:
                self.limit = min(self.ceiling, self.limit + max(1, self.limit // 2))
                return
        self.limit = min(self.ceiling, self.best_limit + 1)
        self.stable = True
