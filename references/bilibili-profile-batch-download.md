# B站主页批量下载（CDP + yt-dlp）— 实战记录

## 概述

当用户提供**B站用户主页链接**（`/space.bilibili.com/{uid}/video`）时，使用CDP爬取+yt-dlp批量下载方案。

## 工作流程

### Step 1 — CDP爬取（Agent做）

1. **导航到主页**：`browser_navigate("https://space.bilibili.com/{uid}/video")`
2. **等待页面加载**：等3-5秒让JS渲染
3. **滚动加载**：滚动到底部触发懒加载，直到加载完所有视频
4. **提取数据**：通过`Runtime.evaluate`从DOM中提取每个视频的：
   - `bvid`（BV号）
   - `title`（标题）
   - `views`（播放量）
   - `favorites`（收藏数）
   - `comments`（评论数）
   - `shares`（分享数）
5. **保存JSON**：输出到 `/tmp/bilibili_videos.json`

### Step 2 — 批量下载（脚本做）

```bash
python3 scripts/bilibili_batch.py \
  --input /tmp/bilibili_videos.json \
  --output-dir ./downloads/用户名 \
  --min-views 10000
```

脚本会：
1. 读入视频列表JSON
2. 按播放量门槛过滤
3. 对每个符合条件的视频调用yt-dlp下载
4. 生成 `下载报告.md`

### Step 3 — 元数据验证（重要！）

**踩坑记录：** CDP从DOM解析的播放量可能不准确（缓存/错误选择器导致）。下载完成后必须用yt-dlp重新获取准确数据验证。

```bash
# 验证单个视频元数据
yt-dlp --cookies-from-browser chrome --dump-json "https://www.bilibili.com/video/BVxxxxxx" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(f'Views: {d[\"view_count\"]}, Likes: {d[\"like_count\"]}')"
```

## DOM结构参考

B站个人空间视频列表页面的DOM结构：

```html
<div class="video-list">
  <div class="bili-video-card">
    <a href="/video/BVxxxxxx" data-bvid="BVxxxxxx">
      <img src="..." alt="视频标题"/>
      <div class="card-bottom">
        <div class="stats">
          <span>播放: 1.2万</span>
          <span>弹幕: 100</span>
          <span>收藏: 500</span>
          <span>分享: 50</span>
        </div>
      </div>
    </a>
  </div>
</div>
```

提取逻辑（JavaScript）：
```javascript
// 获取所有视频卡片
const cards = document.querySelectorAll('.bili-video-card, [class*="video-card"]');
const results = [];
cards.forEach(card => {
  const link = card.querySelector('a[href*="/video/"]');
  if (!link) return;
  const bvid = (link.href.match(/BV\w+/) || [])[0];
  const title = card.querySelector('.title, .card-title')?.textContent?.trim() || '';
  const statsText = card.querySelector('.stats, [class*="stat"]')?.textContent || '';
  // 解析播放量、收藏数等...
  results.push({bvid, title, views, favorites, comments, shares});
});
JSON.stringify(results);
```

## 注意事项

- B站主页**不需要登录**即可浏览全部投稿视频。仅有 `buvid3/buvid4` 设备指纹cookie就足够了。
- Chrome必须至少访问过bilibili.com一次（设置设备cookie）。
- 如果用户设置了隐私，可能看不到视频列表。
- 视频数量较多时（>100个），需要多次滚动才能加载完。
- yt-dlp对B站支持成熟，自动处理去水印和分P视频。
- 每个视频单独一个文件夹，包含：视频文件 + post_caption.txt + metadata.json。

## 已知坑点

| 坑 | 原因 | 解决方案 |
|----|------|---------|
| yt-dlp返回412错误 | B站反爬机制 | 必须用 `--cookies-from-browser chrome` |
| CDP提取blob URL | DASH流媒体(MSE) | 不要尝试CDP抓视频直链，下载交给yt-dlp |
| DOM播放量不准确 | 缓存/错误选择器 | 下载后用yt-dlp --dump-json验证 |
| BV号重复 | DOM中多处引用同一视频 | 必须用Set按bvid去重 |
| 未登录状态 | 缺少buvid3/buvid4 | Chrome至少访问过一次bilibili.com |
