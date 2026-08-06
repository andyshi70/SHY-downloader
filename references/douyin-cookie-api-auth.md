# Douyin Cookie-Based API Access & Limitations

## Overview

Douyin's internal API requires both authentication cookies AND anti-bot signature headers (X-Khronos + X-Gorgon) for most endpoints. This reference documents which endpoints work with cookies alone and which require the additional signatures.

## Cookie Injection from User

When the user provides cookies (from browser DevTools `copy(document.cookie)`), key auth cookies include:

- `sessionid` + `sid_tt` + `uid_tt` — primary auth tokens
- `ttwid` — device/identity token
- `odin_tt` — user identity
- `__ac_signature` — anti-bot signature
- `passport_csrf_token` — CSRF token

Format cookies for curl as a semicolon-separated string (Cookie header).

## Cookie Injection into CDP Browser

When using `browser_cdp` to access a logged-in page, inject the user's cookies via `document.cookie`:

**Limitation:** `document.cookie` can only set non-httpOnly cookies. Critical auth cookies like `sessionid`, `sid_tt`, `uid_tt`, `ttwid`, `odin_tt` are httpOnly and CANNOT be set via JavaScript. However, `__ac_signature`, `passport_csrf_token`, `UIFID_TEMP`, and `enter_pc_once` are non-httpOnly and can be injected.

**Workaround:** After injecting the non-httpOnly cookies, reload the page. The non-httpOnly cookies (especially `__ac_signature` and `passport_csrf_token`) are sometimes sufficient for the iesdouyin.com share page to return `_ROUTER_DATA`.

**Alternative — pass cookies directly to the download script:**
Instead of injecting into the browser, pass the full cookie string (including httpOnly) via `DOUYIN_COOKIE` env var. Scripts that use `urllib.request` send the Cookie header as-is:

```bash
DOUYIN_COOKIE="sessionid=...; sid_tt=...; ttwid=..." python3 scripts/douyin_batch.py --input data.json
```

This is the **most reliable approach** because `urllib.request` has no httpOnly restrictions.

## Endpoint Status

| Endpoint | Cookies Only | Notes |
|----------|-------------|-------|
| `aweme/v1/web/user/profile/other/` | ✅ Works (status_code: 0) | User profile info, follower count, total likes |
| `aweme/v1/web/aweme/post/` | ❌ Returns status_code: 5 | Requires X-Khronos + X-Gorgon signatures |
| `aweme/v1/web/aweme/feed/` | ❌ Returns status_code: 5 | Requires signatures |
| `aweme/v1/web/aweme/detail/` | ❌ May need signatures | Single video detail |

## X-Gorgon Limitation

The `aweme/v1/web/aweme/post/` endpoint requires `X-Khronos` (Unix timestamp) and `X-Gorgon` (HMAC signature of the request parameters + timestamp). These are generated client-side by Douyin's anti-bot JavaScript. No open-source Python implementation is known to work reliably with current versions.

**Do NOT attempt to reverse-engineer X-Gorgon in-session.** It changes frequently and the effort-to-success ratio is extremely low.

## Workaround: Browser Console Fetch

The most reliable way to get a user's video list is to ask the user to run a fetch from their logged-in browser console:

```javascript
// In browser DevTools Console (logged-in to douyin.com)
copy(JSON.stringify(await (await fetch(
  'https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=SEC_UID_HERE&count=87'
)).json()))
```

The browser's JavaScript runtime automatically generates the X-Gorgon signature because the page's anti-bot script is loaded. The `copy()` function puts the result on the clipboard.

Steps to give the user:
1. Open douyin.com user page (logged in)
2. F12 → Console tab
3. Paste the fetch command with the correct sec_user_id
4. Copy the result back to the chat

## Workaround: CDP Runtime.evaluate (local Chrome)

When the user has `browser.cdp_url: http://localhost:9222` configured, you can use `browser_cdp` with `Runtime.evaluate` to execute JavaScript in the logged-in page context. This works because the page's JavaScript runtime generates the X-Gorgon signature automatically.

**Get video list from profile page:**
```javascript
// Navigate to profile page first
document.location.href = 'https://www.douyin.com/user/SEC_UID';
// Wait 6s, then scroll to load all videos
var c = document.querySelector('[class*="route-scroll-container"]');
for (var i = 0; i < 10; i++) { c.scrollTop = c.scrollHeight; }
// Wait 5s, then extract video links with like counts
```

**Get single video URL:**
```javascript
document.location.href = 'https://www.douyin.com/video/AWEME_ID';
// Wait 8s, then:
var v = document.querySelector('video');
var sources = v.querySelectorAll('source');
// sources[0].src is the CDN URL (v26-web.douyinvod.com)
```

## Workaround: iesdouyin.com Share Page ROUTER_DATA

The `iesdouyin.com/share/video/{id}/` page (mobile share page) embeds `_ROUTER_DATA` with the full video metadata including `play_addr.url_list`. This works with cookies (or sometimes without):

```bash
curl -s "https://www.iesdouyin.com/share/video/AWEME_ID/" \
  -H "User-Agent: Mozilla/5.0 (iPhone; ...)" \
  -H "Cookie: $COOKIE" \
  | python3 -c "
import sys, json
html = sys.stdin.read()
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
        play_url = v['videoInfoRes']['item_list'][0]['video']['play_addr']['url_list'][0]
        print(play_url.replace('\\\\u002F', '/').replace('/playwm/', '/play/'))
"
```

This is the most reliable approach for downloading individual videos — it works even when the CDP browser shows blob URLs.

## User Profile Page Detection

When a `v.douyin.com` short URL redirects to `/share/user/` or `/user/` (contains `sec_uid` not `video/`), it's a user profile, not a video. Symptoms:

- H5 route: `Could not find window._ROUTER_DATA`
- yt-dlp: `Unsupported URL`
- Redirect target: `https://www.iesdouyin.com/share/user/MS4w...`

**Tell the user:** "This link resolved to a user profile page (name: X, videos: Y). I need a specific video link or the user's sec_user_id to proceed."

## User Info API (Cookies Only)

This endpoint works reliably with just cookies. Useful for verifying cookie validity and getting user stats:

```bash
curl -s "https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=SEC_UID" \
  -H "User-Agent: Mozilla/5.0 ..." \
  -H "Referer: https://www.douyin.com/user/SEC_UID" \
  -H "Cookie: $COOKIE"
```

Returns `status_code: 0` on success, with `user.nickname`, `user.total_favorited`, etc.