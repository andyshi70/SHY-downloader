"""WeChat Channels (视频号) video downloader provider.

Two modes:
1. API mode (default) — uses Tencent Yuanbao API to parse share URL, 
   gets playable video URL + metadata. No proxy needed.
2. Proxy mode — uses the built Go binary (wx_channels_download) as a subprocess
   for downloading + decrypting encrypted videos.

The API mode is the primary route. It requires the WECHAT_YUANBAO_COOKIE
environment variable to be set with a valid Yuanbao session cookie.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import struct
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

PLATFORM = "wechat_channels"

# ── ISAAC64 Decryption (ported from Go pkg/decrypt/decrypt.go) ──────────────

GOLDEN_RATIO = 0x9E3779B97F4A7C13


class RandCtx64:
    """ISAAC-64 pseudo-random number generator context."""

    def __init__(self, enc_key: int):
        self.rand_cnt = 255
        self.seed = [0] * 256
        self.mm = [0] * 256
        self.aa = 0
        self.bb = 0
        self.cc = 0
        self._rand64_init(enc_key)

    def _mix(self, a, b, c, d, e, f, g, h):
        a = (a - e) & 0xFFFFFFFFFFFFFFFF
        f = (f ^ (h >> 9)) & 0xFFFFFFFFFFFFFFFF
        h = (h + a) & 0xFFFFFFFFFFFFFFFF
        b = (b - f) & 0xFFFFFFFFFFFFFFFF
        g = (g ^ (a << 9)) & 0xFFFFFFFFFFFFFFFF
        a = (a + b) & 0xFFFFFFFFFFFFFFFF
        c = (c - g) & 0xFFFFFFFFFFFFFFFF
        h = (h ^ (b >> 23)) & 0xFFFFFFFFFFFFFFFF
        b = (b + c) & 0xFFFFFFFFFFFFFFFF
        d = (d - h) & 0xFFFFFFFFFFFFFFFF
        a = (a ^ (c << 15)) & 0xFFFFFFFFFFFFFFFF
        c = (c + d) & 0xFFFFFFFFFFFFFFFF
        e = (e - a) & 0xFFFFFFFFFFFFFFFF
        b = (b ^ (d >> 14)) & 0xFFFFFFFFFFFFFFFF
        d = (d + e) & 0xFFFFFFFFFFFFFFFF
        f = (f - b) & 0xFFFFFFFFFFFFFFFF
        c = (c ^ (e << 20)) & 0xFFFFFFFFFFFFFFFF
        e = (e + f) & 0xFFFFFFFFFFFFFFFF
        g = (g - c) & 0xFFFFFFFFFFFFFFFF
        d = (d ^ (f >> 17)) & 0xFFFFFFFFFFFFFFFF
        f = (f + g) & 0xFFFFFFFFFFFFFFFF
        h = (h - d) & 0xFFFFFFFFFFFFFFFF
        e = (e ^ (g << 14)) & 0xFFFFFFFFFFFFFFFF
        g = (g + h) & 0xFFFFFFFFFFFFFFFF
        return a, b, c, d, e, f, g, h

    def _isaac64(self):
        self.cc = (self.cc + 1) & 0xFFFFFFFFFFFFFFFF
        self.bb = (self.bb + self.cc) & 0xFFFFFFFFFFFFFFFF
        for i in range(256):
            r = i % 4
            if r == 0:
                self.aa = (~(self.aa ^ (self.aa << 21))) & 0xFFFFFFFFFFFFFFFF
            elif r == 1:
                self.aa ^= self.aa >> 5
            elif r == 2:
                self.aa ^= self.aa << 12
            elif r == 3:
                self.aa ^= self.aa >> 33
            self.aa = (self.aa + self.mm[(i + 128) % 256]) & 0xFFFFFFFFFFFFFFFF
            x = self.mm[i]
            y = (self.mm[(x >> 3) % 256] + self.aa + self.bb) & 0xFFFFFFFFFFFFFFFF
            self.mm[i] = y
            self.bb = (self.mm[(y >> 11) % 256] + x) & 0xFFFFFFFFFFFFFFFF
            self.seed[i] = self.bb

    def _rand64_init(self, enc_key: int):
        a = b = c = d = e = f = g = h = GOLDEN_RATIO
        self.seed[0] = enc_key
        for i in range(1, 256):
            self.seed[i] = 0
        for _ in range(4):
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)
        for i in range(0, 256, 8):
            a = (a + self.seed[i]) & 0xFFFFFFFFFFFFFFFF
            b = (b + self.seed[i + 1]) & 0xFFFFFFFFFFFFFFFF
            c = (c + self.seed[i + 2]) & 0xFFFFFFFFFFFFFFFF
            d = (d + self.seed[i + 3]) & 0xFFFFFFFFFFFFFFFF
            e = (e + self.seed[i + 4]) & 0xFFFFFFFFFFFFFFFF
            f = (f + self.seed[i + 5]) & 0xFFFFFFFFFFFFFFFF
            g = (g + self.seed[i + 6]) & 0xFFFFFFFFFFFFFFFF
            h = (h + self.seed[i + 7]) & 0xFFFFFFFFFFFFFFFF
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a
            self.mm[i + 1] = b
            self.mm[i + 2] = c
            self.mm[i + 3] = d
            self.mm[i + 4] = e
            self.mm[i + 5] = f
            self.mm[i + 6] = g
            self.mm[i + 7] = h
        for i in range(0, 256, 8):
            a = (a + self.mm[i]) & 0xFFFFFFFFFFFFFFFF
            b = (b + self.mm[i + 1]) & 0xFFFFFFFFFFFFFFFF
            c = (c + self.mm[i + 2]) & 0xFFFFFFFFFFFFFFFF
            d = (d + self.mm[i + 3]) & 0xFFFFFFFFFFFFFFFF
            e = (e + self.mm[i + 4]) & 0xFFFFFFFFFFFFFFFF
            f = (f + self.mm[i + 5]) & 0xFFFFFFFFFFFFFFFF
            g = (g + self.mm[i + 6]) & 0xFFFFFFFFFFFFFFFF
            h = (h + self.mm[i + 7]) & 0xFFFFFFFFFFFFFFFF
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a
            self.mm[i + 1] = b
            self.mm[i + 2] = c
            self.mm[i + 3] = d
            self.mm[i + 4] = e
            self.mm[i + 5] = f
            self.mm[i + 6] = g
            self.mm[i + 7] = h
        self._isaac64()

    def random(self) -> int:
        result = self.seed[self.rand_cnt]
        if self.rand_cnt == 0:
            self._isaac64()
            self.rand_cnt = 255
        else:
            self.rand_cnt -= 1
        return result


def decrypt_video_data(data: bytes, enc_len: int, key: int) -> bytes:
    """Decrypt the first enc_len bytes of video data using ISAAC64.

    This is the core decryption algorithm used by 视频号 encrypted videos.
    The encryption only covers the first enc_len bytes (typically 131072 = 128KB).
    """
    if len(data) == 0 or enc_len == 0:
        return data

    out = bytearray(data)
    ctx = RandCtx64(key)

    for i in range(0, min(enc_len, len(data)), 8):
        rand_num = ctx.random()
        ks = struct.pack(">Q", rand_num)
        for j in range(8):
            idx = i + j
            if idx >= enc_len or idx >= len(data):
                break
            out[idx] ^= ks[j]

    return bytes(out)


# ── Yuanbao API ─────────────────────────────────────────────────────────────

YUANBAO_API = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
CHANNELS_API = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"


def _generate_rid() -> str:
    ts = format(int(time.time()), "x")
    rand = "".join(format((int(time.time() * 1000) ^ i) % 16, "x") for i in range(8))
    return f"{ts}-{rand}"


def _build_yuanbao_headers(cookie: str) -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "origin": "https://yuanbao.tencent.com",
        "referer": "https://yuanbao.tencent.com/chat/naQivTmsDa/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "cookie": cookie,
        "x-language": "zh-CN",
        "x-platform": "mac",
        "x-source": "web",
        "x-webversion": "2.69.0",
    }


def _parse_share_url(share_url: str, cookie: str) -> dict:
    """Step 1: Use Yuanbao API to parse a 视频号 share URL.

    Returns the parse result with playable_url, wx_export_id, author, desc, etc.
    """
    payload = json.dumps({
        "type": "video_channel_url",
        "url": share_url,
        "scene": 1,
    })
    resp = requests.post(
        YUANBAO_API,
        data=payload,
        headers=_build_yuanbao_headers(cookie),
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise ValueError(
            f"Yuanbao API error: code={result.get('code')}, msg={result.get('msg')}"
        )
    return result["data"]


def _get_feed_info(export_id: str, general_token: str) -> dict:
    """Step 2: Use channels.weixin.qq.com feed API to get full video info.

    Returns feed info with videoUrl, originVideoUrl, author info, title, etc.
    """
    rid = _generate_rid()
    api_url = (
        f"https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
        f"?_rid={rid}&_pageUrl=https%3A%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    )
    payload = json.dumps({
        "baseReq": {"generalToken": general_token},
        "exportId": export_id,
    })
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": (
            f"https://channels.weixin.qq.com/finder-preview/pages/feed"
            f"?entry_card_type=48&comment_scene=39&appid=0"
            f"&token={general_token}&entry_scene=0&eid={export_id}"
        ),
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
    }
    resp = requests.post(api_url, data=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if result.get("errCode") != 0:
        raise ValueError(
            f"Channels API error: errCode={result.get('errCode')}, errMsg={result.get('errMsg')}"
        )
    return result["data"]


def _clean_video_url(video_url: str) -> str:
    """Clean video URL to only keep encfilekey and token params."""
    parsed = urlparse(video_url)
    qs = parse_qs(parsed.query)
    filekey = qs.get("encfilekey", [None])[0]
    token = qs.get("token", [None])[0]
    if filekey and token:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?encfilekey={filekey}&token={token}"
    return video_url


# ── Online API (zero-config, from z-video-downloader) ─────────────────────────
# Credits: https://github.com/ltaoo/wx_channels_download

WX_CHANNELS_PARSE_API = "https://sph.litao.workers.dev/api/fetch_video_profile"
WX_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

def online_api_fetch(
    url: str,
    output_root: Path,
    *,
    max_video_mb: float = 2000,
    timeout: int = 120,
) -> dict | None:
    """Download 视频号 video via online parsing API (zero-config).

    Uses sph.litao.workers.dev to resolve share links into H.264/H.265
    video URLs, then downloads directly. No cookies, no Go binary needed.
    Returns the same result dict format as fetch(), or None on failure.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": WX_UA})
    try:
        parse_resp = session.post(
            WX_CHANNELS_PARSE_API,
            json={"url": url},
            headers={"Content-Type": "application/json", "User-Agent": WX_UA},
            timeout=min(timeout, 30),
        )
        if parse_resp.status_code != 200:
            logger.info(f"[wechat_channels] online API HTTP {parse_resp.status_code}, skipping")
            return None

        data = parse_resp.json()
        if data.get("errCode") and data.get("errCode") != 0:
            logger.info(f"[wechat_channels] online API errCode={data.get('errCode')}, skipping")
            return None
        if "error" in data:
            logger.info(f"[wechat_channels] online API error: {data['error'][:100]}, skipping")
            return None

        feed_info = (data.get("data") or {}).get("feedInfo") or {}
        author_info = (data.get("data") or {}).get("authorInfo") or {}

        h264_info = feed_info.get("h264VideoInfo") or {}
        h265_info = feed_info.get("h265VideoInfo") or {}
        h264_url = h264_info.get("videoUrl", "").strip()
        h265_url = h265_info.get("videoUrl", "").strip()
        default_url = feed_info.get("videoUrl", "").strip()

        video_urls: list[tuple[str, str]] = []
        if h264_url:
            video_urls.append((h264_url, "H264"))
        if h265_url and h265_url != h264_url:
            video_urls.append((h265_url, "H265"))
        if not video_urls and default_url:
            video_urls.append((default_url, "default"))

        if not video_urls:
            logger.info("[wechat_channels] online API returned no video URLs")
            return None

        description = feed_info.get("description", "").strip()
        nickname = author_info.get("nickname", "").strip()
        title_base = _safe_filename(description[:80] or nickname or "wx-channels-video")

        limit = int(max_video_mb * 1024 * 1024)
        downloaded_files: list[str] = []
        total_bytes = 0

        for video_url, label in video_urls:
            filename = f"{title_base}_{label}.mp4"
            target = output_root / _unique_path(filename)
            partial = Path(str(target) + ".part")

            try:
                resp = session.get(
                    video_url,
                    headers={"User-Agent": WX_UA, "Referer": "https://weixin.qq.com/"},
                    timeout=timeout,
                    stream=True,
                )
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    continue

                length = resp.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > limit:
                            logger.info(f"[wechat_channels] online API: video > {max_video_mb}MB")
                            return None
                    except ValueError:
                        pass

                size = 0
                with open(partial, "wb") as handle:
                    for chunk in resp.iter_content(1024 * 64):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > limit:
                            handle.close()
                            partial.unlink(missing_ok=True)
                            return None
                        handle.write(chunk)

                if size == 0:
                    partial.unlink(missing_ok=True)
                    continue

                partial.replace(target)
                downloaded_files.append(str(target))
                total_bytes += size
            except Exception as exc:
                if partial.exists():
                    partial.unlink(missing_ok=True)
                if not downloaded_files:
                    logger.info(f"[wechat_channels] online API download failed: {str(exc)[:100]}")
                continue

        if downloaded_files:
            output_root.mkdir(parents=True, exist_ok=True)
            # Write metadata
            metadata = {
                "platform": "wechat_channels",
                "title": description,
                "author": nickname,
                "description": description,
                "download_method": "online_api",
                "files": downloaded_files,
                "bytes": total_bytes,
            }
            (output_root / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_root / "post_caption.txt").write_text(description or "", encoding="utf-8")
            return {
                "platform": "wechat_channels",
                "output_dir": str(output_root),
                "video_path": downloaded_files[0],
                "post_caption_path": str(output_root / "post_caption.txt"),
                "metadata_path": str(output_root / "metadata.json"),
                "post_caption": description,
                "author": nickname,
                "download_method": "online_api",
            }

        return None
    except Exception as exc:
        logger.info(f"[wechat_channels] online API exception: {str(exc)[:100]}, falling back")
        return None


def _safe_filename(text: str, fallback: str = "video") -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", (text or "").strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or fallback


def _unique_path(filename: str, out_dir: Path | None = None) -> Path:
    """Simple unique filename generator for the online API path."""
    import datetime as dt
    date = dt.date.today().isoformat()
    return Path(f"{date}-{filename}")


# ── Provider Interface ──────────────────────────────────────────────────────


def supports(url: str) -> bool:
    """Check if this URL is a 视频号 link."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    # Match sph:// protocol or weixin.qq.com/channels.weixin.qq.com domains
    if url.startswith("sph://"):
        return True
    return any(
        domain in host
        for domain in ("channels.weixin.qq.com", "weixin.qq.com", "video.weixin.qq.com")
    )


def fetch(
    url: str,
    output_root: Path,
    *,
    metadata_only: bool = False,
    **options,
) -> dict:
    """Download a 视频号 video and extract metadata.

    Tries three paths in order:
    1. Online API (zero-config, via sph.litao.workers.dev) — no cookies needed
    2. Yuanbao API mode — requires WECHAT_YUANBAO_COOKIE
    3. Proxy mode — requires Go binary (wx_channels_download)
    """
    # Path 1: Try online API first (zero-config)
    if not metadata_only:
        logger.info("[wechat_channels] Trying online API (zero-config)...")
        online_result = online_api_fetch(url, output_root)
        if online_result is not None and online_result.get("video_path"):
            logger.info("[wechat_channels] Online API succeeded!")
            return online_result
        logger.info("[wechat_channels] Online API failed, falling back to next method...")

    cookie = os.environ.get("WECHAT_YUANBAO_COOKIE", "")
    go_binary = os.environ.get("WX_CHANNELS_BINARY", "") or _find_go_binary()
    if not go_binary:
        go_binary = subprocess.run(["find", os.path.expanduser("~/aigit"), "-maxdepth", "2", "-name", "wx_channels_download", "-type", "f"], capture_output=True, text=True, timeout=10).stdout.strip().split("\n")[0] if False else ""

    # Path 3 (early): Go binary proxy mode — try BEFORE raising cookie error.
    # This lets Go binary work even without WECHAT_YUANBAO_COOKIE.
    if not cookie and go_binary:
        logger.info("[wechat_channels] No Yuanbao cookie; trying Go binary proxy mode first...")
        try:
            proxy_result = proxy_fetch(url, output_root, metadata_only=False)
            if proxy_result and proxy_result.get("video_path"):
                return proxy_result
        except Exception as e:
            logger.warning(f"[wechat_channels] Go binary proxy mode failed: {e}")
        # Revert system proxy after failed attempt
        _revert_system_proxy()

    # Step 1: Parse the share URL via Yuanbao API
    if not cookie:
        raise ValueError(
            "WECHAT_YUANBAO_COOKIE environment variable is required. "
            "Get it from browser DevTools (Application > Cookies > yuanbao.tencent.com)."
        )

    logger.info("[wechat_channels] Step 1/2: parsing share URL via Yuanbao API...")
    parse_result = _parse_share_url(url, cookie)
    playable_url = parse_result.get("playable_url", "")
    wx_export_id = parse_result.get("wx_export_id", "")
    author = parse_result.get("author", "")
    author_icon = parse_result.get("author_icon", "")
    cover_url = parse_result.get("cover_url", "")
    desc = parse_result.get("desc", "")

    logger.info(f"[wechat_channels] Got exportId={wx_export_id}, author={author}")

    # Step 2: Get full feed info for video URLs + metadata
    feed_info = {}
    video_url = ""
    origin_video_url = ""
    h264_url = ""
    h265_url = ""

    if playable_url:
        parsed = urlparse(playable_url)
        general_token = parse_qs(parsed.query).get("token", [""])[0]
        eid = parse_qs(parsed.query).get("eid", [""])[0]
        if general_token and eid:
            logger.info("[wechat_channels] Step 2/2: getting feed info...")
            try:
                feed_info = _get_feed_info(eid, general_token)
                fi = feed_info.get("feedInfo", {})
                video_url = fi.get("videoUrl", "")
                origin_video_url = _clean_video_url(fi.get("videoUrl", ""))
                h264_info = fi.get("h264VideoInfo", {})
                h265_info = fi.get("h265VideoInfo", {})
                h264_url = h264_info.get("videoUrl", "")
                h265_url = h265_info.get("videoUrl", "")
                # Update metadata from feed info
                desc = fi.get("description", desc)
                author_info = feed_info.get("authorInfo", {})
                author = author_info.get("nickname", author)
                author_icon = author_info.get("headImgUrl", author_icon)
                cover_url = fi.get("coverUrl", cover_url)
            except Exception as e:
                logger.warning(f"[wechat_channels] Feed API failed: {e}")
                # Continue with Yuanbao data only

    # Build metadata
    metadata = {
        "platform": "wechat_channels",
        "title": desc,
        "author": author,
        "author_icon": author_icon,
        "cover_url": cover_url,
        "description": desc,
        "export_id": wx_export_id,
        "playable_url": playable_url,
        "video_url": video_url,
        "origin_video_url": origin_video_url,
        "h264_url": h264_url,
        "h265_url": h265_url,
        "feed_info": _sanitize_feed_info(feed_info),
    }

    # Extract additional metadata from feed info
    if feed_info:
        fi = feed_info.get("feedInfo", {})
        metadata["likes"] = fi.get("likeCountFmt", "")
        metadata["comments"] = fi.get("commentCountFmt", "")
        metadata["forwards"] = fi.get("forwardCountFmt", "")
        metadata["favorites"] = fi.get("favCountFmt", "")
        metadata["media_type"] = fi.get("mediaType", 0)
        metadata["create_time"] = fi.get("createtime", 0)
        metadata["cover_url"] = fi.get("coverUrl", cover_url)

    if metadata_only:
        return metadata

    # Step 3: Download the video
    # Try URLs in order of preference: playable_url > h264 > h265 > video_url
    download_urls = []
    if playable_url:
        download_urls.append(("playable", playable_url))
    if h264_url:
        download_urls.append(("h264", h264_url))
    if h265_url:
        download_urls.append(("h265", h265_url))
    if origin_video_url and origin_video_url != playable_url:
        download_urls.append(("origin", origin_video_url))
    if video_url and video_url not in {u for _, u in download_urls}:
        download_urls.append(("video", video_url))

    # Try each URL until one works
    video_path = None
    decrypt_key = 0
    enc_limit = 131072  # default 128KB

    for source_type, dl_url in download_urls:
        logger.info(f"[wechat_channels] Trying {source_type} URL...")
        try:
            # Try Go binary first if available
            if go_binary:
                video_path = _download_via_go_binary(
                    go_binary, dl_url, output_root, metadata, decrypt_key
                )
                if video_path:
                    metadata["source_type"] = source_type
                    metadata["download_method"] = "go_binary"
                    break

            # Otherwise use Python requests
            video_path = _download_via_requests(dl_url, output_root, metadata)
            if video_path:
                metadata["source_type"] = source_type
                metadata["download_method"] = "requests"
                break

        except Exception as e:
            logger.warning(f"[wechat_channels] {source_type} download failed: {e}")
            continue

    # Fallback: if API mode got metadata but download failed, try proxy mode
    if not video_path and go_binary:
        logger.info("[wechat_channels] API mode download failed, trying proxy mode fallback...")
        try:
            proxy_result = proxy_fetch(url, output_root, metadata_only=False)
            if proxy_result.get("video_path"):
                video_path = Path(proxy_result["video_path"])
                metadata = proxy_result.get("metadata", metadata)
                metadata["download_method"] = "proxy_fallback"
        except Exception as e:
            logger.warning(f"[wechat_channels] Proxy mode fallback also failed: {e}")

    # Write metadata
    metadata_path = output_root / "metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # Write post caption
    caption_path = output_root / "post_caption.txt"
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(desc or "")

    result = {
        "platform": PLATFORM,
        "item_id": wx_export_id,
        "author": author,
        "title": desc,
        "output_dir": str(output_root),
        "metadata_path": str(metadata_path),
        "caption_path": str(caption_path),
        "metadata": metadata,
    }

    if video_path:
        result["video_path"] = str(video_path)
        result["duration"] = metadata.get("duration", 0)
        result["resolution"] = metadata.get("resolution", "")

    return result


def _sanitize_feed_info(feed_info: dict) -> dict:
    """Remove large binary-friendly fields from feed info for cleaner metadata."""
    if not feed_info:
        return {}
    fi = feed_info.get("feedInfo", {})
    return {
        "description": fi.get("description", ""),
        "mediaType": fi.get("mediaType", 0),
        "likeCountFmt": fi.get("likeCountFmt", ""),
        "commentCountFmt": fi.get("commentCountFmt", ""),
        "forwardCountFmt": fi.get("forwardCountFmt", ""),
        "favCountFmt": fi.get("favCountFmt", ""),
        "createTime": fi.get("createtime", 0),
        "coverUrl": fi.get("coverUrl", ""),
        "author": feed_info.get("authorInfo", {}).get("nickname", ""),
        "authorIcon": feed_info.get("authorInfo", {}).get("headImgUrl", ""),
    }


def _download_via_go_binary(
    go_binary: str,
    video_url: str,
    output_root: Path,
    metadata: dict,
    decrypt_key: int = 0,
) -> Path | None:
    """Download via the Go binary's download command.

    The Go binary handles multi-threaded download + optional decryption.
    """
    if not os.path.isfile(go_binary) and not os.access(go_binary, os.X_OK):
        logger.warning(f"[wechat_channels] Go binary not executable: {go_binary}")
        return None

    filename = _make_filename(metadata.get("title", "video"), ".mp4")
    output_path = output_root / filename

    cmd = [go_binary, "download", "--url", video_url, "--filename", str(output_path)]
    if decrypt_key:
        cmd.extend(["--key", str(decrypt_key)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(output_root),
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"[wechat_channels] Go binary download OK: {output_path}")
            return output_path
        else:
            logger.warning(f"[wechat_channels] Go binary failed: {result.stderr}")
            return None
    except Exception as e:
        logger.warning(f"[wechat_channels] Go binary error: {e}")
        return None


def _download_via_requests(
    video_url: str,
    output_root: Path,
    metadata: dict,
) -> Path | None:
    """Download video using Python requests (simple single-threaded)."""
    filename = _make_filename(metadata.get("title", "video"), ".mp4")
    output_path = output_root / filename

    try:
        resp = requests.get(
            video_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                ),
                "Referer": "https://channels.weixin.qq.com/",
            },
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info(
                f"[wechat_channels] Download via requests OK: {output_path} "
                f"({downloaded}/{total} bytes)"
            )
            return output_path

    except Exception as e:
        logger.warning(f"[wechat_channels] requests download failed: {e}")
        if output_path.exists():
            output_path.unlink()

    return None


# ── Go Binary Discovery ──────────────────────────────────────────────────────


def _find_go_binary() -> str | None:
    """Find the wx_channels_download Go binary.

    Checks WX_CHANNELS_BINARY env var, then searches common locations.
    """
    from_env = os.environ.get("WX_CHANNELS_BINARY", "")
    if from_env:
        if os.path.isfile(from_env) and os.access(from_env, os.X_OK):
            return from_env
        logger.warning(f"[wechat_channels] WX_CHANNELS_BINARY not found: {from_env}")

    candidates = [
        os.path.expanduser("~/aigit/wx_channels_download/wx_channels_download"),
        os.path.expanduser("~/aigit/wx_channels_download/build/wx_channels_download"),
        "wx_channels_download",
        "/usr/local/bin/wx_channels_download",
        "/opt/homebrew/bin/wx_channels_download",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def _get_go_config_path() -> str | None:
    """Find the config.yaml for the Go binary.

    Checks WX_CHANNELS_CONFIG env var, then looks next to the binary.
    """
    from_env = os.environ.get("WX_CHANNELS_CONFIG", "")
    if from_env and os.path.isfile(from_env):
        return from_env

    # Look next to the Go binary
    go_binary = _find_go_binary()
    if go_binary:
        cfg_dir = os.path.dirname(os.path.abspath(go_binary))
        for candidate in [
            os.path.join(cfg_dir, "config.yaml"),
            os.path.join(cfg_dir, "config", "config.yaml"),
            os.path.expanduser("~/aigit/wx_channels_download/config.yaml"),
        ]:
            if os.path.isfile(candidate):
                return candidate

    # Default hard-coded path
    default = os.path.expanduser("~/aigit/wx_channels_download/config.yaml")
    if os.path.isfile(default):
        return default
    return None


# ── Proxy Mode ────────────────────────────────────────────────────────────────

PROXY_API_PORT = 2022  # Go binary's API server port
PROXY_PORT = 2023  # Go binary's interceptor/proxy port
PROXY_BASE_URL = f"http://127.0.0.1:{PROXY_API_PORT}"
_SAVED_PROXY: dict = {}


def _save_system_proxy():
    """Save current system proxy settings (macOS networksetup)."""
    saved = {}
    try:
        for mode in ("webproxy", "socksfirewallproxy"):
            out = subprocess.run(
                ["networksetup", "-get" + mode, "Wi-Fi"],
                capture_output=True, text=True, timeout=5,
            )
            saved[mode] = out.stdout.strip()
    except Exception as e:
        logger.warning(f"[wechat_channels] Could not save proxy state: {e}")
    return saved


def _restore_system_proxy(saved: dict):
    """Restore previously saved system proxy settings."""
    if not saved:
        return
    try:
        for mode, cfg in saved.items():
            if not cfg:
                continue
            lines = cfg.splitlines()
            enabled = any(l.startswith("Enabled:") and "Yes" in l for l in lines)
            server = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Server:")), "")
            port = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Port:")), "")
            cmd = ["networksetup", "-set" + mode, "Wi-Fi", server, port]
            if enabled:
                subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                logger.info(f"[wechat_channels] Restored {mode}: {server}:{port}")
            else:
                subprocess.run(
                    ["networksetup", "-set" + mode, "Wi-Fi", "0.0.0.0", "0"],
                    capture_output=True, text=True, timeout=5,
                )
    except Exception as e:
        logger.warning(f"[wechat_channels] Could not restore proxy: {e}")


def _set_system_proxy():
    """Set macOS system proxy to Go binary's interceptor port."""
    try:
        subprocess.run(
            ["networksetup", "-setwebproxy", "Wi-Fi", "127.0.0.1", str(PROXY_PORT)],
            capture_output=True, text=True, timeout=5,
        )
        subprocess.run(
            ["networksetup", "-setsocksfirewallproxy", "Wi-Fi", "127.0.0.1", str(PROXY_PORT)],
            capture_output=True, text=True, timeout=5,
        )
        logger.info(f"[wechat_channels] System proxy set to 127.0.0.1:{PROXY_PORT}")
    except Exception as e:
        logger.warning(f"[wechat_channels] Could not set proxy: {e}")
        raise


def _revert_system_proxy(saved: dict | None = None):
    """Revert system proxy to saved state (or disable)."""
    if saved is None:
        saved = _SAVED_PROXY
    _restore_system_proxy(saved)


def _start_proxy(
    go_binary: str,
    *,
    config_path: str | None = None,
    port: int = PROXY_PORT,
    api_port: int = PROXY_API_PORT,
) -> subprocess.Popen | None:
    """Start the Go binary proxy as a background subprocess.

    The Go binary's root command starts both the API server (port 2022)
    and the interceptor proxy (port 2023) that captures WeChat Channels
    video traffic.

    Returns the Popen handle, or None on failure.
    """
    cmd = [go_binary]
    cmd.extend(["--port", str(port)])
    if config_path:
        cmd.extend(["--config", config_path])

    logger.info(f"[wechat_channels] Starting proxy: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        return proc
    except Exception as e:
        logger.error(f"[wechat_channels] Failed to start proxy: {e}")
        return None


def _wait_for_proxy(timeout: int = 30) -> bool:
    """Wait for the Go binary's API server to be ready.

    Polls http://127.0.0.1:{api_port}/api/status until it returns
    a valid response or timeout expires.
    """
    status_url = f"{PROXY_BASE_URL}/api/status"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(status_url, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    f"[wechat_channels] Proxy API ready: version={data.get('data', {}).get('version', '?')}"
                )
                return True
        except (requests.ConnectionError, requests.Timeout):
            pass
        time.sleep(0.5)
    return False


def _stop_proxy(proc: subprocess.Popen | None) -> None:
    """Stop the Go binary proxy subprocess gracefully."""
    if proc is None:
        return
    logger.info("[wechat_channels] Stopping proxy...")
    # Try graceful shutdown via SIGTERM first
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
            logger.info("[wechat_channels] Proxy stopped gracefully")
            return
        except subprocess.TimeoutExpired:
            logger.warning("[wechat_channels] Proxy did not stop, sending SIGKILL...")
            proc.kill()
            proc.wait(timeout=5)
            logger.info("[wechat_channels] Proxy killed")


def _get_captured_info(share_url: str) -> dict | None:
    """Query the Go binary's API to get captured video info for a share URL.

    Uses the /api/channels/parse_sph endpoint which takes a share URL
    and returns feed info including video URL, h264/h265 variants, and
    metadata.

    Returns a dict with:
        - video_url: str
        - origin_video_url: str
        - h264_url: str
        - h265_url: str
        - title: str
        - author: str
        - cover_url: str
        - metadata: dict (all feed info)
    Or None on failure.
    """
    parse_url = f"{PROXY_BASE_URL}/api/channels/parse_sph"
    try:
        resp = requests.get(
            parse_url,
            params={"url": share_url},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            logger.warning(
                f"[wechat_channels] parse_sph API error: {result.get('msg', '')}"
            )
            return None

        data = result.get("data", {})
        feed_info = data.get("feedInfo", {})
        author_info = data.get("authorInfo", {})

        video_url = feed_info.get("videoUrl", "")
        origin_video_url = feed_info.get("originVideoUrl", "")
        h264_info = feed_info.get("h264VideoInfo", {})
        h265_info = feed_info.get("h265VideoInfo", {})
        h264_url = h264_info.get("videoUrl", "")
        h265_url = h265_info.get("videoUrl", "")

        return {
            "video_url": video_url,
            "origin_video_url": origin_video_url,
            "h264_url": h264_url,
            "h265_url": h265_url,
            "title": feed_info.get("description", ""),
            "author": author_info.get("nickname", ""),
            "cover_url": feed_info.get("coverUrl", ""),
            "metadata": feed_info,
        }
    except Exception as e:
        logger.warning(f"[wechat_channels] Failed to query captured info: {e}")
        return None


def proxy_fetch(
    url: str,
    output_root: Path,
    *,
    metadata_only: bool = False,
    **options,
) -> dict:
    """Download via Go binary API mode.

    Starts the wx_channels_download binary in API-only mode, uses its
    parse_sph endpoint to get a working video URL (the Go binary's API
    returns a CDN token that works for direct download), then downloads
    the video directly with Python requests.

    This is the recommended mode for downloading 视频号 videos since
    the pure Python Yuanbao API doesn't return a working download token.

    Steps:
    1. Find the Go binary (WX_CHANNELS_BINARY env var or common locations)
    2. Start the binary as a background subprocess (API + proxy)
    3. Wait for the API server to be ready
    4. Query /api/channels/parse_sph to get the video URL with working token
    5. Download the video directly with Python requests
    6. Extract metadata from the API response
    7. Stop the binary
    """
    # Step 1: Find Go binary
    go_binary = _find_go_binary()
    if not go_binary:
        raise RuntimeError(
            "wx_channels_download binary not found. "
            "Set WX_CHANNELS_BINARY env var or compile from "
            "~/aigit/wx_channels_download/"
        )
    config_path = _get_go_config_path()

    # Step 2: Start Go binary with config
    logger.info("[wechat_channels] [proxy] Step 1/5: starting Go binary...")
    proc = _start_proxy(go_binary, config_path=config_path)
    if not proc:
        raise RuntimeError("Failed to start Go binary")

    try:
        # Step 3: Wait for API
        logger.info("[wechat_channels] [proxy] Step 2/5: waiting for API server...")
        if not _wait_for_proxy():
            raise RuntimeError("API server did not become ready in time")

        # Step 4: Get video info with working token
        logger.info("[wechat_channels] [proxy] Step 3/5: getting video URL...")
        captured = _get_captured_info(url)
        if not captured:
            raise RuntimeError("Failed to get video info from API")

        title = captured.get("title", "")
        author = captured.get("author", "")
        logger.info(f"[wechat_channels] [proxy] Got: {author} - {title[:40]}...")

        # Build metadata
        metadata = {
            "platform": "wechat_channels",
            "title": title,
            "author": author,
            "cover_url": captured.get("cover_url", ""),
            "description": title,
            "video_url": captured.get("video_url", ""),
            "origin_video_url": captured.get("origin_video_url", ""),
            "h264_url": captured.get("h264_url", ""),
            "h265_url": captured.get("h265_url", ""),
            "download_method": "proxy_mode",
            "feed_info": captured.get("metadata", {}),
        }

        if metadata_only:
            return metadata

        # Step 5: Download using Python requests (Go binary's download command
        # fails because the CDN doesn't support Range/partial content)
        logger.info("[wechat_channels] [proxy] Step 4/5: downloading video...")
        video_url = (captured.get("h264_url") or captured.get("h265_url")
                     or captured.get("video_url") or captured.get("origin_video_url"))

        video_path = _download_via_requests(video_url, output_root, metadata)
        if not video_path:
            # Fallback: try with Go binary's download (might work for some CDNs)
            video_path = _download_via_go_binary(
                go_binary, video_url, output_root, metadata,
            )

        # Write metadata
        metadata_path = output_root / "metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        # Write post caption
        caption_path = output_root / "post_caption.txt"
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(title or "")

        result = {
            "platform": PLATFORM,
            "item_id": "",
            "author": author,
            "title": title,
            "output_dir": str(output_root),
            "metadata_path": str(metadata_path),
            "caption_path": str(caption_path),
            "metadata": metadata,
        }

        if video_path:
            result["video_path"] = str(video_path)

        return result

    finally:
        # Step 6: Stop Go binary
        logger.info("[wechat_channels] [proxy] Step 5/5: stopping...")
        _stop_proxy(proc)


def _make_filename(title: str, ext: str) -> str:
    """Create a safe filename from the video title."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", title.strip())[:100]
    if not safe:
        safe = "video"
    return f"{safe}{ext}"


# ── Standalone CLI ──────────────────────────────────────────────────────────


def main():
    """CLI entry point for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Download 视频号 video")
    parser.add_argument("url", help="视频号 share URL")
    parser.add_argument("--output-dir", default="downloads", help="Output directory")
    parser.add_argument("--metadata-only", action="store_true", help="Metadata only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    result = fetch(args.url, output_root, metadata_only=args.metadata_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()