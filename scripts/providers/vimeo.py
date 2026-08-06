"""Vimeo provider backed by yt-dlp."""
from __future__ import annotations
import json, re, shutil, subprocess
from pathlib import Path
from time import strftime
from urllib.parse import urlparse

PLATFORM = "vimeo"

def supports(url: str) -> bool:
    return "vimeo.com" in urlparse(url).netloc.lower()

def fetch(url: str, output_root: Path, *, metadata_only: bool = False, **options) -> dict:
    yt = _require_ytdlp()
    metadata = json.loads(_run([yt, "--no-playlist", "--dump-single-json", url]))
    item_id = str(metadata.get("id", "unknown"))
    folder = output_root / f"vimeo-{item_id}"
    folder.mkdir(parents=True, exist_ok=True)
    caption = _caption(metadata)
    (folder / "post_caption.txt").write_text(caption, encoding="utf-8")
    video_path = None
    if not metadata_only:
        video_path = _download(yt, url, folder, _safe_name(metadata.get("title"), item_id))
    meta = _normalize(url, metadata, item_id, caption, video_path, metadata_only)
    (folder / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"platform": PLATFORM, "id": item_id, "output_dir": str(folder), "video_path": str(video_path) if video_path else None, "post_caption_path": str(folder / "post_caption.txt"), "metadata_path": str(folder / "metadata.json"), "post_caption": caption, "author": metadata.get("uploader"), "duration_seconds": metadata.get("duration"), "download_method": "yt_dlp"}

def _require_ytdlp() -> str:
    p = shutil.which("yt-dlp")
    if p: return p
    raise RuntimeError("yt-dlp not found")

def _run(cmd, timeout=240) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode == 0: return r.stdout
    raise RuntimeError("yt-dlp failed: " + (r.stderr.strip() or r.stdout.strip()[:200]))

def _download(yt, url, folder, name):
    out = folder / name
    _run([yt, "--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(out), url], timeout=900)
    if out.exists(): return out
    ms = sorted(folder.glob(f"{out.stem}.*"))
    if ms: return ms[0]
    raise RuntimeError("yt-dlp reported success but no file found")

def _caption(m):
    parts = [m.get("title", "").strip(), m.get("description", "").strip()]
    return "\n\n".join(p for p in parts if p)

def _safe_name(title, fid):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', ' ', (title or fid))[:80].strip() or fid
    return f"{s}-{fid}.mp4"

def _normalize(url, m, fid, caption, vp, mo):
    w, h = m.get("width"), m.get("height")
    return {"platform": PLATFORM, "source_url": url, "fetched_at": strftime("%Y-%m-%dT%H:%M:%S%z"), "id": fid, "caption": caption, "title": m.get("title"), "author": {"nickname": m.get("uploader"), "id": m.get("uploader_id")}, "video": {"width": w, "height": h, "resolution": f"{w}x{h}" if w and h else None, "duration_seconds": m.get("duration"), "view_count": m.get("view_count")}, "download": {"method": "yt_dlp", "video_path": str(vp) if vp else None, "metadata_only": mo}}