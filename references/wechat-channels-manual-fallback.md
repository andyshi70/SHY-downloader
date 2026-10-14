# 视频号手动代理回退下载工作流

当 `download_video.py` 自动路径失败时（返回 2-3KB HTML 伪文件，或 `proxy_fetch()` 返回空 URL），使用此手动工作流。

## 验证过的成功案例

- **URL:** `https://weixin.qq.com/sph/AAeBurP47W`
- **视频:** "免费短视频工具爆赚50万" (2.7MB, 1分09秒)
- **作者:** 咕噜猫数字人总监
- **日期:** 2026-07-19

## 工作流

### 1. 启动 Go binary

```bash
/Users/evandy/aigit/wx_channels_download/wx_channels_download --config /Users/evandy/aigit/wx_channels_download/config.yaml
```

等待 3-5 秒直到日志显示`API服务启动成功, 地址: 127.0.0.1:2022`。

验证：
```bash
curl -s http://127.0.0.1:2022/api/status
# 期望: {"code":0,"data":{"channels":{"available":false},"version":"260714"},"msg":"ok"}
```

> `channels.available: false` 是正常的 — 系统代理未设置不影响 parse_sph API。

### 2. 调用 parse_sph API 获取工作 token

```bash
curl -s "http://127.0.0.1:2022/api/channels/parse_sph?url=https://weixin.qq.com/sph/AAeBurP47W" -o /tmp/parse_sph.json
```

提取 h264 URL：
```bash
H264_URL=$(python3 -c "
import json
d = json.load(open('/tmp/parse_sph.json'))
feed = d['data']['data']['feedInfo']
print(feed['h264VideoInfo']['videoUrl'])
")
```

### 3. 下载视频

```bash
curl -s -o /tmp/video.mp4 \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36" \
  -H "Referer: https://channels.weixin.qq.com/" \
  --max-time 30 \
  "$H264_URL"
```

### 4. 验证文件

```bash
file /tmp/video.mp4
# 期望输出: "ISO Media, MP4 Base Media v1 [ISO 14496-12:2003]"
# 如果输出 "HTML document" 或大小 < 10KB → 删除并排查原因
```

### 5. 写入产物

```bash
OUTPUT_DIR="/Users/evandy/Desktop/av/chaijie/<视频名称>"
mkdir -p "$OUTPUT_DIR"
mv /tmp/video.mp4 "$OUTPUT_DIR/<视频名称>.mp4"

# 写 metadata.json
# 写 post_caption.txt
```

### 6. 停止 Go binary

```bash
kill -TERM <PID>  # 或直接 Ctrl+C
```

## 关键技术细节

- **Token 差异:** Go binary 的 parse_sph API 返回的 `token` + `sign` 与 Python Yuanbao API 返回的**不同**。base URL 和 encfilekey 一样，只有 token 和 sign 不同。Go binary 的 token 才能下载成功。
- **CDN 不支持 Range:** 不要用 Go binary 的 `download` 命令下载。它用多线程 Range 请求，CDN 返回 "服务器不支持并发下载"。用 `curl` 或 Python `requests.get(stream=True)` 单线程下载。
- **Python 版本:** 系统 Python 3.9.6 不支持 `X | Y` 类型提示。用 `/Users/evandy/.hermes/hermes-agent/venv/bin/python3` (3.11)。

## 错误排查

| 症状 | 原因 | 修复 |
|------|------|------|
| `parse_sph` 返回空 URL | Go binary 配置问题或 cookie 过期 | 检查 config.yaml 中的 sphCookie；重新登录 yuanbao.tencent.com 获取新 cookie |
| 下载文件 2-3KB 是 HTML | token 无效或 CDN 拒绝 | 使用 parse_sph API 获取新 token，不要用 Python API 的 token |
| `_get_captured_info()` 返回空 | proxy_fetch 内部逻辑问题 | 跳过自动路径，直接上手动手动流程 |
| 文件头 `<!DOCTYPE html>` | 下载到的是错误页面 | 删除文件，验证 URL 和 token，重试 |