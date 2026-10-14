---
name: SHY-downloader
description: Universal video downloader. 15+ platforms: 抖音(Douyin), 小红书(Xiaohongshu), 微信视频号(WeChat Channels), YouTube, Bilibili, Vimeo, X/Twitter, TikTok, Instagram, Facebook, mp4直链, m3u8流. 微信视频号零配置在线解析, 抖音H5去水印, 小红书__INITIAL_STATE__解析, 断点续传, 字幕封面, 下载报告, ASR转录, 批量追踪博主.
confidence: proven
---

# SHY-downloader

Universal video downloader — 15+ platforms, one CLI.

## 支持的渠道

| 渠道 | 方式 | 需要配置？ |
|------|------|----------|
| **微信视频号** | 在线解析 API (sph.litao.workers.dev) | ❌ 零配置 |
| 微信视频号 (备选) | Yuanbao API + Go binary | 需 WECHAT_YUANBAO_COOKIE（先登陆元宝） |
| **抖音** | H5 ROUTER_DATA 去水印直链 | ❌ 零配置 |
| **小红书** | httpx HTTP/2 + __INITIAL_STATE__ | ⚠️ 需 xsec_token |
| **YouTube** | yt-dlp → Invidious 代理降级 360p | ❌ 零配置 |
| **Bilibili** | yt-dlp → 自动 Chrome cookies | ❌ 零配置 (自动取 cookie) |
| **Vimeo** | yt-dlp | ❌ 零配置 |
| **X/Twitter** | yt-dlp → Chrome cookies | ❌ 零配置 |
| **TikTok** | yt-dlp → Chrome cookies | ❌ 零配置 |
| **Instagram** | yt-dlp → Chrome cookies | ❌ 零配置 |
| **Facebook** | yt-dlp → Chrome cookies | ❌ 零配置 |
| **mp4/webm 直链** | requests 流式下载 + 断点续传 | ❌ 零配置 |
| **m3u8/mpd 流** | yt-dlp | ❌ 零配置 |

## 功能特性

- 断点续传（.part + HTTP Range）
- 下载报告（download-report.md + json）
- 字幕下载 + 封面嵌入
- YouTube Invidious 熔断降级
- 下载历史去重
- ASR 语音转文字 (SiliconFlow / Whisper)
- 批量追踪博主 (batch_follow.py)
- 抖音批量下载 (douyin_batch.py)
- B站批量下载 (bilibili_batch.py)

## 快速开始

```bash
# 下载单个视频
python3 scripts/download_video.py "https://www.youtube.com/watch?v=..."

# 指定标题
python3 scripts/download_video.py --title "我的视频" "https://www.bilibili.com/video/BV..."

# 带字幕+封面
python3 scripts/download_video.py --subtitles --embed-thumbnail "https://www.youtube.com/watch?v=..."

# 只提取元数据
python3 scripts/download_video.py --metadata-only "https://v.douyin.com/..."

# 微信视频号（零配置）
python3 scripts/download_video.py "https://weixin.qq.com/sph/Axv548mzBF"

# 批量下载
python3 scripts/download_video.py --url-file "urls.txt"

# 下载报告
python3 scripts/download_video.py "https://..." --out-root ./downloads
```

## 注意事项

- **微信视频号**：下载前会先打印提醒——**请先在网页端登陆腾讯元宝，登陆后再使用此 skill**。代码按序尝试三条路径：① 在线解析 API `sph.litao.workers.dev`（零配置，可能失败）② 元宝 API（需 `WECHAT_YUANBAO_COOKIE`）③ Go binary 代理模式。在线 API 成功时直接下载 MP4；失败且无 Cookie 时报错要求配置。详见 `references/wechat-channels-api.md`。
- **元宝 Cookie 获取与配置**：浏览器打开 https://yuanbao.tencent.com 微信扫码登录 → DevTools → Application → Cookies → `yuanbao.tencent.com` 复制 Cookie 字符串 → 配置：`export WECHAT_YUANBAO_COOKIE="hy_user=...; hy_token=..."`（或写入文件后 `export WECHAT_YUANBAO_COOKIE_FILE="/path/to/cookie.txt"` 指定）。**Cookie 约 2 个月有效期，过期必须重新登录获取。**
- **ASR 转文字**：`asr.py` 是 Python 库模块，非 CLI。当前 Whisper 后端**默认模型是 `base`**，zh 场景自动附加中文 topic prompt（改善标点）；追求更高准确率请按 `references/asr-quality.md` 建议改用 `medium`。
- **Go binary 生命周期**：`terminal` 工具不接受 `&` 后台化，且 `background=true` + `notify` 参数组合受限。**在 `execute_code` 中用 `subprocess.Popen` + `os.setsid` 管理 Go binary**——启动、等待 API、解析、下载、停止全部在同一 `execute_code` 块内完成，避免进程泄露。

## 依赖

- Python 3.9+
- `requests`, `yt-dlp`, `ffmpeg`
- 可选: `httpx[http2]` (小红书), `openai-whisper` (本地ASR)