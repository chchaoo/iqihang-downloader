"""Resolve CC live replays through the learning center's playback flow."""

import json
import os
import shutil
import subprocess
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from download_courses import SITE, api, duration, retry, run_ffmpeg


def cc_request(path, params, playback_token=None):
    url = "https://view.csslcloud.net" + path
    headers = {"Referer": SITE + "/", "Origin": SITE, "User-Agent": "Mozilla/5.0"}
    body = None
    if playback_token:
        url += "?" + urlencode(params)
        headers["X-HD-Token"] = playback_token
    else:
        body = json.dumps(params).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=headers), timeout=30) as response:
        result = json.load(response)
    if not result.get("success") or not result.get("data"):
        error = result.get("error") or {}
        raise ValueError(f"CC 回放接口 {path} 未返回数据（{error.get('code', '未知')}）")
    return result["data"]


def replay_media(token, user_id, course, resource):
    query = urlencode({"liveId": resource["resourceId"]})
    record = api("/api/ark/web/v1/user/course/live/replay?" + query, token=token)
    params = {
        "accountId": "5BBD2F25E0E3EF7D",
        "replayId": record["ccId"],
        "deviceType": "h5-pc",
        "deviceVersion": "3.21.0",
        "tpl": 20,
    }
    login = retry(lambda: cc_request("/replay/user/login", {
        **params, "userName": str(user_id), "userToken": f"{user_id}:{course['id']}",
    }), OSError)
    user = login["user"]
    return retry(lambda: cc_request("/replay/video/play", {
        **params, "replayId": user["replayId"], "terminal": 3,
    }, user["token"]), OSError)


def replay_versions(token, user_id, course, resource):
    media = replay_media(token, user_id, course, resource)
    renditions = media.get("video") or []
    if not renditions:
        raise ValueError("回放接口未返回画质")
    try:
        return [(int(entry["code"]), str(entry.get("desc") or entry["code"]), entry)
                for entry in renditions]
    except (KeyError, TypeError, ValueError):
        raise ValueError("接口返回的回放画质无法比较") from None


def save_replay(ffmpeg, token, user_id, course, resource, destination, progress=None,
                selected_quality=None, acquire=None):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("保存回放需要 FFprobe 核对时长")
    versions = replay_versions(token, user_id, course, resource)
    wanted = max(entry[0] for entry in versions) if selected_quality is None else selected_quality
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part.mp4")
    for rank, _, rendition in versions:
        if rank != wanted:
            continue
        for field in ("primary", "secondary"):
            url = rendition.get(field) or ""
            if url.startswith("//"):
                url = "https:" + url
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            try:
                expected = duration(ffprobe, url)
                local = acquire(url) if acquire else None
                network_options = [] if local else [
                    "-rw_timeout", "30000000", "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_max_retries", "3",
                ]
                if progress:
                    progress(0)
                result = run_ffmpeg([
                    ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-xerror",
                    *network_options, "-i", str(local or url), "-map", "0:v:0",
                    "-map", "0:a:0", "-c", "copy", str(temporary),
                ], progress, expected)
                if result.returncode == 0 and expected > 0 and abs(
                    duration(ffprobe, temporary) - expected
                ) <= 2:
                    os.replace(temporary, destination)
                    return f"已下载直播回放（{rendition.get('desc') or rendition['code']}）"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
    raise ValueError("所选画质回放的线路均未能保存完整视频")

