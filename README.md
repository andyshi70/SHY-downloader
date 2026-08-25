# SHY-downloader

通用视频下载器 — 15+ 平台，一个 CLI。

## 支持的渠道

| 渠道 | 方式 | 需要配置？ |
|------|------|----------|
| **微信视频号** | 在线解析 API (sph.litao.workers.dev) | ❌ 零配置 |
| 微信视频号 (备选) | Yuanbao API + Go binary | 需 WECHAT_YUANBAO_COOKIE |
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

## 安装依赖

```bash
pip install requests yt-dlp ffmpeg-python
# 可选
pip install httpx[http2]  # 小红书
pip install openai-whisper  # 本地ASR
```

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

# 指定输出目录
python3 scripts/download_video.py "https://..." --out-root ~/Desktop/videos

# 下载报告
python3 scripts/download_video.py "https://..." --out-root ./downloads
```

## 微信视频号配置

### Path 1: Online API（推荐，零配置）

直接使用，无需任何配置。

### Path 2: Yuanbao API（需要 Cookie）

1. 打开 https://yuanbao.tencent.com
2. 用微信扫码登录
3. Chrome DevTools → Application → Cookies → `https://yuanbao.tencent.com`
4. 复制所有 Cookie 字符串
5. 设置环境变量：
   ```bash
   export WECHAT_YUANBAO_COOKIE="hy_user=xxx; hy_token=xxx; ..."
   ```
6. 写入 `~/.hermes/.env` 让每次都能用

### Path 3: Go Binary Proxy Mode

需要编译并配置 Go binary：

```bash
cd ~/aigit/wx_channels_download
go build -o wx_channels_download ./main.go
```

编辑 `config.yaml`：

```yaml
debug:
  error: false
  echolog: false

download:
  dir: "~/Desktop/videos"  # 修改为你想要的下载目录
  pauseWhenDownload: false
  playDoneAudio: false

api:
  protocol: "http"
  hostname: "127.0.0.1"
  port: 2022

proxy:
  system: false
  hostname: "127.0.0.1"
  port: 2023
  tun: false
  skipInstallRootCert: true

cloudflare:
  sphCookie: "hy_user=xxx; hy_token=xxx; hy_source=web"
```

启动：
```bash
./wx_channels_download --config config.yaml
```

## 项目结构

```
SHY-downloader/
├── scripts/
│   ├── download_video.py      # 主程序
│   ├── asr.py                 # ASR 语音转文字
│   ├── batch_follow.py        # 批量追踪博主
│   ├── douyin_batch.py        # 抖音批量下载
│   ├── bilibili_batch.py      # B站批量下载
│   └── providers/
│       ├── wechat_channels.py # 视频号专用
│       ├── douyin.py          # 抖音专用
│       ├── xiaohongshu.py     # 小红书专用
│       ├── youtube.py         # YouTube
│       ├── bilibili.py        # B站
│       ├── vimeo.py           # Vimeo
│       ├── twitter.py         # X/Twitter
│       ├── tiktok.py          # TikTok
│       ├── instagram.py       # Instagram
│       └── facebook.py        # Facebook
├── references/                # API 参考文档
│   ├── wechat-channels-api.md
│   ├── wechat-channels-manual-fallback.md
│   ├── douyin-cookie-api-auth.md
│   └── ...
└── SKILL.md                   # Hermes Skill 定义
```

## 输出格式

下载完成后会在输出目录生成：

```
2026-08-25-video-title/
├── video.mp4              # 视频文件
├── metadata.json          # 元数据（作者、标题、点赞数等）
├── post_caption.txt       # 视频描述文案
└── download-report.md     # 下载报告
```

## 注意事项

- 视频号链接有时效性，过期后需要重新分享
- 部分平台需要登录态（Cookie）才能下载
- 大文件下载请确保磁盘空间充足
- Go binary 的 proxy mode 会修改系统代理，下载后会自动还原

## License

MIT
