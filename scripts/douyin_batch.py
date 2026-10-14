#!/usr/bin/env python3
"""
Douyin profile batch downloader.

Two-step workflow:
  Step 1 (Agent): Use CDP to scrape the profile page → save video_list.json
  Step 2 (Script): Run this script with --input to download + generate report

Usage:
  # Step 1 is done by the agent via CDP (see SKILL.md)
  
  # Step 2: Download all videos and generate report
  DOUYIN_COOKIE="..." python3 scripts/douyin_batch.py \\
    --input /tmp/video_list.json \\
    --output-dir ./downloads/用户名
  
  # Step 2 with like threshold filter
  DOUYIN_COOKIE="..." python3 scripts/douyin_batch.py \\
    --input /tmp/video_list.json \\
    --min-likes 10000 \\
    --output-dir ./downloads/用户名
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Batch download videos from a Douyin profile (requires pre-scraped video list)."
    )
    parser.add_argument("--input", required=True,
                        help="Path to JSON file with scraped video list from CDP")
    parser.add_argument("--output-dir", default="./downloads",
                        help="Root output directory")
    parser.add_argument("--min-likes", type=int, default=10000,
                        help="Minimum likes threshold (default: 10000)")
    parser.add_argument("--no-download", action="store_true",
                        help="Only generate report, skip downloads")
    parser.add_argument("--asr", choices=["none", "whisper"], default="none",
                        help="Run ASR after download (default: none)")
    return parser


def fetch_video_data(vid: str, cookie_str: str = "") -> Optional[dict]:
    """Fetch full video metadata from iesdouyin.com share page ROUTER_DATA.
    
    Returns dict with: play_url, caption, aweme_id, author (nickname, sec_uid),
    video (width, height, duration, ratio), music, create_time, statistics.
    
    Tries without cookies first. Falls back to cookies if ROUTER_DATA is not found.
    """
    url = f"https://www.iesdouyin.com/share/video/{vid}/"
    
    for attempt, use_cookie in enumerate([False, True]):
        if use_cookie and not cookie_str:
            break
        headers = {
            "User-Agent": MOBILE_UA,
            "Referer": "https://www.iesdouyin.com/",
        }
        if use_cookie:
            headers["Cookie"] = cookie_str
        
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == 1:
                print(f"    ⚠ fetch page failed: {e}")
            continue

        idx = html.find("_ROUTER_DATA = ")
        if idx < 0:
            if attempt == 1:
                print("    ⚠ no ROUTER_DATA found")
            continue

        start = idx + len("_ROUTER_DATA = ")
        depth = 0
        end = start
        for i in range(start, len(html)):
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            elif html[i] == "<" and depth == 0:
                break

        try:
            data = json.loads(html[start:end])
        except json.JSONDecodeError:
            if attempt == 1:
                print("    ⚠ JSON parse failed")
            continue

        ld = data.get("loaderData", {})
        for k, v in ld.items():
            if isinstance(v, dict) and "videoInfoRes" in v:
                res = v["videoInfoRes"]
                items = res.get("item_list", [])
                if items:
                    item = items[0]
                    video = item.get("video", {}) or {}
                    addr = video.get("play_addr", {}) or {}
                    urls = addr.get("url_list", [])
                    if not urls:
                        if use_cookie:
                            print("    ⚠ no play URLs in ROUTER_DATA")
                        continue
                    raw_url = urls[0].replace("\\u002F", "/").replace("\\/", "/")
                    play_url = raw_url.replace("/playwm/", "/play/")
                    
                    # Build clean metadata
                    author = item.get("author", {}) or {}
                    statistics = item.get("statistics", {}) or {}
                    music = item.get("music", {}) or {}
                    
                    result = {
                        "play_url": play_url,
                        "aweme_id": item.get("aweme_id", vid),
                        "desc": item.get("desc", ""),
                        "create_time": item.get("create_time", 0),
                        "author": {
                            "nickname": author.get("nickname", ""),
                            "sec_uid": author.get("sec_uid", ""),
                            "unique_id": author.get("unique_id", ""),
                            "signature": author.get("signature", ""),
                        },
                        "video": {
                            "width": video.get("width", 0),
                            "height": video.get("height", 0),
                            "duration": video.get("duration", 0),
                            "ratio": video.get("ratio", ""),
                        },
                        "music": {
                            "mid": music.get("mid", ""),
                            "title": music.get("title", ""),
                            "author": music.get("author", ""),
                            "duration": music.get("duration", 0),
                        },
                        "statistics": {
                            "digg_count": statistics.get("digg_count", 0),
                            "comment_count": statistics.get("comment_count", 0),
                            "collect_count": statistics.get("collect_count", 0),
                            "share_count": statistics.get("share_count", 0),
                        },
                    }
                    return result
        if use_cookie:
            print("    ⚠ video data not found in ROUTER_DATA")
    
    return None


def download_video(url: str, save_path: Path) -> bool:
    """Download video from URL to save_path. Returns True on success."""
    headers = {
        "User-Agent": MOBILE_UA,
        "Referer": "https://www.douyin.com/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
        return True
    except Exception as e:
        print(f"    ⚠ download failed: {e}")
        return False


def human_size(bytes_size: int) -> str:
    if bytes_size < 1024:
        return f"{bytes_size}B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.0f}KB"
    else:
        return f"{bytes_size / (1024 * 1024):.1f}MB"


def format_likes(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def generate_report(videos_meta: list, output_dir: Path):
    """Generate a markdown report table."""
    report_path = output_dir / "下载报告.md"
    
    lines = [
        "# 抖音批量下载报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"共 {len(videos_meta)} 个视频",
        "",
        "| 序号 | 视频主题 | 点赞数 | 收藏数 | 评论数 | 分享数 | 文件大小 |",
        "|------|---------|--------|--------|--------|--------|---------|",
    ]
    
    for i, v in enumerate(videos_meta, 1):
        row = (
            f"| {i} "
            f"| {v.get('title', '')[:40]} "
            f"| {format_likes(v.get('likes', 0))} "
            f"| {format_likes(v.get('favorites', 0))} "
            f"| {v.get('comments', 0)} "
            f"| {v.get('shares', 0)} "
            f"| {human_size(v.get('file_size', 0))} |"
        )
        lines.append(row)
    
    lines.append("")
    lines.append("---")
    lines.append(f"下载目录：`{output_dir}`")
    
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    cookie_str = os.environ.get("DOUYIN_COOKIE", "")
    if cookie_str:
        print("DOUYIN_COOKIE found, will use as fallback for ROUTER_DATA extraction")
    
    # Load video list from JSON
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)
    
    with open(input_path, encoding="utf-8") as f:
        raw_data = json.load(f)
    
    # Support both flat array and {videos: [...]} format
    if isinstance(raw_data, list):
        raw_videos = raw_data
        author = ""
    elif isinstance(raw_data, dict):
        raw_videos = raw_data.get("videos", raw_data.get("items", []))
        author = raw_data.get("author", "")
    else:
        print(f"ERROR: Unexpected JSON format in {args.input}")
        sys.exit(1)
    
    print(f"Loaded {len(raw_videos)} videos from {args.input}")
    
    # Filter by min-likes
    filtered = [v for v in raw_videos if v.get("likes", 0) >= args.min_likes]
    skipped = len(raw_videos) - len(filtered)
    if skipped:
        print(f"Filtered out {skipped} videos below {args.min_likes} likes threshold")
    
    if not filtered:
        print("No videos match the threshold")
        return 0
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    total = len(filtered)
    
    for i, v in enumerate(filtered):
        vid = v.get("aweme_id", "")
        title = v.get("title", "")[:40]
        likes = v.get("likes", 0)
        
        print(f"[{i+1}/{total}] 👍{format_likes(likes)} {title}...", end=" ", flush=True)
        
        # Create per-video folder
        video_folder = output_dir / f"douyin-{vid}"
        video_folder.mkdir(parents=True, exist_ok=True)
        
        # Fetch full metadata from share page
        video_data = fetch_video_data(vid, cookie_str)
        if not video_data:
            print("❌ 获取数据失败")
            results.append({**v, "file_size": 0, "status": "fail", "folder": str(video_folder)})
            continue
        
        # Save post_caption.txt
        caption = video_data.get("desc", v.get("title", ""))
        (video_folder / "post_caption.txt").write_text(caption, encoding="utf-8")
        
        # Save metadata.json
        meta = {
            "platform": "douyin",
            "source_url": f"https://www.douyin.com/video/{vid}",
            "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "aweme_id": vid,
            "desc": caption,
            "create_time": video_data.get("create_time", 0),
            "author": video_data.get("author", {}),
            "video": video_data.get("video", {}),
            "music": video_data.get("music", {}),
            "statistics": video_data.get("statistics", {}),
            "download": {},
        }
        
        # Download video
        play_url = video_data.get("play_url", "")
        video_filename = f"{likes}_{vid}.mp4"
        video_path = video_folder / video_filename
        
        do_download = not args.no_download
        if do_download and video_path.exists() and video_path.stat().st_size > 10000:
            do_download = False
            print(f"⏭ 已存在 ({human_size(video_path.stat().st_size)})")
        
        if do_download:
            if download_video(play_url, video_path):
                size = video_path.stat().st_size
                meta["download"] = {
                    "video_path": str(video_path),
                    "method": "h5_router_data",
                    "ratio": "1080p",
                    "status": "ok",
                }
                (video_folder / "metadata.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"✅ {human_size(size)}")
                results.append({
                    **v,
                    "file_size": size,
                    "status": "ok",
                    "folder": str(video_folder),
                    "favorites": video_data.get("statistics", {}).get("collect_count", 0),
                    "comments": video_data.get("statistics", {}).get("comment_count", 0),
                    "shares": video_data.get("statistics", {}).get("share_count", 0),
                })
            else:
                print("❌ 下载失败")
                results.append({**v, "file_size": 0, "status": "fail", "folder": str(video_folder)})
        else:
            # Already exists or no-download mode
            size = video_path.stat().st_size if video_path.exists() else 0
            meta["download"] = {
                "video_path": str(video_path) if video_path.exists() else None,
                "method": "already_exists" if video_path.exists() else "skipped",
                "ratio": "1080p",
                "status": "ok" if video_path.exists() else "skipped",
            }
            (video_folder / "metadata.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            results.append({
                **v,
                "file_size": size,
                "status": "ok" if video_path.exists() else "skipped",
                "folder": str(video_folder),
                "favorites": video_data.get("statistics", {}).get("collect_count", 0),
                "comments": video_data.get("statistics", {}).get("comment_count", 0),
                "shares": video_data.get("statistics", {}).get("share_count", 0),
            })
            if video_path.exists():
                print(f"⏭ 已存在 ({human_size(size)})")
    
    # Generate report
    report_path = generate_report(results, output_dir)
    print(f"\n📊 报告已生成: {report_path}")
    
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    fail_count = sum(1 for r in results if r.get("status") == "fail")
    print(f"✅ 成功: {ok_count}  ❌ 失败: {fail_count}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())