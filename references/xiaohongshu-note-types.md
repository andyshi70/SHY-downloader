# Xiaohongshu Note Types & Provider Behavior

## Three note types

### 1. Video notes (`type: "video"`)
- Contains `video.media.stream.h264` or `h265` arrays with `masterUrl`
- Or `video.consumer.originVideoKey` → CDN URL
- **Provider behavior**: Extracts video URL, downloads MP4, returns `video_path`

### 2. Image-only notes (`type: "normal"`, no video)
- Contains `image_list` array with `url_default` fields
- Common in comedy/meme posts (like "哈哈哈，笑颠了" — 1.4万赞)
- **Provider behavior**: Returns `image_urls` list, `success=true` for metadata extraction
- No video download attempted

### 3. Mixed notes (`type: "video"` + images)
- Video with accompanying photos
- **Provider behavior**: Prioritizes video URL for download, images available in `image_list`

## URL requirements

| Scenario | Works? | Why |
|----------|--------|-----|
| Share link WITH xsec_token | ✅ | Page loads full __INITIAL_STATE__ with note data |
| Direct explore URL without token | ❌ | Redirects to 404 page, no note data in __INITIAL_STATE__ |
| Short link (xhslink.com) | ✅ | Auto-resolved via HEAD request to get full URL |
| Cloud browser IP | ❌ | IP blocked by Xiaohongshu anti-scraping |

## Getting share links

From mobile app: Open note → Share button → Copy Link (includes xsec_token)
From desktop: Right-click note → Copy Link (includes token if logged in)

## Technical notes

- `window.__INITIAL_STATE__` contains JS-specific values: `undefined`, `Infinity`
- Must convert `undefined` → `null` before JSON parsing
- Brace-walking required (regex `.*?` fails across script boundaries)
- httpx HTTP/2 mandatory (requests HTTP/1.1 gets different response)
