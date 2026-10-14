# yt-dlp PATH 问题与解决方案

## 问题现象

SHY-downloader 脚本调用 `find_ytdlp()` 返回 None，导致所有 B 站/YouTube 等 yt-dlp 依赖渠道都失败。

错误信息示例：
```
failed (yt-dlp not found, no provider)
yt-dlp: error: no such option: --js-runtimes
```

## 原因

macOS 上通过 `pip install --user yt-dlp` 安装的 yt-dlp 位于 `~/Library/Python/<ver>/bin/yt-dlp`，默认不在 PATH 中。

Python 3.9 对应的 pip 版本停留在 2025.10.14，不支持 B 站新版页面（会报 `--js-runtimes` 错误）。

## 解决路径（优先级从高到低）

### 1. 直接用 yt-dlp Python 模块

```bash
python3 -m yt_dlp [选项] URL
```
不依赖 PATH，只要有 yt_dlp 包就能跑。

### 2. 找用户安装目录

```bash
# macOS Python 3.9
ls ~/Library/Python/3.9/bin/yt-dlp
ls ~/Library/Python/3.9/lib/python/site-packages/yt_dlp/
```

### 3. Python 3.11 via uv（推荐，可装最新版）

```bash
# 查看路径
which /Users/evandy/.local/bin/python3.11

# 安装最新版 yt-dlp（支持 B 站新版）
/Users/evandy/.local/bin/python3.11 -m pip install -U yt-dlp --break-system-packages
```

验证：
```bash
/Users/evandy/.local/bin/python3.11 -m pip show yt-dlp | grep Version
# 应显示 2026.08.19 或更新版本
```

### 4. 临时加 PATH

```bash
export PATH="$HOME/Library/Python/3.9/bin:$HOME/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin:$PATH"
```

## B 站多 P 批量下载

YT 变量指向可用版本：
```bash
YT="/Users/evandy/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/yt-dlp"
DIR="$HOME/Desktop/av/chaijie/2026-09-10-bilibili-BV1tnth6MEFW"
for i in $(seq 2 13); do
  $YT --cookies-from-browser chrome \
    -o "$DIR/%(playlist_index)s_%(title)s.%(ext)s" \
    --no-playlist \
    "https://www.bilibili.com/video/BV1tnth6MEFW/?p=$i"
done
```

关键参数：
- `--cookies-from-browser chrome`：自动从 Chrome 取 B 站 cookie
- `--no-playlist`：只下当前 P，不整个合集
- `%(playlist_index)s`：文件名带序号，方便排序
- 每个视频建议 sleep 2s 避免限流

## B 站合集预检（先查 P 数再下）

批量下载前必须先用 `-J` 获取元数据确认 P 总数和标题：
```bash
YT="/Users/evandy/.local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/yt-dlp"
YT --cookies-from-browser chrome -J "https://www.bilibili.com/video/BVxxxxxx/" > /tmp/bili_info.json
python3 -c "
import json
with open('/tmp/bili_info.json') as f:
    d = json.load(f)
entries = d.get('entries', [])
print(f'共{len(entries)}P')
for i, e in enumerate(entries, 1):
    print(f'p{i}: {e.get(\"title\",e.get(\"webpage_url_basename\",\"\"))}')
"
```

注意：B 站合集用 `entries` 字段而非 `pages`，这是新版 API 结构。

## 失败案例记录

| 时间 | 问题 | 根因 | 修复 |
|------|------|------|------|
| 2026-09-10 | yt-dlp not found | PATH 不含用户安装目录 | 用 python3 -m yt_dlp 或直接路径 |
| 2026-09-10 | --js-runtimes error | yt-dlp 2025.10.14 太旧 | 换 3.11 + 新版 yt-dlp 2026.08.19 |
| 2026-09-10 | B站合集pages为空 | 新版API用entries字段 | 预检时取entries而非pages |
