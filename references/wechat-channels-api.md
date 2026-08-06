# WeChat Channels (视频号) API Reference

## Architecture

Two-step API approach + Go binary proxy fallback:

```
Share URL → [Yuanbao API] → playable_url + wx_export_id
                               ↓
                    extract token + eid from playable_url
                               ↓
                    [Channels Feed API] → video URLs + metadata
                               ↓
           ┌─── Python only → "token not exist!" (metadata only)
           │
           └─── Go binary parse_sph API → working token → MP4 download
```

**CRITICAL FINDING:** The Python Yuanbao → Feed API chain returns a CDN token that gives "token not exist!" on direct download. The Go binary's `/api/channels/parse_sph` endpoint returns a different, working token. Always use `proxy_fetch()` for downloads. The pure Python path is metadata-only.

## Step 1: Yuanbao Parse API

**Endpoint:** `POST https://yuanbao.tencent.com/api/weixin/get_parse_result`

**Headers (full set required for auth):**
```
accept: application/json, text/plain, */*
accept-language: zh-CN,zh;q=0.9,en;q=0.8
content-type: application/json
origin: https://yuanbao.tencent.com
referer: https://yuanbao.tencent.com/chat/naQivTmsDa/
user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36
cookie: <WECHAT_YUANBAO_COOKIE>
x-language: zh-CN
x-platform: mac
x-source: web
x-webversion: 2.69.0
```

**Payload:**
```json
{"type": "video_channel_url", "url": "<share_url>", "scene": 1}
```

**Response:**
```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "wx_export_id": "export_xxx",
    "cover_url": "https://...",
    "author": "作者名",
    "author_icon": "https://...",
    "desc": "视频描述",
    "playable_url": "https://channels.weixin.qq.com/...?token=xxx&eid=xxx"
  }
}
```

**Key fields:**
- `playable_url` — browser-playable video URL (may be unencrypted). Contains `token` + `eid` query params needed for Step 2.
- `wx_export_id` — video export ID
- `desc` — video title / description text

## Step 2: Channels Feed API

**Endpoint:** `POST https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`

**Query params:** `_rid=<timestamp-hex>-<random-hex>&_pageUrl=https%3A%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed`

**Headers:**
```
Accept: application/json, text/plain, */*
Accept-Language: zh-CN,zh;q=0.9,en;q=0.8
Content-Type: application/json
Origin: https://channels.weixin.qq.com
Referer: https://channels.weixin.qq.com/finder-preview/pages/feed?entry_card_type=48&comment_scene=39&appid=0&token=<token>&entry_scene=0&eid=<eid>
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36
```

**Payload:**
```json
{"baseReq": {"generalToken": "<token>"}, "exportId": "<eid>"}
```

**Response structure:**
```json
{
  "data": {
    "authorInfo": {
      "nickname": "作者名",
      "headImgUrl": "https://...",
      "authIconUrl": "..."
    },
    "feedInfo": {
      "description": "视频描述",
      "videoUrl": "https://...?encfilekey=xxx&token=xxx&...",
      "originVideoUrl": "https://...?encfilekey=xxx&token=xxx",
      "h264VideoInfo": {"videoUrl": "https://..."},
      "h265VideoInfo": {"videoUrl": "https://..."},
      "coverUrl": "https://...",
      "likeCountFmt": "1.2万",
      "commentCountFmt": "356",
      "forwardCountFmt": "89",
      "favCountFmt": "520",
      "mediaType": 4,
      "createtime": 1700000000
    }
  }
}
```

## Go Binary parse_sph API

**Endpoint:** `GET http://127.0.0.1:2022/api/channels/parse_sph?url=<share_url>`

**This is the PRIMARY download method.** The Go binary's API returns a working CDN token that pure Python requests cannot obtain.

### Required config.yaml
```yaml
proxy:
  system: false            # DON'T modify system proxy
  skipInstallRootCert: true
cloudflare:
  sphCookie: "hy_user=xxx; hy_token=xxx; hy_source=web"
```

Without `proxy.system: false`, the binary modifies macOS system proxy settings. Without `sphCookie`, the parse_sph API returns 400.

### Config file auto-detection (`_get_go_config_path()`)
Search order:
1. `WX_CHANNELS_CONFIG` env var
2. Same directory as the Go binary
3. `config/` subdirectory next to the binary
4. Hard-coded `~/aigit/wx_channels_download/config.yaml`

## Download Flow (confirmed working, 2026-07-17)

```python
# 1. Start Go binary with config (proxy.system: false, skipInstallRootCert: true)
# 2. Wait for API at http://127.0.0.1:2022/api/status
# 3. GET /api/channels/parse_sph?url=<share_url>
# 4. Extract h264VideoInfo.videoUrl from response
# 5. Download with Python requests (NOT Go binary's `download` command)
# 6. Save MP4 + metadata.json + post_caption.txt
# 7. Stop Go binary (SIGTERM → 8s wait → SIGKILL)
```

**CRITICAL:** The Go binary's `download` command uses Range requests (multi-threaded). The CDN at `finder.video.qq.com` does NOT support Range requests — it returns "服务器不支持并发下载". Always use Python `requests.get(stream=True)` for the actual file download.

**Verified with real video:** '逛逛GitHub' post "全球首个 Agent 视频制作神器" (251 likes). URL: `https://weixin.qq.com/sph/AGt5yUy6wO`. Downloaded 2.4MB MP4, valid ISO Media file.

### Complete flow output structure
```
~/Desktop/downloads/wechat_channels/
├── 逛逛GitHub_Agent视频神器.mp4    2.4MB
├── metadata.json                    # author, title, likes, comments, forwards
└── post_caption.txt                 # video description text
```

## Getting the Yuanbao Cookie

1. Open https://yuanbao.tencent.com in Chrome
2. Sign in with WeChat QR code
3. DevTools → Application → Storage → Cookies → https://yuanbao.tencent.com
4. Copy ALL cookies as a semicolon-separated string
5. Set as `WECHAT_YUANBAO_COOKIE` env var

Minimal required cookies: `hy_user`, `hy_token`, `hy_source`. Domain is `.tencent.com` (wildcard). Expiry: ~2 months.

## ISAAC64 Decryption

Algorithm from `wx_channels_download/pkg/decrypt/decrypt.go`, ported to Python.

**Key parameters:**
- `encKey`: uint64 — the decryption key (captured from CDN response headers `x-enc-file-key` in proxy mode)
- `encLen`: typically 131072 bytes (128 KB) — only the beginning of the file is encrypted
- Algorithm: ISAAC-64 stream cipher (64-bit word variant of ISAAC)

**Python implementation in `scripts/providers/wechat_channels.py`:**
- `RandCtx64` class — ISAAC-64 context with `random()` method
- `decrypt_video_data(data, enc_len, key)` — decrypt function

**Proxy mode key capture:**
- When `wx_channels_download` runs as system proxy, it intercepts CDN responses
- The CDN returns special headers: `x-enc-file-key` (decryption key) and `x-enc-len` (encrypted length)
- These headers are stripped by the CDN edge; only the proxy can capture them
- In API mode, the playable URL from Yuanbao is typically unencrypted

## wx_channels_download CLI Reference

```bash
# Build (CGO-heavy, 3-5 min)
cd ~/aigit/wx_channels_download
go mod tidy
go build -o wx_channels_download ./main.go

# Commands
./wx_channels_download                           # Start proxy + API (interactive)
./wx_channels_download download --url <url> --key <key> --filename <out>
./wx_channels_download decrypt --filepath <path> --key <key>
./wx_channels_download --config config.yaml       # Start with config
```

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `Yuanbao API error: code=xxx` | Cookie expired/invalid | Re-login at yuanbao.tencent.com |
| `Channels API error: errCode=xxx` | Token/eid expired | Share URLs have limited validity |
| `token not exist!` (HTTP 400) | CDN token from Python API doesn't work | **Use Go binary's parse_sph API instead** |
| `服务器不支持并发下载` | CDN doesn't support Range | Use Python requests (single-stream GET) |
| `SecCertificateAddToKeychain: Write permissions error` | macOS cert install failed | Install manually to login keychain |
| `cloudflare.sphCookie not configured` | Go binary config missing | Create config.yaml with sphCookie set |
| Go build timeout | Heavy CGO deps or Homebrew network | Use Go tarball directly, `go mod tidy`, `go build` (3-5 min) |

## Source Code

- Provider module: `scripts/providers/wechat_channels.py`
- Go reference: `/Users/evandy/aigit/wx_channels_download/`
  - Decryption: `pkg/decrypt/decrypt.go`
  - Yuanbao API: `internal/api/sph.go`
  - Download command: `cmd/download.go`
  - Interceptor (proxy): `internal/interceptor/plugin.go`