# Bilibili ASR (Audio → Text)

本模块将 `output/<bvid>/` 下的音频（优先 mp3/m4a）转写为文本：

- `output/<bvid>/asr/transcript.txt`
- `output/<bvid>/asr/transcript.srt`
- `output/<bvid>/asr/segments.json`

默认优先使用随仓库附带的本地 faster-whisper 模型：
`BiliNote_win_v1.1.1/models/whisper/models--Systran--faster-whisper-base/snapshots/<hash>/`。
如未找到本地模型，则回退使用在线模型名（例如 `base`），由 faster-whisper 自动下载。

## 使用

```bash
python -m bilibili_asr.cli run --bvid BVxxxxxxxxx [--audio path] [--lang zh] [--model auto|base|/path/to/model] [--compute int8|float16|float32]
```

参数说明：
- `--bvid`：视频 bvid（用于定位 `output/<bvid>/`）。
- `--audio`：可选，显式指定音频路径；不传则自动在 `output/<bvid>/` 中查找 `*.mp3|*.m4a`。
- `--lang`：可选，语言代码（如 `zh`、`en`）；不传由模型自动检测。
- `--model`：
  - `auto`（默认）：优先本地 Systran/faster-whisper-base 快照；找不到则用在线 `base`。
  - 直接填写 huggingface 名称（如 `medium`），或本地模型目录绝对路径。
- `--compute`：计算类型，默认 `int8`（CPU 友好）。GPU 可使用 `float16`。

产物路径：`output/<bvid>/asr/`。

