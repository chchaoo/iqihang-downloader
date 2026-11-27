"""Resolve and save on-demand course videos."""

import json
import os
import re
import shutil
import subprocess
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request


SITE = "https://www.iqihang.com"


def request(url, *, token=None, payload=None):
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    with urllib.request.urlopen(
        urllib.request.Request(url, data=body, headers=headers), timeout=30
    ) as response:
        return response.read().decode("utf-8-sig")


def retry(operation, errors):
    for delay in (2, 5, 0):
        try:
            return operation()
        except errors as error:
            if not delay or (
                isinstance(error, urllib.error.HTTPError)
                and error.code not in {408, 429, 500, 502, 503, 504}
            ):
                raise
            time.sleep(delay)


def api(path, **kwargs):
    fetch = lambda: request(SITE + path, **kwargs)
    result = json.loads(fetch() if "payload" in kwargs else retry(fetch, OSError))
    if result.get("data") is None:
        raise ValueError(f"爱启航接口未返回数据（状态码 {result.get('code', '未知')}）")
    return result["data"]


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))
    return value[:48].strip(" .") or "未命名"


def videos(nodes, parts=()):
    for node_index, node in enumerate(nodes, 1):
        title = re.sub(r"\.mp4$", "", str(node.get("name") or ""), flags=re.I)
        name = f"{node_index:03d} {safe_name(title)}"
        resources = node.get("resourceList") or []
        children = node.get("children") or []
        for index, resource in enumerate(resources, 1):
            yield parts + (f"{name} [{index:02d}]",), resource
        if not resources and not children:
            yield parts + (name,), {}
        yield from videos(children, parts + (name,))


def playback_url(copy):
    url = copy.get("playurl") or ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlsplit(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def probe_resolution(url):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not url:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-rw_timeout", "8000000",
             "-probesize", "1000000", "-analyzeduration", "1500000",
             "-select_streams", "v", "-show_entries", "stream=width,height",
             "-of", "json", url],
            capture_output=True, text=True, timeout=12, creationflags=HIDE_WINDOW,
        )
        if result.returncode:
            return None
        streams = json.loads(result.stdout).get("streams") or []
        return max((min(stream.get("width", 0), stream.get("height", 0))
                    for stream in streams), default=0) or None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def video_versions(vid):
    query = urllib.parse.urlencode({"vid": vid, "siteid": "A183AC83A2983CCC"})
    response = request(f"https://p.bokecc.com/servlet/getvideofile?{query}").strip()
    if not response.startswith("{"):
        match = re.fullmatch(r"[^()]*\((.*)\)\s*;?", response, re.S)
        if not match:
            raise ValueError("视频接口返回了无法识别的格式")
        response = match[1]
    copies = json.loads(response).get("copies") or []
    if not copies:
        raise ValueError("点播接口未返回画质")

    def quality(copy):
        label = str(copy.get("desp") or "").strip().upper()
        if label in {"原画", "原始画质", "SOURCE", "ORIGINAL"}:
            return float("inf")
        match = re.search(r"(?<!\d)(\d{3,4})\s*P(?=[^A-Z]|$)", label)
        if match:
            return int(match[1])
        match = re.search(r"\b(\d{3,5})\s*[X×]\s*(\d{3,5})\b", label)
        if match:
            return min(int(match[1]), int(match[2]))
        match = re.search(r"(?<!\d)([248])\s*K(?=[^A-Z]|$)", label)
        if match:
            return {2: 1440, 4: 2160, 8: 4320}[int(match[1])]
        try:
            height = int(copy.get("height") or 0)
            width = int(copy.get("width") or height)
            return min(width, height) if width > 0 and height > 0 else None
        except (TypeError, ValueError):
            return None

    try:
        # Higher CC quality codes indicate higher renditions.
        codes = [int(copy["quality"]) for copy in copies]
    except (KeyError, TypeError, ValueError):
        codes = []
    if codes and all(code > 0 for code in codes):
        return [(code, str(copy.get("desp") or code), copy)
                for code, copy in zip(codes, copies)]

    ranked = [(quality(copy), copy) for copy in copies]
    if len(ranked) > 1 and any(rank is None for rank, _ in ranked):
        probed = {}
        for index, (rank, copy) in enumerate(ranked):
            if rank is not None:
                continue
            url = playback_url(copy)
            if url not in probed:
                probed[url] = probe_resolution(url)
            ranked[index] = (probed[url], copy)
        if any(rank is None for rank, _ in ranked):
            raise ValueError("无法确认全部画质的分辨率，请检查 FFprobe 或媒体线路后重试")
    return [(rank or 0, str(copy.get("desp") or "唯一画质"), copy)
            for rank, copy in ranked]


def stream_url(vid, selected_quality=None):
    versions = video_versions(vid)
    rank = max(entry[0] for entry in versions) if selected_quality is None else selected_quality
    best = [copy for value, _, copy in versions if value == rank]
    for copy in best:
        url = playback_url(copy)
        if url:
            return url
    raise ValueError("所选画质没有有效的点播地址")


def duration(ffprobe, source, timeout=90):
    result = subprocess.run(
        [ffprobe, "-v", "error", "-rw_timeout", "30000000",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, timeout=timeout, creationflags=HIDE_WINDOW,
    )
    if result.returncode:
        raise ValueError("FFprobe 无法读取视频时长")
    return float(result.stdout.strip())


HIDE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_ffmpeg(command, progress=None, expected=0):
    if progress is None or not expected:
        return subprocess.run(command, stderr=subprocess.PIPE, creationflags=HIDE_WINDOW)
    command = [command[0], "-progress", "pipe:1", *command[1:]]
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                              creationflags=HIDE_WINDOW) as process:
            for line in process.stdout:
                if line.startswith(b"out_time_us="):
                    value = line.partition(b"=")[2].strip()
                    if value.isdigit():
                        progress(min(0.99, int(value) / 1_000_000 / expected))
            code = process.wait()
        errors.seek(0, os.SEEK_END)
        errors.seek(max(0, errors.tell() - 16_384))
        return subprocess.CompletedProcess(command, code, stderr=errors.read())


def fetch_video(ffmpeg, vid, destination, progress=None, selected_quality=None, acquire=None):
    url = stream_url(vid, selected_quality)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part.mp4")
    local = acquire(url) if acquire else None
    source = local or url
    input_options = [] if local else [
        "-rw_timeout", "30000000", "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1", "-reconnect_on_http_error", "408,429,5xx",
        "-reconnect_max_retries", "3", "-reconnect_delay_total_max", "15",
    ]
    if not local and urllib.parse.urlsplit(url).path.lower().endswith(".m3u8"):
        input_options += ["-f", "hls", "-seg_max_retry", "3"]
    options = input_options + [
        "-i", str(source), "-map", "0:v:0",
        "-map", "0:a?", "-c", "copy", str(temporary),
    ]
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    ffprobe = shutil.which("ffprobe")
    expected = 0
    if progress and ffprobe:
        try:
            expected = max(0, duration(ffprobe, source, timeout=15))
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
    process = run_ffmpeg(command + ["-xerror"] + options, progress, expected)
    if process.returncode and ffprobe and b"corrupt input packet" in process.stderr:
        expected = expected or duration(ffprobe, source)
        if progress:
            progress(0)
        process = run_ffmpeg(command + options, progress, expected)
        if not process.returncode:
            actual = duration(ffprobe, temporary)
            if abs(actual - expected) > 2:
                raise ValueError(f"下载时长 {actual:.1f} 秒，源视频 {expected:.1f} 秒")
    if process.returncode or not temporary.is_file() or not temporary.stat().st_size:
        stderr = process.stderr.decode("utf-8", errors="replace")
        stderr = re.sub(r'https?://[^\s\x27"<>]+', "[媒体地址]", stderr)
        detail = " ".join(stderr.splitlines()[-3:])[:500]
        raise ValueError(f"FFmpeg 退出码 {process.returncode}。{detail}")
    os.replace(temporary, destination)
    return "已下载"

