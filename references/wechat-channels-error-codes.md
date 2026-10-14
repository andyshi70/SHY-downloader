# 视频号错误码与故障排查

## errMsg.type 含义

| type | 含义 | 原因 | 解决方案 |
|------|------|------|----------|
| 0 | 成功 | - | 正常下载 |
| 1 | 参数错误 | URL 格式不对 | 检查分享链接 |
| 2 | 链接失效 | 视频已删除/私密/过期 | 重新分享获取新链接 |
| 3 | 权限不足 | 需登录才能访问 | 配置 WECHAT_YUANBAO_COOKIE |
| 4 | 频率限制 | 请求过于频繁 | 等待后重试 |

## Token 失效处理

**症状：** `token not exist!` 或 HTTP 400

**原因：** 
- Yuanbao API 返回的 CDN token 有时效性
- Python 获取的 token 与 Go binary 获取的不同

**解决：**
1. 优先使用 Go binary 的 parse_sph API（返回不同的 working token）
2. 如果 Go binary 也失败，重新分享视频获取新链接
3. 检查 WECHAT_YUANBAO_COOKIE 是否过期（约2个月有效期）

## 加密视频处理

**症状：** 下载到的是 HTML 而非 MP4

**原因：** 视频被 ISAAC64 加密，需要先解密

**解决：**
1. 使用 Go binary proxy mode（自动捕获解密 key）
2. 或使用 Python 解密函数（需要 encKey 和 encLen）

```python
from scripts.providers.wechat_channels import decrypt_video_data

# 解密前 128KB
decrypted = decrypt_video_data(encrypted_data, enc_len=131072, key=enc_key)
```

## 链接时效性

- 分享链接有效期：约 7 天
- Token 有效期：约 24 小时
- 建议：下载后及时保存，不要长时间存储 share URL
