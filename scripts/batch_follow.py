#!/usr/bin/env python3
"""
Batch creator following — download latest videos from tracked creators.

Pulls from ai-boshu-crawler's best ideas:
- Batch tracking of multiple creators (from JSON or Feishu)
- CDP-based Douyin profile scraping (via existing Chrome CDP, not a separate bridge)
- yt-dlp + Playwright fallback for Bilibili
- Manifest tracking + download archive dedup
- Optional Feishu write-back

Keeps the local video-downloader's strengths:
- Single-video H5 ROUTER_DATA approach (lighter than CDP bridge)
- Provider-based modular design
- ASR pipeline (SiliconFlow + Whisper)
- Per-video folder output structure

Usage:
  # From JSON file (default: ./creators.json)
  python3 batch_follow.py --platform all --videos-per-creator 3

  # Dry run to see what would be downloaded
  python3 batch_follow.py --dry-run

  # Download + generate pending analysis manifest for viral-analysis-pipeline
  python3 batch_follow.py --analyze

  # With Feishu
  python3 batch_follow.py --from-feishu

  # Only Bilibili
  python3 batch_follow.py --platform bili
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ──
ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT.parent
PROVIDERS_DIR = ROOT / "providers"
DEFAULT_ARCHIVE = ROOT / "download-archive.txt"
MANIFEST_DIR = ROOT.parent / "manifests"
DEFAULT_OUTPUT = Path.home() / "Desktop" / "av" / "chaijie"
DEFAULT_CREATORS = ROOT / "creators.json"

# ── UA ──
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

# ── Helpers ──

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ts_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def safe_name(value: str, max_len: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "untitled")[:max_len]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def load_archive(archive_path: Path) -> set:
    """Load download archive: one ID per line."""
    if not archive_path.exists():
        return set()
    return {
        line.strip()
        for line in archive_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def save_archive(archive_path: Path, ids: set) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        "\n".join(sorted(ids)) + "\n", encoding="utf-8"
    )


def run_command(args: list, *, timeout: Optional[int] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command with proper env."""
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\n"
            f"Exit: {result.returncode}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result


# ── Creator Loading ──

def load_creators_from_json(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("creators", [])
    if not isinstance(payload, list):
        raise RuntimeError("creators must be a list or an object with a 'creators' list")
    return payload


def load_creators_from_feishu() -> list:
    """
    Load creators from Feishu Base (creators table).
    Uses lark-cli to query the same table structure as ai-boshu-crawler.
    """
    try:
        import bilibili_following_latest as bili
    except ImportError:
        # Fallback: try to use lark-cli directly
        pass
    
    # Check if feishu-base-config.json exists
    config_path = ROOT / "feishu-base-config.json"
    if not config_path.exists():
        raise RuntimeError(
            "Feishu mode requires feishu-base-config.json in the scripts directory.\n"
            "See creators.example.json for the expected format."
        )
    
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base_token = config.get("base_token", config.get("base-token"))
    table_id = config.get("tables", {}).get("creators", {}).get("table_id")
    profile = config.get("profile", "default")
    
    if not base_token or not table_id:
        raise RuntimeError("feishu-base-config.json missing base_token or tables.creators.table_id")
    
    # Use lark-cli to list records
    result = run_command(
        ["lark-cli", "--profile", profile, "base",
         "+record-list",
         "--base-token", base_token,
         "--table-id", table_id,
         "--limit", "200",
         "--format", "json"],
        timeout=30,
    )
    
    data = json.loads(result.stdout)
    if not data.get("ok"):
        raise RuntimeError(f"lark-cli returned error: {data.get('error', 'unknown')}")
    
    payload = data["data"]
    names = payload["fields"]
    creators = []
    
    for record_id, values in zip(payload["record_id_list"], payload["data"]):
        row = dict(zip(names, values))
        
        # Skip if not tracking
        track_field = _get_row_field(row, ["是否持续跟踪", "抖音持续跟踪", "持续跟踪"])
        if track_field is not True:
            continue
        
        creator = {
            "name": _get_row_field(row, ["博主名称", "name", "名称"]) or f"creator_{record_id}",
            "record_id": record_id,
            "platforms": {},
        }
        
        # Check for Douyin
        douyin_url = _get_row_field(row, ["抖音主页链接", "douyin_url", "抖音链接"])
        douyin_sec_uid = _get_row_field(row, ["抖音SecUID", "douyin_sec_uid"])
        if douyin_url or douyin_sec_uid:
            creator["platforms"]["douyin"] = {
                "url": douyin_url or "",
                "sec_uid": douyin_sec_uid or "",
            }
        
        # Check for Bilibili
        bili_mid = _get_row_field(row, ["B站MID", "bilibili_mid", "mid"])
        bili_url = _get_row_field(row, ["主页链接", "bilibili_url", "B站主页"])
        if bili_mid or bili_url:
            if not bili_mid and bili_url:
                match = re.search(r"space\.bilibili\.com/(\d+)", bili_url)
                bili_mid = match.group(1) if match else ""
            creator["platforms"]["bilibili"] = {
                "mid": str(bili_mid or ""),
                "url": bili_url or f"https://space.bilibili.com/{bili_mid}/video" if bili_mid else "",
            }
        
        if creator["platforms"]:
            creators.append(creator)
    
    return creators


def _get_row_field(row: dict, candidates: list):
    for key in candidates:
        val = row.get(key)
        if val is not None and val != "":
            return val
    return None


# ── Douyin Profile Fetcher (via Playwright + CDP) ──

def fetch_douyin_profile_videos(creator: dict, videos_per_creator: int, cdp_url: str, min_likes: int = 0) -> list:
    """
    Scrape Douyin creator profile via Playwright connected to existing Chrome CDP.
    
    Returns list of dicts: {aweme_id, title, likes, url}
    """
    from playwright.sync_api import sync_playwright
    
    douyin = creator.get("platforms", {}).get("douyin", {})
    profile_url = douyin.get("url", "")
    if not profile_url:
        # Try to construct from sec_uid
        sec_uid = douyin.get("sec_uid", "")
        if sec_uid:
            profile_url = f"https://www.douyin.com/user/{sec_uid}"
    
    if not profile_url:
        raise RuntimeError(f"No Douyin URL for creator: {creator['name']}")
    
    videos = []
    
    with sync_playwright() as pw:
        # Connect to existing Chrome CDP
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to profile
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            
            # Scroll to load videos
            seen_count = 0
            no_new_count = 0
            max_no_new = 3
            
            while no_new_count < max_no_new and len(videos) < videos_per_creator * 2:
                # Extract video links
                new_videos = page.evaluate(f"""
                    (() => {{
                        const links = document.querySelectorAll('a[href*="/video/"]');
                        const seen = new Set();
                        const results = [];
                        for (const l of links) {{
                            const match = l.href.match(/\\/video\\/(\\d+)/);
                            if (!match || seen.has(match[1])) continue;
                            seen.add(match[1]);
                            const txt = l.textContent || '';
                            const likeMatch = txt.match(/(\\d+[\\.\\d]*万?)/);
                            const likeStr = likeMatch ? likeMatch[1] : '0';
                            let likes = 0;
                            if (likeStr.includes('万')) likes = Math.round(parseFloat(likeStr) * 10000);
                            else likes = parseInt(likeStr) || 0;
                            const title = txt.replace(/^(置顶)?[0-9.]+万?/, '').trim().substring(0, 60);
                            results.push({{aweme_id: match[1], title: title, likes: likes}});
                        }}
                        return results;
                    }})()
                """)
                
                if len(new_videos) > seen_count:
                    seen_count = len(new_videos)
                    no_new_count = 0
                    videos = new_videos
                else:
                    no_new_count += 1
                
                # Scroll down
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
            
            # Filter by min likes
            if min_likes > 0:
                videos = [v for v in videos if v["likes"] >= min_likes]
            
            # Take top N
            videos = videos[:videos_per_creator]
            
            # Add full URL
            for v in videos:
                v["url"] = f"https://www.douyin.com/video/{v['aweme_id']}"
                v["creator"] = creator["name"]
            
        finally:
            page.close()
            browser.close()
    
    return videos


# ── Bilibili Profile Fetcher (yt-dlp + Playwright fallback) ──

def fetch_bilibili_profile_videos(creator: dict, videos_per_creator: int, cdp_url: str, min_views: int = 0) -> list:
    """
    Fetch latest Bilibili videos from a creator.
    Primary: yt-dlp --flat-playlist
    Fallback: Playwright search
    """
    bili = creator.get("platforms", {}).get("bilibili", {})
    mid = bili.get("mid", "")
    space_url = bili.get("url", "")
    
    if not mid and space_url:
        match = re.search(r"space\.bilibili\.com/(\d+)", space_url)
        mid = match.group(1) if match else ""
    
    if not mid:
        raise RuntimeError(f"No Bilibili MID for creator: {creator['name']}")
    
    if not space_url:
        space_url = f"https://space.bilibili.com/{mid}/video"
    
    # Try yt-dlp first
    try:
        result = run_command(
            [
                "yt-dlp", "--no-update",
                "--flat-playlist",
                "--playlist-items", f"1:{videos_per_creator}",
                "--dump-json",
                space_url,
            ],
            timeout=60,
        )
        entries = []
        for line in result.stdout.splitlines():
            if line.strip().startswith("{"):
                entries.append(json.loads(line))
        
        videos = []
        for entry in entries:
            bvid = entry.get("id") or entry.get("webpage_url_basename")
            if not bvid:
                continue
            videos.append({
                "bvid": bvid,
                "title": entry.get("title", ""),
                "views": entry.get("view_count", 0),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "creator": creator["name"],
            })
        
        if videos:
            return videos
    
    except Exception as e:
        print(f"  yt-dlp failed for {creator['name']}: {e}")
        print(f"  Falling back to Playwright search...")
    
    # Fallback: Playwright search
    try:
        return _fetch_bilibili_via_playwright(creator, videos_per_creator, cdp_url)
    except Exception as e:
        print(f"  Playwright fallback also failed: {e}")
        return []


def _fetch_bilibili_via_playwright(creator: dict, videos_per_creator: int, cdp_url: str) -> list:
    """Fallback: search Bilibili for creator's latest videos."""
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        
        try:
            search_url = (
                f"https://search.bilibili.com/all?keyword={urllib.parse.quote(creator['name'])}"
                f"&order=pubdate"
            )
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)
            
            videos = page.evaluate(f"""
                (() => {{
                    const results = [];
                    const seen = new Set();
                    const creatorName = {json.dumps(creator['name'])};
                    const limit = {videos_per_creator};
                    for (const a of document.querySelectorAll('a[href*="/video/BV"]')) {{
                        const match = a.href.match(/\\/video\\/(BV[0-9A-Za-z]{{10}})/);
                        if (!match || seen.has(match[1])) continue;
                        const title = (a.textContent || '').trim();
                        if (!title || /^稍后再看/.test(title)) continue;
                        const card = a.closest('.video-list-item, .bili-video-card, .video-item');
                        const cardText = card ? (card.innerText || '').trim() : '';
                        if (!cardText.includes(creatorName)) continue;
                        seen.add(match[1]);
                        results.push({{
                            bvid: match[1],
                            url: `https://www.bilibili.com/video/${{match[1]}}/`,
                            title: title,
                            creator: creatorName,
                        }});
                        if (results.length >= limit) break;
                    }}
                    return results;
                }})()
            """)
            
            return videos[:videos_per_creator]
        finally:
            page.close()
            browser.close()


# ── Download using existing providers ──

def download_video(video_info: dict, platform: str, output_root: Path, archive: set, args) -> Optional[dict]:
    """
    Download a single video using the existing provider.
    Returns result dict or None if skipped.
    """
    video_id = video_info.get("aweme_id") or video_info.get("bvid")
    if not video_id:
        return None
    
    # Check archive
    if video_id in archive:
        return None
    
    # Use the existing download_video.py entrypoint
    video_url = video_info["url"]
    result = run_command(
        [
            sys.executable,
            str(ROOT / "download_video.py"),
            video_url,
            "--output-dir", str(output_root),
            "--asr", args.asr,
        ],
        timeout=300,
        check=False,
    )
    
    if result.returncode == 0:
        # Parse the JSON output from download_video.py
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    
    return {"error": result.stderr[-500:] if result.stderr else "unknown error"}


# ── Main ──

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch follow creators — download latest videos from tracked creators."
    )
    parser.add_argument("--creators", default=str(DEFAULT_CREATORS),
                        help="Path to creators JSON file")
    parser.add_argument("--from-feishu", action="store_true",
                        help="Load creators from Feishu Base instead of JSON")
    parser.add_argument("--platform", choices=["all", "douyin", "bili"], default="all",
                        help="Platform to process (default: all)")
    parser.add_argument("--videos-per-creator", type=int, default=3,
                        help="Number of latest videos per creator (default: 3)")
    parser.add_argument("--min-likes", type=int, default=0,
                        help="Minimum likes filter for Douyin (default: 0)")
    parser.add_argument("--min-views", type=int, default=0,
                        help="Minimum views filter for Bilibili (default: 0)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT),
                        help="Output directory for downloaded videos")
    parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE),
                        help="Download archive file path")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be downloaded without downloading")
    parser.add_argument("--analyze", action="store_true",
                        help="Generate .pending_analysis.json manifest for downstream viral-analysis-pipeline")
    parser.add_argument("--asr", choices=["auto", "none", "whisper", "siliconflow"], default="auto",
                        help="ASR backend (default: auto)")
    parser.add_argument("--cdp-host", default="127.0.0.1",
                        help="Chrome CDP host (default: 127.0.0.1)")
    parser.add_argument("--cdp-port", type=int, default=9222,
                        help="Chrome CDP port (default: 9222)")
    return parser


def main():
    args = build_parser().parse_args()
    output_root = Path(args.output_dir).expanduser().resolve()
    archive_path = Path(args.archive).expanduser().resolve()
    cdp_url = f"http://{args.cdp_host}:{args.cdp_port}"
    
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load creators
    if args.from_feishu:
        creators = load_creators_from_feishu()
    else:
        creators_path = Path(args.creators).expanduser().resolve()
        if not creators_path.exists():
            print(f"Creators file not found: {creators_path}")
            print(f"Create one using the example: {ROOT}/creators.example.json")
            return 1
        creators = load_creators_from_json(creators_path)
    
    print(f"Loaded {len(creators)} creators")
    
    # Load archive
    archive = load_archive(archive_path)
    print(f"Archive has {len(archive)} entries")
    
    # Manifest
    manifest = {
        "started_at": now_str(),
        "platform": args.platform,
        "dry_run": args.dry_run,
        "creators_count": len(creators),
        "archive_count": len(archive),
        "results": [],
        "summary": {"downloaded": 0, "skipped": 0, "failed": 0},
    }
    
    process_douyin = args.platform in ("all", "douyin")
    process_bili = args.platform in ("all", "bili")
    
    for creator in creators:
        creator_result = {
            "name": creator["name"],
            "platforms": {},
        }
        
        # ── Douyin ──
        if process_douyin and "douyin" in creator.get("platforms", {}):
            try:
                print(f"\n[Douyin] {creator['name']}")
                videos = fetch_douyin_profile_videos(
                    creator, args.videos_per_creator, cdp_url, args.min_likes
                )
                print(f"  Found {len(videos)} videos")
                
                platform_result = {"found": len(videos), "downloaded": [], "skipped": [], "failed": []}
                
                for video in videos:
                    vid = video["aweme_id"]
                    if vid in archive:
                        platform_result["skipped"].append({"id": vid, "title": video.get("title", "")})
                        continue
                    
                    if args.dry_run:
                        platform_result["downloaded"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "url": video["url"],
                            "dry_run": True,
                        })
                        print(f"  [dry-run] would download: {video.get('title', vid)}")
                        continue
                    
                    result = download_video(video, "douyin", output_root, archive, args)
                    if result and "error" not in result:
                        archive.add(vid)
                        platform_result["downloaded"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "output_dir": result.get("output_dir"),
                            "video_path": result.get("video_path"),
                        })
                        print(f"  Downloaded: {video.get('title', vid)}")
                    else:
                        platform_result["failed"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "error": str(result.get("error", "unknown")) if result else "no result",
                        })
                        print(f"  Failed: {video.get('title', vid)}")
                
                creator_result["platforms"]["douyin"] = platform_result
                
            except Exception as e:
                print(f"  Error: {e}")
                creator_result["platforms"]["douyin"] = {"error": str(e)}
        
        # ── Bilibili ──
        if process_bili and "bilibili" in creator.get("platforms", {}):
            try:
                print(f"\n[Bilibili] {creator['name']}")
                videos = fetch_bilibili_profile_videos(
                    creator, args.videos_per_creator, cdp_url, args.min_views
                )
                print(f"  Found {len(videos)} videos")
                
                platform_result = {"found": len(videos), "downloaded": [], "skipped": [], "failed": []}
                
                for video in videos:
                    vid = video["bvid"]
                    if vid in archive:
                        platform_result["skipped"].append({"id": vid, "title": video.get("title", "")})
                        continue
                    
                    if args.dry_run:
                        platform_result["downloaded"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "url": video["url"],
                            "dry_run": True,
                        })
                        print(f"  [dry-run] would download: {video.get('title', vid)}")
                        continue
                    
                    result = download_video(video, "bilibili", output_root, archive, args)
                    if result and "error" not in result:
                        archive.add(vid)
                        platform_result["downloaded"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "output_dir": result.get("output_dir"),
                            "video_path": result.get("video_path"),
                        })
                        print(f"  Downloaded: {video.get('title', vid)}")
                    else:
                        platform_result["failed"].append({
                            "id": vid,
                            "title": video.get("title", ""),
                            "error": str(result.get("error", "unknown")) if result else "no result",
                        })
                        print(f"  Failed: {video.get('title', vid)}")
                
                creator_result["platforms"]["bilibili"] = platform_result
                
            except Exception as e:
                print(f"  Error: {e}")
                creator_result["platforms"]["bilibili"] = {"error": str(e)}
        
        manifest["results"].append(creator_result)
    
    # Save archive
    save_archive(archive_path, archive)
    
    # Summary
    total_downloaded = sum(
        len(r.get("platforms", {}).get(p, {}).get("downloaded", []))
        for r in manifest["results"]
        for p in ("douyin", "bilibili")
    )
    total_skipped = sum(
        len(r.get("platforms", {}).get(p, {}).get("skipped", []))
        for r in manifest["results"]
        for p in ("douyin", "bilibili")
    )
    total_failed = sum(
        len(r.get("platforms", {}).get(p, {}).get("failed", []))
        for r in manifest["results"]
        for p in ("douyin", "bilibili")
    )
    
    manifest["summary"] = {
        "downloaded": total_downloaded,
        "skipped": total_skipped,
        "failed": total_failed,
        "dry_run": args.dry_run,
        "archive_count": len(archive),
    }
    manifest["ended_at"] = now_str()
    
    # Save manifest
    manifest_path = MANIFEST_DIR / f"{ts_slug()}-batch-follow.json"
    write_json(manifest_path, manifest)
    
    # ── Save analysis manifest (if --analyze) ──
    if args.analyze and total_downloaded > 0:
        pending = []
        for r in manifest["results"]:
            for p in ("douyin", "bilibili"):
                for d in r.get("platforms", {}).get(p, {}).get("downloaded", []):
                    if d.get("dry_run"):
                        continue
                    out_dir = d.get("output_dir")
                    if not out_dir:
                        continue
                    out_path = Path(out_dir)
                    transcript = None
                    for t in ("transcript.txt", "transcript.whisper.json", "transcript.siliconflow.json"):
                        t_path = out_path / t
                        if t_path.exists():
                            transcript = str(t_path)
                            break
                    pending.append({
                        "id": d["id"],
                        "title": d.get("title", ""),
                        "creator": r["name"],
                        "platform": p,
                        "output_dir": out_dir,
                        "video_path": d.get("video_path"),
                        "transcript_path": transcript,
                        "post_caption_path": str(out_path / "post_caption.txt") if (out_path / "post_caption.txt").exists() else None,
                        "metadata_path": str(out_path / "metadata.json") if (out_path / "metadata.json").exists() else None,
                        "downloaded_at": now_str(),
                    })
        if pending:
            analysis_manifest = output_root / ".pending_analysis.json"
            write_json(analysis_manifest, {
                "source": "batch_follow",
                "generated_at": now_str(),
                "pending": pending,
                "total": len(pending),
            })
            print(f"  Analysis manifest: {analysis_manifest} ({len(pending)} videos pending analysis)")

    print(f"\n{'='*50}")
    print(f"Batch follow complete!")
    print(f"  Downloaded: {total_downloaded}")
    print(f"  Skipped (already in archive): {total_skipped}")
    print(f"  Failed: {total_failed}")
    print(f"  Archive: {archive_path}")
    print(f"  Manifest: {manifest_path}")
    
    if total_failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())