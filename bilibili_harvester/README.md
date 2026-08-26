# Bilibili Harvester (CLI)

单文件/批量采集 Bilibili 的可公开获取数据：元信息、字幕、音频、视频、弹幕（XML）与评论（尽力而为）。

## 输入

- `target_movie.txt`（根目录）：每行一个 B 站视频链接或 b23 短链。
- `cookie.txt`（根目录，可选）：浏览器导出的 Netscape 格式 Cookie，用于受限视频访问与更高清晰度。

## 输出

默认输出到 `output/<bvid>/`：

- `metadata.json`：视频元信息（含分P、作者、统计快照、yt-dlp 提取到的字段）。
- `subtitles/`：提取到的字幕（.srt/.xml；未登录时仅弹幕xml）。
- `video.mp4`：合并后的视频（有需要才下）。
- `audio.mp3`：提取到的音频（可配置码率，默认64kbps）。
- `danmaku.xml` 与 `danmaku_<cid>.xml`：基础弹幕XML（主cid与分P）。
- `comments.json`：评论快照（首屏/若干页，尽力而为）。
- `core.json` / `streams.json`：结构化核心与码流信息（与上面在同一目录）。
- `manifest.json`：该目录下产物的文件清单（含sha256）。

## 运行

```bash
python -m bilibili_harvester run --output output --download video,audio,subtitles --pages 3
```

可选参数：
- `--output` 输出目录，默认 `harvest_output`。
- `--download` 逗号分隔：`video,audio,subtitles,none`（默认 `audio,subtitles`）。
- `--pages` 评论拉取页数，默认 1。
- `--proxy http://host:port` 可选代理（区域受限时）。

## 说明

- 下载/解析使用 yt-dlp + ffmpeg，复用浏览器 Cookie（若提供）。
- 弹幕：使用 `https://comment.bilibili.com/<cid>.xml` 获取基础 XML；历史/Proto 未覆盖。
- 评论：调用公开接口（GET），可能受限或分页受限，脚本做尽力尝试与错误回退。
