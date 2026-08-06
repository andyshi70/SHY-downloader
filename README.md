# SHY-downloader

> Universal video downloader — 15+ platforms, one CLI.  
> 抖音、小红书、微信视频号、YouTube、Bilibili、Vimeo、X/Twitter、TikTok、Instagram、Facebook、mp4 直链、m3u8 流，全平台覆盖。

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)]()
[![yt-dlp](https://img.shields.io/badge/yt--dlp-powered-green)]()

---

## 渠道一览

### 零配置 — 直接可用

| 渠道 | 下载方式 | 说明 |
|------|---------|------|
| **微信视频号** `weixin.qq.com/sph/` | 在线解析 API | 通过 `sph.litao.workers.dev` 免费解析，无需任何 cookie、登录、本地工具。默认保存 H.264 + H.265 双版本 |
| **抖音** `douyin.com` `iesdouyin.com` | H5 ROUTER_DATA 去水印直链 | 解析 SSR 页面提取非水印 play_addr，优先 `aweme/v1/play/` 端点 |
| **YouTube** `youtube.com` `youtu.be` | yt-dlp + Invidious 代理降级 | 匿名下载，被 bot 检测自动降级到 360p 代理通道 |
| **Bilibili** `bilibili.com` `b23.tv` | yt-dlp + 自动 Chrome cookies | 匿名 412 时自动取 Chrome cookie 重试 |
| **Vimeo** `vimeo.com` | yt-dlp | 匿名下载 |
| **X/Twitter** `x.com` `twitter.com` | yt-dlp + Chrome cookies 备选 | 公开视频匿名可用 |
| **TikTok** `tiktok.com` | yt-dlp + Chrome cookies 备选 | 公开视频匿名可用 |
| **Instagram** `instagram.com` | yt-dlp + Chrome cookies 备选 | Reels/帖子视频 |
| **Facebook** `facebook.com` `fb.watch` | yt-dlp + Chrome cookies 备选 | 公开视频 |
| **mp4/webm/mov/mkv 直链** | requests 流式下载 | 断点续传 (.part + HTTP Range) |
| **m3u8/mpd 流** | yt-dlp | 分片并发下载 |

### 需特殊配置

| 渠道 | 下载方式 | 需要什么 |
|------|---------|---------|
| **小红书** `xiaohongshu.com` | httpx HTTP/2 + `__INITIAL_STATE__` 解析 | 链接必须含 `xsec_token`（从 App 复制完整分享链接） |
| **微信视频号 (备选)** | Yuanbao API + Go binary | 需 `WECHAT_YUANBAO_COOKIE` + 编译 `wx_channels_download` |

---

## 功能特性

- **微信视频号零配置** — 无需 Go binary、无需 cookie、无需证书，一行命令下载
- **抖音去水印** — 解析 H5 页面 `window._ROUTER_DATA`，获取非水印 `play_addr` 直链
- **小红书支持** — httpx HTTP/2 + `__INITIAL_STATE__` 解析，获取视频/图片/元数据
- **断点续传** — 直链视频使用 `.part` 文件 + HTTP Range 续传，中断后复用原目录继续
- **YouTube 熔断降级** — yt-dlp 被 bot 检测时自动切换 Invidious 代理通道
- **字幕 + 封面** — 下载自动/手动字幕，嵌入视频封面
- **下载报告** — 每次运行生成 `download-report.md` + `download-report.json`，含逐条状态
- **下载历史去重** — 使用 `--download-archive` 避免重复下载
- **ASR 语音转文字** — 支持 SiliconFlow SenseVoiceSmall 和本地 Whisper
- **批量追踪博主** — `batch_follow.py` 跟踪多个创作者，自动下载最新视频，去重，可选飞书同步
- **抖音批量下载** — `douyin_batch.py` 从博主主页按点赞数筛选下载
- **B站批量下载** — `bilibili_batch.py` 从 UP 主页按播放量筛选下载
- **元数据提取** — 每个视频保存 `metadata.json` + `post_caption.txt`，含标题、描述、作者、统计数据

---

## 快速开始

### 安装依赖

```bash
pip install requests yt-dlp
# 可选：小红书支持
pip install httpx[http2]
# 可选：本地 ASR
pip install openai-whisper
```

### 下载单个视频

```bash
python3 scripts/download_video.py "https://www.youtube.com/watch?v=BaW_jenozKc"
```

### 指定标题

```bash
python3 scripts/download_video.py --title "我的视频" "https://www.bilibili.com/video/BV1xx411c7mD"
```

### 微信视频号（零配置）

```bash
python3 scripts/download_video.py "https://weixin.qq.com/sph/Axv548mzBF"
```

输出：
```
Video/Downloads/2026-08-06-视频号主题/
├── 视频描述_H264.mp4
├── 视频描述_H265.mp4
├── metadata.json
├── post_caption.txt
├── download-report.md
└── download-report.json
```

### 带字幕和封面

```bash
python3 scripts/download_video.py \
  --subtitles --sub-langs "zh.*,en.*" --embed-thumbnail \
  --title "教程" "https://www.youtube.com/watch?v=..."
```

### 只提取元数据（不下载视频）

```bash
python3 scripts/download_video.py --metadata-only "https://v.douyin.com/..."
```

### 批量下载

```bash
# 从文本文件（每行一个链接）
python3 scripts/download_video.py --url-file "urls.txt"

# 下载整个播放列表
python3 scripts/download_video.py --playlist "https://www.youtube.com/playlist?list=..."
```

### 高清/最高画质

```bash
# 限制 1080p（默认）
python3 scripts/download_video.py --quality 1080 "..."

# 最高画质
python3 scripts/download_video.py --quality best "..."
```

### 使用 cookie 绕过风控

```bash
# 从文件
python3 scripts/download_video.py --cookies-file "cookies.txt" "https://www.bilibili.com/video/BV..."

# 从浏览器
python3 scripts/download_video.py --browser-cookies chrome "https://..."
```

### 断点续传（中断后继续）

```bash
# 第一次下载中断后，复用目录继续
python3 scripts/download_video.py --run-dir "Video/Downloads/2026-08-06-原任务" "https://example.com/video.mp4"
```

### 下载历史去重

```bash
python3 scripts/download_video.py \
  --download-archive "Video/Downloads/download-archive.txt" \
  --url-file "urls.txt"
```

---

## 输出目录结构

### 默认输出

```
~/Desktop/av/chaijie/
├── douyin-{aweme_id}/
│   ├── video.mp4
│   ├── post_caption.txt
│   ├── metadata.json
│   ├── audio.m4a           (ASR 启用时)
│   └── transcript.txt       (ASR 启用时)
├── bilibili-{bvid}/
│   ├── video.mp4
│   ├── post_caption.txt
│   └── metadata.json
├── youtube-{video_id}/
│   ├── video.mp4
│   ├── post_caption.txt
│   └── metadata.json
├── wechat-channels-{id}/
│   ├── 视频描述_H264.mp4
│   ├── 视频描述_H265.mp4
│   ├── post_caption.txt
│   └── metadata.json
├── xiaohongshu-{note_id}/
│   ├── video.mp4
│   ├── post_caption.txt
│   └── metadata.json
└── manifests/              (batch_follow 产物)
    └── {timestamp}-batch-follow.json
```

### 自定义输出目录

```bash
python3 scripts/download_video.py --out-root ./downloads "..."
```

使用 `--out-root` 时，报告文件也输出到对应目录。

---

## 批量和追踪

### 批量追踪博主

```bash
# 从 JSON 配置文件追踪
python3 scripts/batch_follow.py --platform all --videos-per-creator 3

# 仅 B站，最少 10000 播放量
python3 scripts/batch_follow.py --platform bili --min-views 10000

# 仅抖音，最少 5000 点赞
python3 scripts/batch_follow.py --platform douyin --min-likes 5000

# 试运行（不下载）
python3 scripts/batch_follow.py --dry-run

# 下载后标记为待分析（配合 viral-analysis-pipeline）
python3 scripts/batch_follow.py --analyze
```

### 抖音批量下载（从博主主页）

```bash
DOUYIN_COOKIE="..." python3 scripts/douyin_batch.py \
  --input /tmp/douyin_videos.json \
  /用户名 \
  --min-likes 10000
```

### B站批量下载（从UP主页）

```bash
python3 scripts/bilibili_batch.py \
  --input /tmp/bilibili_videos.json \
  /用户名 \
  --min-views 10000
```

---

## ASR 语音转文字

```bash
# 自动选择（SiliconFlow > Whisper）
python3 scripts/download_video.py --asr auto "..."

# 指定 SiliconFlow
SILICONFLOW_API_KEY="..." python3 scripts/download_video.py --asr siliconflow "..."

# 指定本地 Whisper
python3 scripts/download_video.py --asr whisper --asr-model base "..."

# 跳过 ASR
python3 scripts/download_video.py --asr none "..."
```

---

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `urls` | — | 视频链接（可多个） |
| `--url-file` | — | 文本文件，每行一个链接 |
| `--title` | 自动 | 输出目录名称 |
| `--out-root` | `~/Desktop/av/chaijie` | 输出根目录 |
| `--run-dir` | — | 复用已有目录（续传） |
| `--quality` | `1080` | 最大高度或 `best` |
| `--max-video-mb` | `2000` | 单视频大小限制 |
| `--cookies-file` | — | Netscape cookies.txt |
| `--browser-cookies` | — | `chrome`/`safari`/`edge`/`firefox` |
| `--playlist` | off | 允许下载播放列表 |
| `--no-invidious` | off | 禁用 YouTube Invidious 降级 |
| `--subtitles` | off | 下载字幕 |
| `--sub-langs` | `zh.*,en.*` | 字幕语言 |
| `--embed-thumbnail` | off | 嵌入封面 |
| `--download-archive` | — | 下载历史文件 |
| `--metadata-only` | off | 仅提取元数据 |
| `--asr` | `auto` | ASR 后端 |
| `--asr-language` | `auto` | ASR 语言 |
| `--asr-prompt` | — | ASR 提示词 |
| `--timeout` | `3600` | yt-dlp 超时 |

---

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `DOUYIN_COOKIE` | 仅限封禁 | 抖音 H5 页面 cookie |
| `WECHAT_YUANBAO_COOKIE` | 仅限备选路径 | 微信视频号 Yuanbao API cookie |
| `WX_CHANNELS_BINARY` | 仅限备选路径 | Go binary 路径 |
| `SILICONFLOW_API_KEY` | 仅限 SiliconFlow ASR | SiliconFlow API key |

---

## 项目结构

```
SHY-downloader/
├── scripts/
│   ├── download_video.py          # 主入口 CLI
│   ├── asr.py                     # ASR 语音转文字
│   ├── batch_follow.py            # 批量追踪博主
│   ├── douyin_batch.py            # 抖音批量下载
│   ├── bilibili_batch.py          # B站批量下载
│   ├── creators.example.json      # 博主配置模板
│   └── providers/
│       ├── __init__.py            # 注册中心
│       ├── douyin.py              # 抖音 H5 去水印
│       ├── bilibili.py            # B站 yt-dlp
│       ├── youtube.py             # YouTube yt-dlp
│       ├── xiaohongshu.py         # 小红书 __INITIAL_STATE__
│       ├── wechat_channels.py     # 微信视频号（在线API + Yuanbao + Go）
│       ├── vimeo.py               # Vimeo
│       ├── twitter.py             # X/Twitter
│       ├── tiktok.py              # TikTok
│       ├── instagram.py           # Instagram
│       └── facebook.py            # Facebook
├── tests/
│   └── test_download_video.py
├── references/                    # 平台文档
├── SKILL.md
└── README.md
```

---

## 致谢

- [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) — 微信视频号下载器，本项目视频号在线解析能力基于其 API
- [JoeanAmier/XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) — 小红书下载器，本项目小红书解析参考其方法
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 核心下载引擎
- [tjxj/z-skills](https://github.com/tjxj/z-skills) — 本项目灵感来源

## 免责声明

本项目仅用于技术交流和研究目的。请遵守法律法规，尊重内容创作者权益，仅下载您有权访问和保存的内容。请勿用于任何非法用途。