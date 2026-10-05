# bili-video-insight

独立 Codex Skill：获取 B站视频或本地媒体，通过本地语音识别与关键帧分析整理带时间点的大纲和思维导图。无需平台字幕。

工具处理下载、导入、分段转写、抽帧、可选 OCR 和导出；**语义整理由使用 Skill 的 Codex 完成**，不是一条命令自动生成可信总结。

## 快速开始

需要 Python 3.10+、uv、ffmpeg/ffprobe。

```bash
uv sync --extra asr
uv run python scripts/bili_insight.py doctor
uv run python scripts/bili_insight.py fetch BVxxxxxxxxxx --out /absolute/path/task-work
uv run --extra asr python scripts/bili_insight.py transcribe --out /absolute/path/task-work
```

本地素材用 `import-media /path/video.mp4 --out …`。画面分析需下载 `--mode video` 后运行 `frames --out …`。OCR 可选，需要 Tesseract 中文语言包。

使用 `$bili-video-insight` 让 Codex 读取全部分析包、核对关键帧并生成有证据的大纲；`render` 导出 Markdown、FreeMind 和离线可折叠 HTML。当前环境具备 imagegen 时可额外制作脑图概览图片。

详见 [SKILL.md](SKILL.md) 和 [运行文档](references/workflow.md)。

## 安装为个人 Skill

将此目录放入 Codex skills 目录，或建立符号链接：

```bash
ln -s /absolute/path/bili-video-insight ~/.codex/skills/bili-video-insight
```

源码仅保留在独立仓库。目标已存在时先检查，不覆盖已有 Skill。重新打开会话后确认发现情况。

## 验证

```bash
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check scripts tests
```

下载受站点、网络与账户权限影响。工具不自动读取浏览器身份或绕过访问限制。ASR 可能误识别人名、公式和术语；采样帧不覆盖全部画面文字。结果需核对后交付。

## 发布范围

仅上传代码、Skill、测试与文档。运行目录、媒体、模型、Cookie、私有转写和用户数据不提交；发布前仍需检查暂存文件。无需 API Key，ASR 默认在本地执行。

原创代码采用 MIT 许可，依赖遵循各自许可。对 Bili-Insight 的借鉴限于公开流程思路，未复制其代码。

