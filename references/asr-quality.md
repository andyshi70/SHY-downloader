# ASR Quality Reference

## Whisper model tiers — Chinese Accuracy

### `base` model — unreliable for Chinese

`base` misreads brand names, homophones, and technical terms constantly:

| Type | Example (garbled → correct) |
|------|----------------------------|
| Brand name | "Cloud Code Codex" → "Claude Code, Codex" |
| Homophone | "太大力了" → "太炸裂了" |
| Word boundary | "CP开员了" → "MCP开源了" |
| Proper noun | "解图分析" → "截图分析" |
| Punctuation | No punctuation; run-on sentence |

**Never** present `base` output as final transcript. Always correct or use `medium`.

### `medium` model — dramatically better for Chinese

`medium` recovers brand names, technical terms, and general accuracy with minimal errors. Takes ~1.5 min for a 2-min video (~30 frames/s). Use this as the default when `SILICONFLOW_API_KEY` is unavailable.

**Domain-specific `initial_prompt`** significantly improves recognition of technical terms. Describe the video topic explicitly:

```python
run_asr(video, out_dir,
    backend="whisper",
    model="medium",
    language="zh",
    prompt="这是一段关于Windows MCP开源项目的介绍视频，涉及GitHub、AI、MCP协议、电脑自动化等技术话题。请使用简体中文转写，保留专有名词如GitHub、MCP、Windows、AI、MIT等。")
```

**Rule:** Use Whisper `medium` + topic-specific prompt for Chinese content. `base` only for quick drafts that will be heavily post-edited. `medium` requires no extra setup — just `model="medium"` parameter.

## Running ASR (library, not CLI)

`scripts/asr.py` is a Python module, not a CLI script. Call `run_asr()` via `execute_code`:

```python
import sys; sys.path.insert(0, '/Users/evandy/.hermes/skills/SHY-downloader/scripts')
from asr import run_asr
from pathlib import Path

result = run_asr(
    video_path=Path('path/to/video.mp4'),
    output_dir=Path('output/dir'),
    backend='whisper',
    model='medium',        # use 'medium' for Chinese, not 'base'
    language='zh',
    prompt='这是关于<topic>的视频。请使用简体中文转写，保留专有名词。',
)
print(result)  # includes transcript_path
```

`SILICONFLOW_API_KEY` empty → Whisper is the only backend. `medium` model works well without it.

## Output files

- `transcript.txt` — plain text transcript (punctuated via gap analysis)
- `transcript.whisper.json` — full Whisper segments JSON (with timestamps)
- `audio.m4a` / `audio.mp3` — extracted audio

## Model selection guide

| Model | Speed | Chinese accuracy | Best for |
|-------|-------|-----------------|----------|
| `base` | Fast (~10s) | Poor — heavy post-correction needed | Quick draft only |
| `medium` | ~1.5 min per 2 min video | Good — brand names and technical terms mostly correct | Default for Chinese |
| `large-v3` | Slow | Excellent | When accuracy is critical |

Set `SILICONFLOW_API_KEY` and use `backend='siliconflow'` for SenseVoiceSmall — best accuracy with API. Without key, Whisper `medium` is the recommended default.
