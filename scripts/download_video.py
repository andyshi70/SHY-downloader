#!/usr/bin/env python3
"""SHY-downloader: Universal video downloader.

Downloads videos from 15+ platforms with optional ASR transcription,
metadata extraction, and batch follow capabilities.

Platforms:
  - Complex (dedicated providers): 抖音(Douyin), 小红书(Xiaohongshu),
    微信视频号(WeChat Channels), YouTube, Bilibili
  - yt-dlp (thin wrappers): Vimeo, X/Twitter, TikTok, Instagram, Facebook
  - Direct: mp4/webm/mov/mkv/m4v/flv/ogv URLs
  - Stream: m3u8/mpd URLs
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import detect_provider
from asr import run_asr

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_OUT_ROOT = Path.home() / "Desktop" / "av" / "chaijie"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".flv", ".ogv"}
STREAM_EXT = {".m3u8", ".mpd"}

# ── Helpers ──────────────────────────────────────────────────────────────────

def slugify(text: str, fallback: str = "video", max_len: int = 60) -> str:
    v = re.sub(r"[^\w.-]+", "-", str(text), flags=re.U).strip("-._")
    v = re.sub(r"-{2,}", "-", v)
    return v[:max_len].strip("-._") or fallback

def make_run_dir(out_root: Path, title: str) -> Path:
    d = dt.date.today().isoformat()
    base = out_root / f"{d}-{slugify(title or 'video')}"
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return base
    for i in range(2, 1000):
        c = out_root / f"{d}-{slugify(title or 'video')}-{i:02d}"
        if not c.exists():
            c.mkdir(parents=True, exist_ok=True)
            return c
    raise RuntimeError(f"Cannot create unique dir under {out_root}")

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        c = path.with_name(f"{path.stem}-{i:02d}{path.suffix}")
        if not c.exists():
            return c
    raise RuntimeError(f"Cannot create unique filename for {path}")

def find_ytdlp() -> str | None:
    return shutil.which("yt-dlp")

def classify_url(url: str) -> str:
    p = urlparse(url)
    ext = Path(p.path).suffix.lower()
    if ext in VIDEO_EXT:
        return "direct"
    if ext in STREAM_EXT:
        return "stream"
    return "platform"

def size_text(size) -> str:
    if not size:
        return ""
    size = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size} B"

# ── Direct download (with resume) ────────────────────────────────────────────

def download_direct(session, url, out_dir, *, max_mb=2000, timeout=120):
    """Download a direct video URL with .part resume support."""
    record = {"url": url, "status": "failed", "files": [], "bytes": 0, "note": ""}
    partial = None
    try:
        headers = {"User-Agent": DEFAULT_UA, "Referer": url}
        resp = session.get(url, timeout=timeout, stream=True, headers=headers)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "").lower()
        if "text/html" in ct or "application/json" in ct:
            record["note"] = f"unexpected-content-type:{ct.split(';')[0]}"
            return record

        limit = int(max_mb * 1024 * 1024)
        length = resp.headers.get("Content-Length")
        if length:
            try:
                if int(length) > limit:
                    record["note"] = f"video-larger-than-{max_mb:g}MB"
                    return record
            except ValueError:
                pass

        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if ext not in VIDEO_EXT:
            ext = ".mp4"
        stem = Path(parsed.path).stem or "video"
        filename = f"{stem}{ext}"
        target = unique_path(out_dir / filename)
        partial = Path(str(target) + ".part")

        # Resume support
        resume_from = partial.stat().st_size if partial.exists() else 0
        mode = "wb"
        expected = 0
        if resume_from:
            resp.close()
            rh = dict(headers)
            rh["Range"] = f"bytes={resume_from}-"
            resp = session.get(url, timeout=timeout, stream=True, headers=rh)
            resp.raise_for_status()
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range", "")
                m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", cr)
                if m and int(m.group(1)) == resume_from:
                    expected = int(m.group(3))
                    mode = "ab"

        written = resume_from
        with open(partial, mode) as f:
            for chunk in resp.iter_content(1024 * 16):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    f.close()
                    partial.unlink(missing_ok=True)
                    record["note"] = f"video-larger-than-{max_mb:g}MB"
                    return record
                f.write(chunk)

        if expected and written != expected:
            record["note"] = f"incomplete:{written}!={expected}"
            return record
        partial.replace(target)
        record["status"] = "ok"
        record["files"] = [str(target)]
        record["bytes"] = written
        return record
    except Exception as exc:
        record["note"] = str(exc)[:300]
        return record

# ── yt-dlp download (with enhanced features) ────────────────────────────────

def ytdlp_download(
    url, out_dir, *, ytdlp, quality="1080", max_mb=2000,
    cookies_file="", browser_cookies="", playlist=False,
    subtitles=False, sub_langs="zh.*,en.*", embed_thumbnail=False,
    download_archive="", timeout=3600,
):
    """Download via yt-dlp with resume, retries, subtitles, and thumbnail support."""
    kind = "stream" if classify_url(url) == "stream" else "platform"
    record = {"url": url, "kind": kind, "platform": "yt-dlp", "status": "failed", "files": [], "bytes": 0, "note": ""}

    if quality == "best":
        fmt = "bv*+ba/b"
    else:
        try:
            h = int(quality)
            fmt = f"bv*[height<={h}]+ba/b[height<={h}]/b"
        except ValueError:
            record["note"] = f"invalid-quality:{quality}"
            return record

    tmpl = str(out_dir / "%(title).150B [%(id)s].%(ext)s")
    cmd = [str(ytdlp), "--ignore-config", "--continue", "--part",
           "--retries", "10", "--fragment-retries", "10",
           "--retry-sleep", "fragment:exp=1:20",
           "--concurrent-fragments", "4", "--no-progress",
           "--print", "after_move:filepath",
           "--merge-output-format", "mp4", "--embed-metadata",
           "--trim-filenames", "150", "--max-filesize", str(int(max_mb * 1024 * 1024)),
           "-f", fmt, "-o", tmpl, "--write-info-json", url]

    if not playlist:
        cmd.insert(1, "--no-playlist")
    else:
        cmd.insert(1, "--yes-playlist")
    if cookies_file:
        cmd[1:1] = ["--cookies", str(Path(cookies_file).expanduser())]
    elif browser_cookies:
        cmd[1:1] = ["--cookies-from-browser", browser_cookies]
    if subtitles:
        cmd[1:1] = ["--write-subs", "--write-auto-subs", "--sub-langs", sub_langs, "--convert-subs", "srt"]
    if embed_thumbnail:
        cmd.insert(1, "--embed-thumbnail")
    if download_archive:
        cmd[1:1] = ["--download-archive", str(Path(download_archive).expanduser())]

    try:
        before = set(str(p) for p in out_dir.iterdir())
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(filter(None, [str(Path(ytdlp).parent), env.get("PATH", "")]))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        printed = [p.strip() for p in (proc.stdout or "").splitlines() if p.strip()]
        after = [str(p) for p in out_dir.iterdir() if str(p) not in before and p.suffix.lower() in VIDEO_EXT]
        files = sorted(set(p for p in printed + after if p))

        if proc.returncode == 0 and files:
            record["status"] = "ok"
            record["files"] = files
            record["bytes"] = sum(Path(f).stat().st_size for f in files)
            return record

        # Check for archive skip
        diag = (proc.stdout or "") + (proc.stderr or "")
        if any(m in diag.lower() for m in ["already been recorded", "already been downloaded"]):
            record["status"] = "skipped"
            record["note"] = "already in archive"
            return record

        tail = [l for l in diag.splitlines() if l.strip()]
        record["note"] = (tail[-1] if tail else f"exit-{proc.returncode}")[:300]
        # Cookie hint
        if not (cookies_file or browser_cookies):
            markers = ["sign in", "login", "cookies", "captcha", "bot", "412", "403", "forbidden"]
            if any(m in record["note"].lower() for m in markers):
                record["note"] += " | 可用 --cookies-file cookies.txt 或 --browser-cookies chrome 重试"
        return record
    except subprocess.TimeoutExpired:
        record["note"] = f"timeout-{timeout}s"
        return record
    except Exception as exc:
        record["note"] = str(exc)[:300]
        return record

# ── YouTube Invidious fallback ───────────────────────────────────────────────

def youtube_video_id(url: str) -> str:
    p = urlparse(url)
    h, path = p.netloc.lower(), p.path.strip("/")
    if "youtu.be" in h:
        return path.split("/")[0] if re.fullmatch(r"[\w-]{11}", path.split("/")[0]) else ""
    if "youtube.com" in h:
        if p.path == "/watch":
            from urllib.parse import parse_qs
            v = (parse_qs(p.query).get("v") or [""])[0]
            return v if re.fullmatch(r"[\w-]{11}", v) else ""
        for prefix in ("shorts/", "embed/", "live/"):
            if path.startswith(prefix):
                v = path.split("/", 1)[1].split("/")[0]
                if re.fullmatch(r"[\w-]{11}", v):
                    return v
    return ""

def invidious_fallback(url, out_dir, *, max_mb=2000, timeout=120, instances=None):
    """Download YouTube 360p via Invidious proxy when yt-dlp fails."""
    record = {"url": url, "kind": "platform-fallback", "platform": "YouTube/Invidious", "status": "failed", "files": [], "bytes": 0, "note": ""}
    vid = youtube_video_id(url)
    if not vid:
        return record
    instances = instances or ("https://inv.thepixora.com",)
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA})
    last_err = ""
    for inst in instances:
        inst = inst.rstrip("/")
        try:
            # Get title
            r = session.get(f"{inst}/api/v1/videos/{vid}", timeout=min(timeout, 30))
            title = r.json().get("title", "") if r.ok else ""

            # Resolve proxy URL
            r = session.get(f"{inst}/latest_version?id={vid}&itag=18&local=true",
                          timeout=min(timeout, 30), allow_redirects=False)
            loc = r.headers.get("Location", "")
            if loc:
                r = session.get(loc, timeout=min(timeout, 30), allow_redirects=False)
                loc = r.headers.get("Location", loc)
            if not loc:
                continue
            proxy_url = loc

            # Probe
            r, data = session.get(proxy_url, headers={"Range": "bytes=0-2047"}, timeout=min(timeout, 30)), b""
            data = r.content
            if r.status_code != 206 or not data[:4] == b"\x00\x00\x00":
                continue
            cr = r.headers.get("Content-Range", "")
            m = re.search(r"/(\d+)$", cr)
            total = int(m.group(1)) if m else 0
            if total <= 0:
                continue
            limit = int(max_mb * 1024 * 1024)
            if total > limit:
                record["note"] = f"larger-than-{max_mb:g}MB"
                return record

            safe = re.sub(r'[\\/:*?"<>|]+', "_", (title or f"youtube-{vid}"))[:80]
            target = unique_path(out_dir / f"{safe}-360p.mp4")
            partial = Path(str(target) + ".part")

            with open(partial, "wb") as f:
                f.write(data)
            written = len(data)
            chunk_size = 1024 * 1024
            while written < total:
                end = min(written + chunk_size - 1, total - 1)
                r = session.get(proxy_url, headers={"Range": f"bytes={written}-{end}"}, timeout=min(timeout, 30))
                if r.status_code != 206:
                    raise RuntimeError(f"range-fail:{r.status_code}")
                with open(partial, "ab") as f:
                    f.write(r.content)
                written += len(r.content)
                if written > limit:
                    partial.unlink(missing_ok=True)
                    record["note"] = f"larger-than-{max_mb:g}MB"
                    return record

            if partial.stat().st_size != total:
                raise RuntimeError(f"size-mismatch:{partial.stat().st_size}!={total}")
            partial.replace(target)
            record["status"] = "ok"
            record["files"] = [str(target)]
            record["bytes"] = total
            record["note"] = f"via {inst} itag=18"
            return record
        except Exception as exc:
            last_err = f"{inst}: {str(exc)[:150]}"
            continue
    record["note"] = f"invidious-failed: {last_err}"
    return record

# ── Report ───────────────────────────────────────────────────────────────────

def write_report(out_dir, urls, records):
    ok = sum(1 for r in records if r["status"] == "ok")
    sk = sum(1 for r in records if r["status"] == "skipped")
    fl = len(records) - ok - sk
    payload = {"created_at": dt.datetime.now().isoformat(timespec="seconds"), "out_dir": str(out_dir), "urls": urls, "records": records}
    (out_dir / "download-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 下载报告", "",
        f"- 输出目录：`{out_dir}`",
        f"- 链接数：{len(records)}",
        f"- 成功：{ok} | 已跳过：{sk} | 失败：{fl}", "",
        "| 状态 | 类型 | 平台 | 文件 | 大小 | 链接 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        files = "<br>".join(f"`{Path(p).name}`" for p in r.get("files", []))
        lines.append(f"| {r['status']} | {r.get('kind','')} | {r.get('platform','')} | {files} | {size_text(r.get('bytes'))} | <{r['url']}> | {r.get('note','')} |")
    (out_dir / "download-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="SHY-downloader: universal video downloader")
    p.add_argument("urls", nargs="*", help="Video URLs")
    p.add_argument("--url-file", action="append", default=[], help="Text file with URLs, one per line")
    p.add_argument("--title", default="", help="Run title for output folder name")
    p.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="Output root directory")
    p.add_argument("--run-dir", default="", help="Reuse existing run directory (resume)")
    p.add_argument("--quality", default="1080", help="Max height or 'best'")
    p.add_argument("--max-video-mb", type=float, default=2000, help="Per-video size limit")
    p.add_argument("--cookies-file", default="", help="Netscape cookies.txt file")
    p.add_argument("--browser-cookies", default="", help="chrome/safari/edge/firefox")
    p.add_argument("--playlist", action="store_true", help="Allow playlist downloads")
    p.add_argument("--no-invidious", action="store_true", help="Disable YouTube Invidious fallback")
    p.add_argument("--subtitles", action="store_true", help="Download subtitles")
    p.add_argument("--sub-langs", default="zh.*,en.*", help="Subtitle languages")
    p.add_argument("--embed-thumbnail", action="store_true", help="Embed thumbnail")
    p.add_argument("--download-archive", default="", help="yt-dlp archive file")
    p.add_argument("--metadata-only", action="store_true", help="Only extract metadata, skip download")
    p.add_argument("--asr", choices=["auto", "siliconflow", "whisper", "none"], default="auto", help="ASR backend")
    p.add_argument("--asr-language", default="auto", help="ASR language")
    p.add_argument("--asr-prompt", default="", help="ASR initial prompt")
    p.add_argument("--timeout", type=int, default=3600, help="yt-dlp timeout")
    args = p.parse_args(argv)

    # Collect URLs
    urls = list(args.urls)
    for f in args.url_file:
        for line in Path(f).expanduser().read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", ";")):
                urls.append(line)
    if not urls:
        print("error: no URLs provided", file=sys.stderr)
        return 1

    out_root = Path(args.out_root).expanduser()
    title = args.title or "shy-download"
    out_dir = Path(args.run_dir).expanduser() if args.run_dir else make_run_dir(out_root, title)
    out_dir.mkdir(parents=True, exist_ok=True)

    ytdlp = find_ytdlp()
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA})

    records = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}", flush=True)
        kind = classify_url(url)

        # 1. Direct URL (with resume)
        if kind == "direct":
            rec = download_direct(session, url, out_dir, max_mb=args.max_video_mb)
            records.append(rec)
            print(f"  -> {rec['status']}{' (' + rec.get('note','') + ')' if rec.get('note') else ''}", flush=True)
            continue

        # 2. Try provider system
        provider = detect_provider(url)
        if provider:
            try:
                result = provider.fetch(url, out_dir, metadata_only=args.metadata_only)
                if result.get("video_path"):
                    rec = {"url": url, "kind": "platform", "platform": provider.PLATFORM, "status": "ok",
                           "files": [result["video_path"]] if result.get("video_path") else [],
                           "bytes": Path(result["video_path"]).stat().st_size if result.get("video_path") else 0,
                           "note": ""}
                else:
                    rec = {"url": url, "kind": "platform", "platform": provider.PLATFORM, "status": "failed",
                           "files": [], "bytes": 0, "note": result.get("error", "no-video")}
                records.append(rec)
                print(f"  -> {rec['status']} ({provider.PLATFORM}){(' ' + rec.get('note','')) if rec.get('note') else ''}", flush=True)
                continue
            except Exception as exc:
                rec = {"url": url, "kind": "platform", "platform": provider.PLATFORM, "status": "failed",
                       "files": [], "bytes": 0, "note": str(exc)[:200]}
                # Fall through to yt-dlp

        # 3. yt-dlp for everything else
        if ytdlp:
            rec = ytdlp_download(url, out_dir, ytdlp=ytdlp, quality=args.quality,
                                 max_mb=args.max_video_mb, cookies_file=args.cookies_file,
                                 browser_cookies=args.browser_cookies, playlist=args.playlist,
                                 subtitles=args.subtitles, sub_langs=args.sub_langs,
                                 embed_thumbnail=args.embed_thumbnail,
                                 download_archive=args.download_archive, timeout=args.timeout)
            # YouTube Invidious fallback
            if rec["status"] != "ok" and not args.no_invidious and youtube_video_id(url) \
               and not (args.cookies_file or args.browser_cookies):
                fb = invidious_fallback(url, out_dir, max_mb=args.max_video_mb)
                if fb["status"] == "ok":
                    fb["note"] = f"yt-dlp: {rec.get('note','')} | {fb.get('note','')}"
                    rec = fb
            records.append(rec)
            print(f"  -> {rec['status']}{' (' + rec.get('note','') + ')' if rec.get('note') else ''}", flush=True)
            continue

        # 4. No yt-dlp, no provider
        rec = {"url": url, "kind": kind, "platform": "unknown", "status": "failed",
               "files": [], "bytes": 0, "note": "yt-dlp-not-found-and-no-provider"}
        records.append(rec)
        print(f"  -> failed (yt-dlp not found, no provider)", flush=True)

    write_report(out_dir, urls, records)

    # ── ASR 默认转文字（除非 --asr none）────────────────────────────────────
    if args.asr != "none":
        for rec in records:
            if rec.get("status") != "ok":
                continue
            for vpath in rec.get("files", []):
                vpath = Path(vpath)
                if vpath.suffix.lower() not in VIDEO_EXT:
                    continue
                if not vpath.exists():
                    continue
                print(f"  [ASR] 转文字: {vpath.name}", flush=True)
                asr_res = run_asr(
                    vpath, out_dir,
                    backend=args.asr,
                    language=args.asr_language,
                    prompt=args.asr_prompt or None,
                )
                if asr_res.get("transcript_path"):
                    rec.setdefault("asr", {}).setdefault("transcript", [])
                    rec["asr"]["transcript"].append(str(asr_res["transcript_path"]))
                    rec.setdefault("files", []).append(str(asr_res["transcript_path"]))
                    print(f"    -> {asr_res['transcript_path']}", flush=True)
                else:
                    print(f"    -> 跳过: {asr_res.get('error', 'no-transcript')}", flush=True)
        write_report(out_dir, urls, records)  # 报告补入 ASR 产物

    ok = sum(1 for r in records if r["status"] == "ok")
    sk = sum(1 for r in records if r["status"] == "skipped")
    fl = len(records) - ok - sk
    print(f"\n输出目录: {out_dir}")
    print(f"成功: {ok}  已跳过: {sk}  失败: {fl}")
    return 0 if ok + sk == len(records) else (1 if ok + sk == 0 else 2)

if __name__ == "__main__":
    raise SystemExit(main())