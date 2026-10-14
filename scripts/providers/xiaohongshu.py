"""Xiaohongshu (小红书) video downloader provider.

Uses the same approach as https://github.com/JoeanAmier/XHS-Downloader:
1. Fetch the note page via httpx (HTTP/2)
2. Extract window.__INITIAL_STATE__ from the HTML
3. Parse note data for video URLs from video.media.stream.h264/h265

Requires a share link with xsec_token:
https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token={TOKEN}

Without xsec_token the page redirects to 404.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import httpx
except ImportError:
    httpx = None

# Cookie string keys used by XHS-Downloader
WEB_SESSION = r"(?:^|; )web_session=[^;]+"
WEB_ID = r"(?:^|; )webId=[^;]+"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "referer": "https://www.xiaohongshu.com/explore",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
}

PLATFORM = "xiaohongshu"

# Regex patterns for supported URLs
LINK_XHS = re.compile(r"https?://www\.xiaohongshu\.com/explore/(\S+)")
LINK_RN = re.compile(r"https?://www\.rednote\.com/explore/(\S+)")
SHARE_XHS = re.compile(r"https?://www\.xiaohongshu\.com/discovery/item/(\S+)")
SHARE_RN = re.compile(r"https?://www\.rednote\.com/discovery/item/(\S+)")
SHORT = re.compile(r"https?://xhslink\.com/[^\s\"'<>\\^`{|}]+")


def supports(url: str) -> bool:
    """Check if this provider can handle the given URL."""
    host = urlparse(url).netloc.lower()
    return any(d in host for d in ["xiaohongshu.com", "rednote.com", "xhslink.com"])


def extract_note_id(url: str) -> tuple[str | None, dict]:
    """Extract note ID and query params from URL."""
    m = SHORT.search(url)
    if m:
        # Resolve short URL first
        import requests
        r = requests.head(m.group(), allow_redirects=True, timeout=10)
        url = r.url

    m = LINK_XHS.search(url) or SHARE_XHS.search(url) or LINK_RN.search(url) or SHARE_RN.search(url)
    if not m:
        return None, {}

    full_path = m.group(1)
    if "?" in full_path:
        path_part, query_str = full_path.split("?", 1)
        note_id = path_part.rstrip("/")
        params = dict(parse_qs(query_str))
    else:
        note_id = full_path.rstrip("/").split("?")[0]
        params = {}

    return note_id, params


def _cookie_str_to_dict(cookie_str: str) -> dict:
    """Convert cookie string to dict (mimics XHS-Downloader's Manager.cookie_str_to_dict)."""
    from http.cookies import SimpleCookie
    cookie = SimpleCookie()
    cookie.load(cookie_str)
    return {key: morsel.value for key, morsel in cookie.items()}


def clean_cookie(cookie_string: str) -> str:
    """Remove webId and web_session from cookie string (for API calls)."""
    cookie_string = re.sub(WEB_ID, "", cookie_string)
    cookie_string = re.sub(WEB_SESSION, "", cookie_string)
    cookie_string = re.sub(r";\s*$", "", cookie_string)
    cookie_string = re.sub(r";\s*;", ";", cookie_string)
    return cookie_string.strip("; ")


def _extract_initial_state(html: str) -> dict | None:
    """Extract and parse window.__INITIAL_STATE__ from page HTML.
    
    Handles JavaScript-specific values (undefined, Infinity, NaN) by converting
    them to JSON-compatible values before parsing.
    """
    if not html or "__INITIAL_STATE__" not in html:
        return None

    yaml_illegal = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    # Find the assignment: window.__INITIAL_STATE__ = {...};
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if not match:
        return None
    
    start = match.end()
    
    # Walk braces to find matching close
    depth = 0
    end = start
    for i in range(start, len(html)):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    
    if end <= start:
        return None
    
    text = html[start:end]
    text = yaml_illegal.sub("", text)
    
    # Convert JS-specific values to JSON-compatible ones
    # undefined → null, Infinity → 0, NaN → 0
    text = re.sub(r'\bundefined\b', 'null', text)
    text = re.sub(r'\bInfinity\b', '"Infinity"', text)
    text = re.sub(r'\bNaN\b', '0', text)
    
    try:
        import json
        return json.loads(text)
    except Exception:
        return None


def _deep_get(data: dict, keys: list | tuple, default=None):
    """Navigate nested dict/list with index support (like XHS-Downloader.deep_get)."""
    if not data:
        return default
    try:
        for key in keys:
            if key.startswith("[") and key.endswith("]"):
                idx = int(key[1:-1])
                if isinstance(data, dict):
                    data = list(data.values())[idx]
                elif isinstance(data, (list, tuple)):
                    data = data[idx]
                else:
                    return default
            else:
                data = data[key]
        return data
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def _parse_note_data(state: dict) -> dict:
    """Extract note data from __INITIAL_STATE__.

    Tries multiple key paths like XHS-Downloader does:
    - PC: note.noteDetailMap[-1].note
    - Phone: noteData.data.noteData
    """
    pc_keys = ("note", "noteDetailMap", "[-1]", "note")
    phone_keys = ("noteData", "data", "noteData")

    result = _deep_get(state, phone_keys) or _deep_get(state, pc_keys)
    if result and isinstance(result, dict):
        return result

    # Try alternate structure: state["note"]["noteDetailMap"] values
    note_map = _deep_get(state, ("note", "noteDetailMap"))
    if note_map and isinstance(note_map, dict):
        vals = list(note_map.values())
        if vals and isinstance(vals[0], dict):
            return vals[0].get("note", vals[0])

    return {}


def _get_video_url(note_data: dict) -> list[str]:
    """Extract video download URLs from note data."""
    urls = []

    # Method 1: stream URLs
    h264 = note_data.get("video", {}).get("media", {}).get("stream", {}).get("h264", [])
    h265 = note_data.get("video", {}).get("media", {}).get("stream", {}).get("h265", [])

    for stream_list in [h264, h265]:
        if stream_list:
            stream_list.sort(key=lambda x: x.get("height", 0), reverse=True)
            master = stream_list[0].get("masterUrl", "")
            if master:
                try:
                    master = bytes(master, "utf-8").decode("unicode_escape")
                except Exception:
                    pass
                urls.append(master)

    # Method 2: originVideoKey
    if not urls:
        consumer = note_data.get("video", {}).get("consumer", {})
        key = consumer.get("originVideoKey", "")
        if key:
            urls.append(f"https://sns-video-bd.xhscdn.com/{key}")

    return urls


def _get_image_urls(note_data: dict) -> list[str]:
    """Extract image URLs from note data (for image-only notes)."""
    images = []
    for img in note_data.get("image_list", []):
        url = img.get("url_default", img.get("url", ""))
        if url:
            try:
                url = bytes(url, "utf-8").decode("unicode_escape")
            except Exception:
                pass
            images.append(url)
    return images


def fetch(url: str, output_root: Path, *, metadata_only: bool = False, **options) -> dict:
    """Fetch Xiaohongshu note data and optionally download video.

    Args:
        url: Xiaohongshu note/share URL (must contain xsec_token)
        output_root: Directory to save files
        metadata_only: If True, only extract metadata, don't download
        **options: Extra options including 'cookie' for browser cookie string

    Returns:
        dict with metadata, video_url, and file paths
    """
    global httpx
    if httpx is None:
        raise RuntimeError("httpx is required for Xiaohongshu. Install with: pip install httpx httpx[http2]")

    result = {
        "platform": PLATFORM,
        "url": url,
        "success": False,
        "error": None,
        "metadata": {},
        "video_url": None,
        "video_path": None,
    }

    # Step 1: Resolve URL and extract note ID
    note_id, params = extract_note_id(url)
    if not note_id:
        result["error"] = "Could not extract note ID from URL"
        return result

    # Build the full note URL
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={v[0]}" for k, v in params.items())
    note_url = f"https://www.xiaohongshu.com/explore/{note_id}{query}"

    # Step 2: Build headers and cookies
    req_headers = HEADERS.copy()
    req_cookies = {}

    # Use provided cookie if available
    cookie_str = options.get("cookie", "")
    if cookie_str:
        req_cookies = _cookie_str_to_dict(cookie_str)

    # Step 3: Fetch the page using httpx with HTTP/2
    with httpx.Client(http2=True, verify=False, follow_redirects=True, timeout=30) as client:
        resp = client.get(note_url, headers=req_headers, cookies=req_cookies)

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}: {resp.url}"
            return result

        html = resp.text

    # Step 4: Extract __INITIAL_STATE__
    state = _extract_initial_state(html)
    if not state:
        result["error"] = "Could not find __INITIAL_STATE__ in page (URL may need xsec_token)"
        return result

    # Step 5: Parse note data
    note_data = _parse_note_data(state)
    if not note_data:
        result["error"] = "Could not extract note data from __INITIAL_STATE__"
        return result

    # Step 6: Extract metadata
    interact = note_data.get("interactInfo", {})
    user = note_data.get("user", {})

    metadata = {
        "note_id": note_data.get("noteId", note_id),
        "title": note_data.get("title", ""),
        "desc": note_data.get("desc", ""),
        "type": note_data.get("type", ""),
        "liked_count": interact.get("likedCount", 0),
        "collected_count": interact.get("collectedCount", 0),
        "comment_count": interact.get("commentCount", 0),
        "share_count": interact.get("shareCount", 0),
        "author_nickname": user.get("nickname", user.get("nickName", "")),
        "author_id": user.get("userId", ""),
        "time": note_data.get("time", 0),
    }
    result["metadata"] = metadata

    # Step 7: Extract video URL
    video_urls = _get_video_url(note_data)
    if not video_urls:
        # Check if it's an image-only note
        if note_data.get("type") == "normal":
            result["error"] = "This is an image-only note, no video to download"
            result["image_urls"] = _get_image_urls(note_data)
            result["success"] = True  # Metadata extracted successfully
            _save_metadata(output_root, metadata, note_id)
            return result
        result["error"] = "No video URL found in note data"
        return result

    result["video_url"] = video_urls[0]
    result["success"] = True

    if metadata_only:
        _save_metadata(output_root, metadata, note_id)
        return result

    # Step 8: Download the video
    import time

    filename = f"{metadata['author_nickname']}_{metadata['title'][:30]}".replace("/", "_").replace("\\", "_")
    filename = re.sub(r'[^\w\u4e00-\u9fff\-_]', '_', filename)[:100]
    if not filename:
        filename = f"xhs_{note_id}"

    video_path = output_root / f"{filename}.mp4"
    temp_path = output_root / f"{filename}.mp4.tmp"

    import requests
    dl_headers = {"User-Agent": HEADERS["user-agent"]}

    with requests.get(video_urls[0], headers=dl_headers, stream=True, timeout=120) as r:
        if r.status_code != 200:
            result["error"] = f"Download failed: HTTP {r.status_code}"
            _save_metadata(output_root, metadata, note_id)
            return result

        total = int(r.headers.get("content-length", 0))
        written = 0

        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                written += len(chunk)

        # Verify file type by checking magic bytes
        with open(temp_path, "rb") as f:
            header = f.read(12)
        if header[:4] == b"\x00\x00\x00\x1c" or header[4:8] == b"ftyp":
            temp_path.rename(video_path)
        else:
            # Not a valid video, keep as-is but rename
            temp_path.rename(video_path)

        result["video_path"] = str(video_path)
        result["file_size"] = written

    return result


def _save_metadata(output_root: Path, metadata: dict, note_id: str) -> Path:
    """Save metadata.json and post_caption.txt to output directory."""
    output_root.mkdir(parents=True, exist_ok=True)
    
    # Save metadata
    meta_path = output_root / "metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # Save post caption
    caption_path = output_root / "post_caption.txt"
    lines = []
    if metadata.get("title"):
        lines.append(metadata["title"])
    if metadata.get("desc"):
        lines.append(metadata["desc"])
    if metadata.get("author_nickname"):
        lines.append(f"作者: {metadata['author_nickname']}")
    if metadata.get("liked_count"):
        lines.append(f"点赞: {metadata['liked_count']}")
    if metadata.get("collected_count"):
        lines.append(f"收藏: {metadata['collected_count']}")
    if metadata.get("comment_count"):
        lines.append(f"评论: {metadata['comment_count']}")
    if metadata.get("share_count"):
        lines.append(f"分享: {metadata['share_count']}")
    caption_path.write_text("\n".join(lines), encoding="utf-8")
    
    return meta_path
