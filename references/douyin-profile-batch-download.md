# Douyin Profile Batch Download (CDP + Script)

## Overview

When the user provides a **Douyin user profile link** (containing `/user/` or `/share/user/`), the single-video download pipeline cannot handle it. Instead, use this two-step workflow:

## Workflow (Two Steps)

### Step 1: CDP Profile Scraping (Agent Does This)

Use the user's **local Chrome CDP** (`browser_cdp` with `Runtime.evaluate`) to:

a. **Navigate** to the profile page and **inject cookies**:
   - Navigate: `document.location.href = "{profile_url}"`
   - Inject non-httpOnly cookies from `DOUYIN_COOKIE` env var via `document.cookie = "..."`
   - Reload the page
   - Wait 5s for SPA to load

b. **Scroll to load all videos**:
   - Find the scroll container: `document.querySelector('[class*="route-scroll-container"]')`
   - Scroll it to bottom: `container.scrollTop = container.scrollHeight`
   - Wait 3-4s, repeat until `has_more` is false or video count stabilizes

c. **Extract video metadata** from the DOM:
```javascript
(function(){
  var links = document.querySelectorAll('a[href*="/video/"]');
  var seen = {}, results = [];
  for(var l of links) {
    var id = (l.href.match(/\/video\/(\d+)/) || [])[1];
    if(!id || seen[id]) continue;
    seen[id] = true;
    var txt = l.textContent || '';
    // Parse like count from text (prefixed number like "4.6万" or "359")
    var likeMatch = txt.match(/(\d+[\.\d]*万?)/);
    var likeStr = likeMatch ? likeMatch[1] : '0';
    // Convert "4.6万" → 46000, "359" → 359
    var likes = 0;
    if(likeStr.includes('万')) likes = Math.round(parseFloat(likeStr) * 10000);
    else likes = parseInt(likeStr) || 0;
    // Clean title (remove like number prefix and "置顶")
    var title = txt.replace(/^(置顶)?[0-9.]+万?/, '').trim().substring(0,60);
    results.push({aweme_id: id, title: title, likes: likes});
  }
  return JSON.stringify({author: '每日心理学', videos: results});
})()
```

d. **Save the scraped data** to a JSON file (e.g., `/tmp/douyin_videos.json`).

### Step 2: Batch Download + Report (Script Does This)

Run `scripts/douyin_batch.py` with the scraped JSON:

```bash
DOUYIN_COOKIE="..." python3 scripts/douyin_batch.py \
  --input /tmp/douyin_videos.json \
  --output-dir ./downloads/用户名
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--input` | (required) | Path to scraped video list JSON |
| `--output-dir` | `./downloads` | Output directory |
| `--min-likes` | `10000` | Minimum likes threshold |
| `--no-download` | false | Only generate report, skip downloads |

### Output Structure

```
downloads/用户名/
├── 4.6万_7653801195030975665.mp4    # Videos (named by likes + id)
├── 16.0万_7638185265920003451.mp4
├── ...
└── 下载报告.md                       # Summary table
```

### Report Format

The `下载报告.md` contains a markdown table:

```markdown
| 序号 | 视频主题 | 点赞数 | 收藏数 | 评论数 | 分享数 | 文件大小 |
|------|---------|--------|--------|--------|--------|---------|
| 1 | 吃鸡翅测试 | 4.6万 | 0 | 0 | 0 | 1.7MB |
| 2 | 孩子厌恶上学 | 16.0万 | 0 | 0 | 0 | 5.0MB |
```

## Requirements

- **Chrome CDP** must be configured (`browser.cdp_url` in config.yaml) for Step 1
- **DOUYIN_COOKIE** environment variable must contain the user's Douyin login cookies (in HTTP header format)
- The CDP browser must have Douyin cookies injected (non-httpOnly cookies via `document.cookie`) before scraping

## Note on Missing Stats Fields

Douyin's profile page DOM exposes **like counts** but may not expose favorites/收藏数, comments/评论数, and shares/分享数 in the text content reliably. These fields default to `0` in the report. For complete stats, each video would need to be individually fetched via the share page API.