"""Per-file HTTP and HLS download engines."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from download_courses import HIDE_WINDOW, SITE

MAX_CONNECTIONS = 16
HEADERS = ["--referer", SITE + "/", "--user-agent", "Mozilla/5.0"]
ARIA_PROGRESS = re.compile(r"\[#\w+\s+(\d+)(?:B)?/(\d+)(?:B)?(?:\(\d+%\))?\s")
HLS_MARKER = "IQH_PROGRESS "


def engine_for(url, material=False):
    path = urlsplit(url).path.lower()
    if not material and path.endswith((".m3u8", ".mpd")):
        return "yt-dlp", shutil.which("yt-dlp")
    return "aria2", shutil.which("aria2c")


def parts_dir(destination):
    return destination.with_name(destination.name + ".iqihang-parts")


def run_engine(command, on_line, name):
    detail = ""
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace", creationflags=HIDE_WINDOW) as process:
        for line in process.stdout:
            on_line(line)
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if clean.startswith(("ERROR:", "Exception:")):
                detail = re.sub(r'https?://[^\s"<>]+', "[媒体地址]", clean)[:160]
        code = process.wait()
    if code:
        raise ValueError(f"{name} 下载失败（退出码 {code}）" + (f" · {detail}" if detail else ""))


def http_metadata(url):
    request = Request(url, method="HEAD", headers={
        "Referer": SITE + "/", "User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity",
    })
    try:
        with urlopen(request, timeout=5) as response:
            etag = response.headers.get("ETag", "").strip()
            return response.headers.get_content_type(), (
                etag if etag and not etag.startswith("W/")
                else response.headers.get("Last-Modified", "")
            )
    except OSError:
        return "", ""


def encrypted_hls(url):
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=5) as response:
            playlist = response.read(128 * 1024)
    except OSError:
        return False
    methods = re.findall(rb"(?im)^#EXT-X-KEY:[^\r\n]*?\bMETHOD=([A-Z0-9-]+)", playlist)
    return any(method.upper() != b"NONE" for method in methods)


def aria2_file(executable, url, target, connections, progress, validator=""):
    def update(line):
        match = ARIA_PROGRESS.search(line)
        if match:
            progress(int(match[1]), int(match[2]))

    state = target.with_name(target.name + ".aria2")
    # A partial file without aria2's piece map may contain holes.
    marker = target.with_name(target.name + ".validator")
    recorded = marker.read_text(encoding="utf-8") if marker.is_file() else ""
    if not validator or validator != recorded or not state.is_file() or not target.is_file():
        state.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
    marker.write_text(validator, encoding="utf-8")
    command = [
        executable, "--no-conf=true", "--no-netrc=true",
        f"--split={connections}", f"--max-connection-per-server={connections}",
        "--min-split-size=1M", "--file-allocation=none", "--auto-save-interval=3",
        "--continue=true", "--auto-file-renaming=false", "--allow-overwrite=true",
        "--always-resume=false", "--check-certificate=true", "--http-accept-gzip=false",
        "--connect-timeout=15", "--timeout=30", "--max-tries=2", "--retry-wait=2",
        "--follow-torrent=false", "--follow-metalink=false", "--remote-time=false",
        "--summary-interval=0", "--console-log-level=error", "--download-result=hide",
        "--human-readable=false", "--truncate-console-readout=false",
        f"--dir={target.parent}", f"--out={target.name}", *HEADERS, url,
    ]
    run_engine(command, update, "aria2")
    if state.exists() or not target.is_file() or not target.stat().st_size:
        raise ValueError("aria2 未生成完整文件")
    size = target.stat().st_size
    progress(size, size)
    return target


def hls_file(executable, url, directory, connections, progress, ffmpeg):
    tracks = {}

    def update(line):
        if not line.startswith(HLS_MARKER):
            return
        try:
            filename, downloaded, total, index, count = json.loads(line[len(HLS_MARKER):])
        except (ValueError, TypeError):
            return
        if not isinstance(filename, str) or not isinstance(downloaded, int):
            return
        previous, known_total, fraction = tracks.get(filename, (0, 0, 0))
        if isinstance(index, int) and isinstance(count, int) and count:
            fraction = max(fraction, index / count)
        tracks[filename] = (max(previous, downloaded),
                            total if isinstance(total, int) else known_total, fraction)
        done = sum(track[0] for track in tracks.values())
        size = sum(track[1] for track in tracks.values()) if all(
            track[1] for track in tracks.values()) else 0
        progress(done, size, sum(track[2] for track in tracks.values()) / len(tracks))

    command = [
        executable, "--ignore-config", "--no-playlist", "--no-warnings", "--quiet",
        "--progress", "--newline", "--color", "never", "--output-na-placeholder", "null",
        "--progress-template",
        ("download:" + HLS_MARKER + "[%(progress.filename)j,%(progress.downloaded_bytes)j,"
         "%(progress.total_bytes)j,%(progress.fragment_index)j,%(progress.fragment_count)j]"),
        "--concurrent-fragments", str(connections), "--continue",
        "--abort-on-unavailable-fragments", "--retries", "2", "--fragment-retries", "2",
        "--socket-timeout", "30", "--downloader", "native",
        "--ffmpeg-location", ffmpeg, "--merge-output-format", "mkv", "--remux-video", "mkv",
        "--output", str(directory / "media.%(ext)s"), *HEADERS, "--", url,
    ]
    run_engine(command, update, "yt-dlp")
    target = directory / "media.mkv"
    if not target.is_file() or not target.stat().st_size:
        raise ValueError("yt-dlp 未生成完整文件")
    if not tracks:
        progress(target.stat().st_size, 0, 0)
    return target


def acquire(url, destination, connections, progress, ffmpeg=None, material=False, use_aria2=True):
    engine, executable = engine_for(url, material)
    if engine == "aria2" and not use_aria2:
        executable = None
    validator = ""
    if engine == "aria2" and (executable or not material and shutil.which("yt-dlp")):
        content_type, validator = http_metadata(url)
        if not material and content_type in {
            "application/vnd.apple.mpegurl", "application/x-mpegurl",
            "audio/mpegurl", "audio/x-mpegurl", "application/dash+xml",
        }:
            engine, executable = "yt-dlp", shutil.which("yt-dlp")
    if not executable:
        return None
    if engine == "yt-dlp" and not urlsplit(url).path.lower().endswith(".mpd") and encrypted_hls(url):
        return None
    # Scope resumable state to the exact URL so files and renditions cannot mix.
    key = hashlib.sha256(f"{engine}:{url}".encode("utf-8")).hexdigest()[:24]
    directory = parts_dir(destination) / key
    directory.mkdir(parents=True, exist_ok=True)
    if engine == "aria2":
        return aria2_file(executable, url, directory / "payload", connections, progress, validator)
    try:
        return hls_file(executable, url, directory, connections, progress, ffmpeg)
    except ValueError:
        # FFmpeg can read some playlists whose fragments yt-dlp cannot assemble.
        return None


def clear_parts(destination):
    directory = parts_dir(destination)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
