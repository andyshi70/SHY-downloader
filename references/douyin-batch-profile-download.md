# Douyin Batch Profile Download (CDP-based)

## Overview

Downloading ALL videos from a Douyin user profile that meet a criterion (e.g., like count > 10k) requires a multi-step authenticated workflow. This reference documents the complete pipeline.

## Prerequisites

- **User's browser cookies** — the Douyin API requires authentication. Get cookies via the user's browser DevTools.
- **Local Chrome CDP** — `browser.cdp_url: http://localhost:9222` must be configured in `config.yaml`
- **Valid cookie string** saved to `/tmp/douyin_cookies.txt`

## Step 1: Get the sec_user_id from the profile URL

The profile URL looks like: `https://www.douyin.com/user/MS4wLjABAAAA...`

Extract the `MS4w...` segment as the `sec_user_id`.

## Step 2: Verify cookies work

```bash
COOKIE=$(cat /tmp/douyin_cookies.txt)
curl -s "https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=SEC_UID" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ..." \
  -H "Referer: https://www.douyin.com/" \
  -H "Cookie: $COOKIE"
```

Expected: `status_code: 0`, `user.nickname` is present.

## Step 3: Load profile in CDP browser and inject cookies

Use `browser_cdp` with `Runtime.evaluate` (target the Douyin page tab):

```javascript
// Inject non-httpOnly cookies via document.cookie
document.cookie = 'passport_csrf_token=...; domain=.douyin.com; path=/; secure';
// HttpOnly cookies (sessionid, sid_tt, uid_tt, ttwid, odin_tt) CANNOT be set via JS.
// They must come from the user's actual login session in the browser.
```

**Important:** httpOnly cookies (`sessionid`, `sid_tt`, `uid_tt`, `ttwid`, `odin_tt`) cannot be set via `document.cookie`. Only non-httpOnly cookies like `passport_csrf_token` and `__ac_signature` can be injected this way. The page will show a login overlay if httpOnly session cookies are missing.

**Workaround when httpOnly cookies are missing:** Navigate to the user profile page. Even without full login, the page may load publicly visible videos. Use the page's scrollable container to trigger lazy loading:

```javascript
// Scroll the route-scroll-container to load all videos
var c = document.querySelector('[class*="route-scroll-container"]');
c.scrollTop = c.scrollHeight;
// Wait 4-5s, repeat until no new videos load
```

## Step 4: Extract video IDs and like counts

After scrolling loads all videos, extract them from the DOM:

```javascript
var links = document.querySelectorAll('a[href*="/video/"]');
var results = [];
var seen = {};
for (var l of links) {
    var id = (l.href.match(/\/video\/(\d+)/) || [])[1];
    if (!id || seen[id]) continue;
    seen[id] = true;
    var txt = l.textContent || '';
    // Like count is embedded in the link text as a number/万 at the start
    var likeMatch = txt.match(/(\d+[\.\d]*万?)/);
    results.push({
        id: id,
        like: likeMatch ? likeMatch[1] : '',
        desc: txt.replace(/^(置顶)?[0-9.]+万?/, '').trim().substring(0, 60)
    });
}
```

The like count appears as a raw number (e.g., `"5997"`), a 万-suffixed string (e.g., `"4.6万"` = 46,000, `"16.0万"` = 160,000), or empty for sidebar recommendations.

## Step 5: Filter by like count threshold

Parse the `like` field: if it contains `万`, multiply by 10,000. Compare against the threshold (e.g., 10,000).

## Step 6: Download each qualifying video

For each qualifying video ID, extract the actual download URL from the **iesdouyin.com share page** `_ROUTER_DATA` payload, then download. This is more reliable than using the CDP browser to load each video page.

```python
import urllib.request, json

def get_video_url(vid, cookie_str):
    url = f'https://www.iesdouyin.com/share/video/{vid}/'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0...)',
        'Cookie': cookie_str,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode('utf-8', errors='replace')
    
    # Find _ROUTER_DATA JSON
    idx = html.find('_ROUTER_DATA = ')
    start = idx + len('_ROUTER_DATA = ')
    depth = 0
    for i in range(start, len(html)):
        if html[i] == '{': depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0: end = i + 1; break
    
    data = json.loads(html[start:end])
    for k, v in data['loaderData'].items():
        if isinstance(v, dict) and 'videoInfoRes' in v:
            items = v['videoInfoRes']['item_list']
            play_url = items[0]['video']['play_addr']['url_list'][0]
            play_url = play_url.replace('\\u002F', '/').replace('\\/', '/')
            # Remove watermark
            return play_url.replace('/playwm/', '/play/')
    return None

# Download
dl_url = get_video_url(vid, cookie_str)
urllib.request.urlretrieve(dl_url, '/path/to/save.mp4')
```

## Step 7: Summary

Report to the user:
- Total videos found on profile
- How many met the like threshold
- File paths for each downloaded video
- Any failures with error messages

## Pitfalls

- **Like count extraction is heuristic.** The number in the link text is parsed by position (first number-like token). Some videos without like data (sidebar recommendations, ads) will return empty strings.
- **Scrolling may not load all 87+ videos.** The Douyin page loads ~30 per scroll. Scroll 10+ times with 4-5s waits.
- **iesdouyin.com share page rate limits.** Making 18+ requests in rapid succession works but adding delays (0.5-1s) between requests improves reliability.
- **playwm → play conversion may fail for some videos.** If the non-watermark URL returns 403, fall back to the watermarked version.
- **The CDP page target may redirect.** After navigating to a video page, the URL may change. Always verify `window.location.href` before extracting video sources.