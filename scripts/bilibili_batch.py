#!/usr/bin/env python3
"""Bilibili batch downloader using yt-dlp with Chrome cookies."""
import json
import os
import re
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path


def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def format_num(n: int) -> str:
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    elif n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def download_video(bvid: str, title: str, output_dir: Path) -> bool:
    """Download a single B站 video using yt-dlp with Chrome cookies."""
    url = f"https://www.bilibili.com/video/{bvid}"
    
    # Safe filename
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:50]
    folder = output_dir / f"douyin-{bvid}"
    folder.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", "chrome",
        "-o", str(folder / "%(title)s.%(ext)s"),
        "--merge-output-format", "mp4",
        "--newline",
        "--no-playlist",
        url,
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        # Check if video file was created
        files = list(folder.glob("*.mp4"))
        if files:
            return True
        return False
    except Exception as e:
        print(f"    ⚠ Error: {e}")
        return False


def generate_report(videos_meta: list, output_dir: Path):
    report_path = output_dir / "下载报告.md"
    
    lines = [
        "# B站批量下载报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"共 {len(videos_meta)} 个视频",
        "",
        "| 序号 | 视频主题 | 播放量 | 收藏数 | 评论数 | 分享数 | 文件大小 |",
        "|------|---------|--------|--------|--------|--------|---------|",
    ]
    
    for i, v in enumerate(videos_meta, 1):
        row = (
            f"| {i} "
            f"| {v.get('title', '')[:40]} "
            f"| {format_num(v.get('views', 0))} "
            f"| {format_num(v.get('favorites', 0))} "
            f"| {format_num(v.get('comments', 0))} "
            f"| {format_num(v.get('shares', 0))} "
            f"| {human_size(v.get('file_size', 0))} |"
        )
        lines.append(row)
    
    lines.append("")
    lines.append("---")
    lines.append(f"下载目录：`{output_dir}`")
    
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main():
    input_path = Path("/tmp/bilibili_videos.json")
    output_dir = Path("/Users/evandy/Desktop/downloads/bilibili-hopiy_046")
    
    with open(input_path, encoding="utf-8") as f:
        raw_videos = json.load(f)
    
    print(f"Loaded {len(raw_videos)} videos")
    
    filtered = [v for v in raw_videos if v.get("views", 0) >= 10000]
    skipped = len(raw_videos) - len(filtered)
    if skipped:
        print(f"Filtered out {skipped} videos below 10000 views threshold")
    
    if not filtered:
        print("No videos match the threshold")
        return 0
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    total = len(filtered)
    
    for i, v in enumerate(filtered):
        bvid = v.get("bvid", "")
        title = v.get("title", "")[:40]
        views = v.get("views", 0)
        
        print(f"[{i+1}/{total}] ▶{format_num(views)} {title}...", end=" ", flush=True)
        
        video_folder = output_dir / f"douyin-{bvid}"
        video_folder.mkdir(parents=True, exist_ok=True)
        
        # Save post_caption.txt
        desc = v.get("description", "") or title
        (video_folder / "post_caption.txt").write_text(desc, encoding="utf-8")
        
        # Save metadata.json
        meta = {
            "platform": "bilibili",
            "source_url": f"https://www.bilibili.com/video/{bvid}",
            "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "bvid": bvid,
            "title": title,
            "description": desc,
            "author": {"name": "hopiy_046"},
            "statistics": {
                "views": v.get("views", 0),
                "favorites": v.get("favorites", 0),
                "comments": v.get("comments", 0),
                "shares": v.get("shares", 0),
            },
            "download": {},
        }
        
        if download_video(bvid, title, output_dir):
            files = list(video_folder.glob("*.mp4"))
            if files:
                video_filename = files[0]
                size = video_filename.stat().st_size
                meta["download"] = {
                    "video_path": str(video_filename),
                    "method": "yt_dlp_chrome_cookies",
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
                })
            else:
                print("❌ No video file found")
                results.append({**v, "file_size": 0, "status": "fail", "folder": str(video_folder)})
        else:
            print("❌ Download failed")
            results.append({**v, "file_size": 0, "status": "fail", "folder": str(video_folder)})
    
    # Generate report
    report_path = generate_report(results, output_dir)
    print(f"\n📊 Report saved: {report_path}")
    
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    fail_count = sum(1 for r in results if r.get("status") == "fail")
    print(f"✅ Success: {ok_count}  ❌ Failed: {fail_count}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
