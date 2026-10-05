# 运行与证据格式

## 安装

Python 3.10+，推荐 uv。系统需要 `ffmpeg` 与 `ffprobe`。只获取媒体：

```bash
uv sync
uv run --no-sync python scripts/bili_insight.py doctor
```

本地语音转写：`uv sync --extra asr`。默认 faster-whisper `small`、CPU int8；首次使用下载模型到运行目录 `model-cache/`。tiny 可验证管线，不适合作为中文学术长视频的最终质量保证。模型不可访问时只阻碍转写。

可选 OCR 需要 `tesseract` 和 `chi_sim`、`eng` 语言包；也可由 Codex 查看关键帧。系统程序按操作系统安装，脚本不修改系统。

## 统一入口与资料库

日常优先使用 `./bili run SOURCE [--mode video]`；完整例子、目录规则与 FAQ 见 [README.md](../README.md)。默认资料库是本项目的 `data/`（整个目录被 Git 忽略），统一查阅入口为 `data/index.html`，可由 `BILI_INSIGHT_HOME` 或 `--library` 覆盖。`run` 到材料就绪即结束，不能替代语义分析。

资料库任务在 `<资料库>/tasks/<来源ID>/<参数摘要>/`，证据与分析包在 `work/`；Codex 读取全部材料并写入 `work/outline.json`，再 `./bili finish TASK_DIR` 导出到 `outputs/`。`./bili list` 更新离线索引，`./bili bundle TASK_DIR` 只打包白名单中的分析导出与阅读页。重复运行复用相同设置；参数变化或 `--new` 创建独立任务。任务状态及错误写入 `task.json`。

资料库任务共享 `cache/models/`。以下低层命令仍使用独立 `--out` 目录，其模型缓存默认位于该目录，也可以用 `transcribe --model-cache` 指定共享缓存；独立目录不自动列入资料库；用户要求统一存放时用 `./bili adopt LEGACY_DIR` 接入完整转写，然后对实际打印的任务目录执行 finish。adopt 保留原目录并校验、复制媒体与采样画面，更新路径，不重新下载或转写。

## 低层命令

`RUN_DIR` 是任务的独立工作目录，可位于项目的 `data/` 内或仓库外；`SKILL_DIR` 是 Skill 目录。shell 变量不覆盖 HOME 或 CODEX_HOME。

```bash
cd "$SKILL_DIR"
uv run --no-sync python scripts/bili_insight.py fetch BV1wZcVevENV --out "$RUN_DIR"
uv run --no-sync python scripts/bili_insight.py transcribe --out "$RUN_DIR" --model small --language zh
```

本地视频：

```bash
uv run --no-sync python scripts/bili_insight.py import-media /absolute/path/video.mp4 --out "$RUN_DIR"
uv run --no-sync python scripts/bili_insight.py frames --out "$RUN_DIR" --interval 60
uv run --no-sync python scripts/bili_insight.py frames --out "$RUN_DIR" --times 626,1977,3601
uv run --no-sync python scripts/bili_insight.py ocr --out "$RUN_DIR" --language chi_sim+eng
```

需要画面时，选新目录运行 `fetch … --mode video --height 480`。更高 height 的实际可用性受正常账户权限限制；不保证接口始终可用。接受 BV号、B站视频链接与 b23.tv 短链接，不接受任意其他域名。

用户显式提供正常登录的 Netscape Cookie 文件可传 `--cookies /private/path/cookies.txt`。不读取或打印 Cookie，不放入仓库、运行目录、交付包。yt-dlp 可能更新文件，建议提供副本。

## 输出和恢复

- `manifest.json`：来源、媒体路径、SHA256、实际时长、章节和状态。不存媒体签名地址或 Cookie。
- `asr-chunks/`：每块的完整结果，设置相同时可复用；中断后重新运行相同命令。
- `transcript.json/.txt/.srt`：原始识别文本、分段 ID、时间和置信度。`complete` 表示全部音频块处理过，不表示逐字正确。
- `analysis-packets/*.md`：分组的可读材料，必须全部读取。无语音块可为空，不补造内容。
- `frames/index.json`、`ocr.json`：采样时间与证据。OCR 初始 `verified: false`。
- `outline.json`：Codex 根据证据撰写的语义分析，不由脚本凭简介伪造。
- `reading.html`：可搜索并切换详细解读、分段释义和复核记录；后两者取决于分析 JSON 的可选字段。
- `segments.md`、`review.md`：可选的时间释义与复核记录，不是逐字稿。
- `outline.md`、`mindmap.md/.mm/.html`：渲染产物。HTML 离线可折叠，可浏览器打印；FreeMind 可导入兼容软件。

下载、ASR 设置或媒体校验和冲突时选新运行目录。每块独立识别，边界附近可能断句，核对相邻段修正。原始媒体保留；本地导入只引用路径，不复制。下载失败状态是 `blocked`，不能称为完成。

## 大纲 JSON

```json
{
  "title": "视频主题",
  "children": [
    {
      "title": "第一章主题",
      "body": "用自己的话概括核心问题。",
      "children": [
        {
          "title": "具体论点",
          "body": "论点的解释、推理与案例，必要时标出不确定处。",
          "evidence": ["s0000-0001"]
        }
      ]
    }
  ]
}
```

从实际转写取 `s…` ID，从帧索引取 `f…` ID，不用示例 ID 代替真实证据。每个叶节点至少一个证据，最多十二层；引用时间由证据推导。画面需由 Codex 看过，渲染器只能检查 ID 存在。

```bash
uv run --no-sync python scripts/bili_insight.py render --out "$RUN_DIR" --outline "$RUN_DIR/outline.json"
```

用户要求完整文案但只给外部链接时，区分技术能力与当前内容规则；不能通过写入文件绕过规则。上传素材时按适用规则整理。

## 维护参考

- [yt-dlp B站解析器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Whisper](https://github.com/openai/whisper)
- [FFmpeg](https://ffmpeg.org/ffmpeg.html)
- [Tesseract](https://github.com/tesseract-ocr/tesseract)
