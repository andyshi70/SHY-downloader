# Xiaohongshu Status & Debugging Guide

**Updated: 2026-07-18** — Provider is now WORKING via XHS-Downloader approach (httpx HTTP/2 + __INITIAL_STATE__ parsing).

## Current Implementation

The provider uses `scripts/providers/xiaohongshu.py`:
1. Fetch note page via `httpx` with HTTP/2 (mandatory)
2. Extract `window.__INITIAL_STATE__` from HTML
3. Parse JS values → JSON-safe (`undefined→null`, `Infinity→"Infinity"`, `NaN→0`)
4. Navigate nested keys to find note data
5. Extract video URLs from `video.media.stream.h264/h265` or `consumer.originVideoKey`

## Known Issues & Fixes

### Issue 1: `__INITIAL_STATE__` parse failure
**Symptom**: "Could not find __INITIAL_STATE__ in page"
**Cause**: Regex `.*?` fails because the script tag content spans multiple lines and may contain nested braces. Also, YAML parser rejects JS `undefined`.
**Fix**: Use brace-walking to extract complete JSON block. Convert JS values before parsing.

### Issue 2: Cloud CDP IP blocked
**Symptom**: Page loads but returns empty `__INITIAL_STATE__` or security block page
**Cause**: Cloud proxy IPs are flagged by Xiaohongshu anti-scraping
**Fix**: Must use local Chrome profile or residential IP. Cookie alone doesn't help if IP is blocked.

### Issue 3: Missing xsec_token
**Symptom**: Redirects to 404 page
**Cause**: URL lacks the required `?xsec_token=XXX` parameter
**Fix**: Get share link from Xiaohongshu app (Share → Copy Link includes token)

### Issue 4: Non-video notes
**Symptom**: Returns "This is an image-only note, no video to download"
**Cause**: Note type is `normal` (image carousel), not `video`
**Fix**: This is expected behavior. Metadata still saved to `metadata.json` + `post_caption.txt`.

## Reference: XHS-Downloader Project

The implementation is based on [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader):
- Key file: `source/application/video.py` — video URL extraction logic
- Key file: `source/application/explore.py` — metadata extraction
- Key file: `source/expansion/converter.py` — `__INITIAL_STATE__` parsing with YAML_ILLEGAL filter

## Testing Notes

- Test with real share links containing `xsec_token`
- Image-only notes (like comedy/meme posts) will return success=true with `image_urls`
- Video notes return `video_url` and download MP4 automatically
