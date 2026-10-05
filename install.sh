#!/bin/sh
# Explicit setup entry point. Analysis commands never install system packages themselves.
set -eu
BILI_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
BILI_SKILL_TARGET="${CODEX_HOME:-$HOME/.codex}/skills/bili-video-insight"
if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

fail() { printf '%s\n' "$*" >&2; exit 1; }
check_only=false
case "${1:-}" in
    '') ;;
    --check) check_only=true ;;
    --help) printf '%s\n' '用法：./install.sh [--check]' '自动准备依赖并注册 Codex Skill；--check 只检查，不安装。'; exit 0 ;;
    *) fail '未知参数；使用 ./install.sh --help 查看用法。' ;;
esac
[ "$#" -le 1 ] || fail '只支持一个可选参数。'
case "$(uname -s)" in Darwin|Linux) ;; *) fail '安装脚本支持 macOS/Linux。' ;; esac

# Check conflicts before any downloads or system changes. Never replace another installation.
registered=false
if [ -e "$BILI_SKILL_TARGET" ] || [ -L "$BILI_SKILL_TARGET" ]; then
    if [ -d "$BILI_SKILL_TARGET" ] && [ "$BILI_SKILL_TARGET" -ef "$BILI_PROJECT_DIR" ]; then
        registered=true
    else
        fail "Skill 位置已有其他文件或项目：${BILI_SKILL_TARGET}；请先检查，不会覆盖。"
    fi
fi

missing_ffmpeg=false
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    missing_ffmpeg=true
fi
if "$check_only"; then
    command -v uv >/dev/null 2>&1 && printf '%s\n' 'uv：已安装' || printf '%s\n' 'uv：待安装'
    "$missing_ffmpeg" && printf '%s\n' 'FFmpeg/ffprobe：待安装' || printf '%s\n' 'FFmpeg/ffprobe：已安装'
    [ -x "$BILI_PROJECT_DIR/.venv/bin/python" ] && printf '%s\n' 'Python 项目环境：已存在' || printf '%s\n' 'Python 项目环境：待安装'
    "$registered" && printf '%s\n' 'Skill：已注册' || printf '%s\n' 'Skill：待注册'
    exit 0
fi

if "$missing_ffmpeg"; then
    printf '%s\n' '正在安装 FFmpeg…'
    case "$(uname -s)" in
        Darwin)
            command -v brew >/dev/null 2>&1 || fail '自动安装 FFmpeg 需要 Homebrew：https://brew.sh；也可手动安装 FFmpeg 后重跑。'
            brew install ffmpeg
            ;;
        Linux)
            command -v apt-get >/dev/null 2>&1 || fail '自动安装 FFmpeg 支持 apt-get；其他发行版请先用系统包管理器安装 ffmpeg。'
            if [ "$(id -u)" -eq 0 ]; then
                apt-get update
                apt-get install -y ffmpeg
            else
                command -v sudo >/dev/null 2>&1 || fail '安装 FFmpeg 需要 sudo 或管理员先行安装。'
                sudo apt-get update
                sudo apt-get install -y ffmpeg
            fi
            ;;
    esac
fi
command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 || fail 'FFmpeg 安装未成功，停止注册。'

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' '正在通过官方安装器安装 uv…'
    command -v curl >/dev/null 2>&1 || fail '需要 curl 下载 uv：https://docs.astral.sh/uv/getting-started/installation/'
    BILI_UV_INSTALLER=$(mktemp)
    trap 'rm -f "$BILI_UV_INSTALLER"' 0
    trap 'exit 1' HUP INT TERM
    curl --proto '=https' --tlsv1.2 -fLsS https://astral.sh/uv/install.sh -o "$BILI_UV_INSTALLER"
    UV_NO_MODIFY_PATH=1 sh "$BILI_UV_INSTALLER"
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    command -v uv >/dev/null 2>&1 || fail 'uv 安装未成功，停止注册。'
fi

cd "$BILI_PROJECT_DIR"
printf '%s\n' '正在准备 Python 与本地语音识别依赖…'
if [ -x .venv/bin/python ]; then
    uv sync --locked --extra asr --inexact --python "$BILI_PROJECT_DIR/.venv/bin/python"
else
    # Stable wheel availability; uv downloads Python if this version is absent.
    uv sync --locked --extra asr --inexact --python 3.12
fi
.venv/bin/python -c 'import faster_whisper, yt_dlp'
./bili doctor
if ! "$registered"; then
    mkdir -p "$(dirname -- "$BILI_SKILL_TARGET")"
    ln -s "$BILI_PROJECT_DIR" "$BILI_SKILL_TARGET"
fi
printf '%s\n' "安装完成：$BILI_SKILL_TARGET" '重新打开 Codex 会话，即可说：使用 $bili-video-insight 分析这个视频：<链接>。' '默认生成详细解读、分段释义、分支脑图；语音模型在首次分析时自动下载。'
