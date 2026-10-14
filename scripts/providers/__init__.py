"""Provider registry for SHY-downloader."""
from __future__ import annotations
from urllib.parse import urlparse

from . import bilibili, douyin, wechat_channels, xiaohongshu, youtube
from . import vimeo, twitter, tiktok, instagram, facebook

IMPLEMENTED_PROVIDERS = [
    douyin, bilibili, youtube, xiaohongshu, wechat_channels,
    vimeo, twitter, tiktok, instagram, facebook,
]

def detect_provider(url: str):
    for provider in IMPLEMENTED_PROVIDERS:
        if provider.supports(url):
            return provider
    return None