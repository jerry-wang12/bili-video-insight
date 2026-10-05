#!/usr/bin/env python3
"""Deterministic media helpers. Semantic outlining is performed by the consuming agent."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class WorkflowError(Exception):
    pass


def output_directory(value):
    directory = Path(value).expanduser().resolve()
    if directory.is_relative_to(Path(__file__).resolve().parents[1]):
        raise WorkflowError("运行数据不能写入 Skill 仓库；请指定独立的 --out 目录")
    return directory


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_message(value):
    # Signed media URLs and credentials must not appear in diagnostics.
    return re.sub(r"https?://[^\s\"'<>]+", "[URL omitted]", str(value))


def run_command(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise WorkflowError(f"缺少程序：{command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise WorkflowError(safe_message(exc.stderr[-2500:])) from exc
    return result.stdout


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def timestamp(seconds, srt=False):
    millis = round(float(seconds) * 1000)
    hours, rest = divmod(millis, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}" + (f",{ms:03}" if srt else "")


def probe(path):
    data = json.loads(
        run_command(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    )
    duration = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise WorkflowError("媒体没有有效时长")
    return {"duration": duration, "streams": [s.get("codec_type") for s in data.get("streams", [])]}


def manifest_path(args):
    return output_directory(args.out) / "manifest.json"


def load_manifest(args):
    path = manifest_path(args)
    if not path.exists():
        raise WorkflowError("请先运行 fetch 或 import-media")
    return read_json(path)


def normalized_source(source):
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", source):
        return f"https://www.bilibili.com/video/{source}/"
    match = re.fullmatch(r"https://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})/?(?:\?[^\s]*)?", source)
    if match:
        return f"https://www.bilibili.com/video/{match[1]}/"
    if re.fullmatch(r"https://b23\.tv/[0-9A-Za-z]+/?", source):
        return source
    raise WorkflowError("仅接受 BV 号、B站视频 HTTPS 链接或 b23.tv 短链接；本地文件用 import-media")


def doctor(args):
    report = {
        "python": sys.version.split()[0],
        "python_architecture": platform.machine(),
        "executables": {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "tesseract")},
        "modules": {
            name: importlib.util.find_spec(name) is not None for name in ("yt_dlp", "faster_whisper")
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def fetch(args):
    source = normalized_source(args.source)
    out = Path(args.out).resolve()
    target = manifest_path(args)
    if target.exists():
        existing = read_json(target)
        if existing.get("source") != source or existing.get("acquisition_mode") != args.mode:
            raise WorkflowError("输出目录已属于其他来源或下载模式；请选新目录")
        media = existing.get("media")
        if existing.get("status") == "acquired" and media and Path(media).is_file():
            if digest(media) != existing.get("sha256"):
                raise WorkflowError("已有媒体的校验和变化；请选新目录重新导入")
            print("复用已验证媒体：" + media)
            return
    if importlib.util.find_spec("yt_dlp") is None:
        raise WorkflowError("缺少 yt-dlp；在项目目录运行 uv sync")
    from yt_dlp import YoutubeDL

    class Logger:
        def debug(self, msg):
            pass

        def warning(self, msg):
            print(safe_message(msg), file=sys.stderr)

        def error(self, msg):
            print(safe_message(msg), file=sys.stderr)

    cookie = None
    if args.cookies:
        cookie = Path(args.cookies).expanduser().resolve()
        if not cookie.is_file():
            raise WorkflowError("Cookie 文件不存在")
        if cookie.is_relative_to(out):
            raise WorkflowError("Cookie 文件不能放在运行输出目录")
        if cookie.is_relative_to(Path(__file__).resolve().parents[1]):
            raise WorkflowError("Cookie 文件不能放在 Skill 仓库")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "source": source, "acquisition_mode": args.mode, "status": "fetching"}
    save_json(target, manifest)
    selector = (
        "bestaudio/best"
        if args.mode == "audio"
        else f"bestvideo[height<={args.height}]+bestaudio/best[height<={args.height}]"
    )
    paths = []

    def completed(info):
        if info.get("status") == "finished" and info.get("info_dict", {}).get("filepath"):
            paths.append(info["info_dict"]["filepath"])

    options = {
        "format": selector,
        "outtmpl": str(out / "media.%(ext)s"),
        "noplaylist": True,
        "merge_output_format": "mp4",
        "logger": Logger(),
        "quiet": True,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "socket_timeout": 20,
        "postprocessor_hooks": [completed],
        "overwrites": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
    if cookie:
        options["cookiefile"] = str(cookie)
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(source, download=True)
            candidates = paths + [info.get("filepath", ""), downloader.prepare_filename(info)]
            candidates += [
                str(p) for p in out.glob("media.*") if p.suffix in {".mp4", ".m4a", ".webm", ".flv", ".mp3"}
            ]
        media = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
        if media is None:
            raise WorkflowError("下载器未产出媒体文件")
        details = probe(media)
        if args.mode == "video" and "video" not in details["streams"]:
            raise WorkflowError("请求视频但下载结果没有视频流")
        if "audio" not in details["streams"]:
            raise WorkflowError("下载结果没有音轨")
        manifest.update(status="acquired", media=str(media.resolve()), sha256=digest(media), **details)
        manifest["title"] = info.get("title", "视频")
        manifest["chapters"] = [
            {"start": c.get("start_time"), "end": c.get("end_time"), "title": c.get("title")}
            for c in info.get("chapters") or []
        ]
        manifest["subtitle_used"] = False
        save_json(target, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception as exc:
        manifest.update(status="blocked", error=safe_message(exc))
        save_json(target, manifest)
        raise WorkflowError("下载失败；已保存状态。不要自动更换身份或代理重试。" + safe_message(exc)) from exc


def import_media(args):
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise WorkflowError("本地媒体文件不存在")
    details = probe(source)
    if "audio" not in details["streams"]:
        raise WorkflowError("媒体没有音轨")
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "media": str(source),
        "title": source.stem,
        "status": "acquired",
        "sha256": digest(source),
        "chapters": [],
        "subtitle_used": False,
        **details,
    }
    target = manifest_path(args)
    if target.exists() and read_json(target).get("sha256") != manifest["sha256"]:
        raise WorkflowError("输出目录属于另一份媒体；请选新目录")
    save_json(target, manifest)
    print("媒体已导入：" + str(target))


def export_transcript(out, segments, complete):
    save_json(out / "transcript.json", {"complete": complete, "segments": segments})
    lines, cues = [], []
    for i, s in enumerate(segments, 1):
        text = s["text"].strip()
        lines.append(f"[{s['id']}] [{timestamp(s['start'])}–{timestamp(s['end'])}] {text}")
        cues.append(f"{i}\n{timestamp(s['start'], True)} --> {timestamp(s['end'], True)}\n{text}\n")
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "transcript.srt").write_text("\n".join(cues), encoding="utf-8")


def transcribe(args):
    manifest = load_manifest(args)
    media = Path(manifest.get("media", ""))
    if manifest.get("status") != "acquired" or not media.is_file():
        raise WorkflowError("没有可用的已下载媒体")
    if digest(media) != manifest["sha256"]:
        raise WorkflowError("媒体校验和变化，拒绝复用旧转写")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WorkflowError("缺少 faster-whisper；运行 uv sync --extra asr。首次使用会下载模型。") from exc
    out = Path(args.out).resolve()
    work = out / "asr-chunks"
    work.mkdir(exist_ok=True)
    settings = {
        "algorithm_version": 1,
        "sha256": manifest["sha256"],
        "model": args.model,
        "language": args.language,
        "chunk_seconds": args.chunk_seconds,
        "device": args.device,
        "compute_type": args.compute_type,
    }
    state_path = work / "settings.json"
    if state_path.exists() and read_json(state_path) != settings:
        raise WorkflowError("转写设置变化，不能混用缓存；请选新输出目录")
    save_json(state_path, settings)
    save_json(out / "asr-status.json", {"complete": False, "status": "processing", "settings": settings})
    total = math.ceil(manifest["duration"] / args.chunk_seconds)
    # A sub-two-second tail can trigger hallucinated text; merge it into the preceding block.
    if total > 1 and manifest["duration"] - (total - 1) * args.chunk_seconds < 2:
        total -= 1
    model = None
    all_segments = []
    for index in range(total):
        offset = index * args.chunk_seconds
        chunk_duration = manifest["duration"] - offset if index == total - 1 else args.chunk_seconds
        result_path = work / f"{index:04}.json"
        if result_path.exists():
            all_segments.extend(read_json(result_path)["segments"])
            continue
        if model is None:
            print("正在加载本地识别模型；首次使用需要下载模型文件。", flush=True)
            model = WhisperModel(
                args.model,
                device=args.device,
                compute_type=args.compute_type,
                download_root=str(out / "model-cache"),
            )
        wav = work / f"{index:04}.wav"
        run_command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                str(offset),
                "-i",
                str(media),
                "-t",
                str(chunk_duration),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav),
            ]
        )
        iterator, _ = model.transcribe(
            str(wav), language=args.language, beam_size=5, vad_filter=True, condition_on_previous_text=False
        )
        current = []
        for local_index, segment in enumerate(iterator):
            start, end = max(0, segment.start), min(chunk_duration, segment.end)
            if end <= start or not segment.text.strip():
                continue
            current.append(
                {
                    "id": f"s{index:04}-{local_index:04}",
                    "start": offset + start,
                    "end": offset + end,
                    "text": segment.text.strip(),
                    "avg_logprob": segment.avg_logprob,
                    "no_speech_prob": segment.no_speech_prob,
                }
            )
        save_json(result_path, {"start": offset, "end": offset + chunk_duration, "segments": current})
        wav.unlink(missing_ok=True)
        all_segments.extend(current)
        export_transcript(out, all_segments, complete=index == total - 1)
        print(f"已转写 {index + 1}/{total} 段", flush=True)
    export_transcript(out, all_segments, complete=True)
    packet_dir = out / "analysis-packets"
    packet_dir.mkdir(exist_ok=True)
    for index in range(total):
        selected = [s for s in all_segments if s["id"].startswith(f"s{index:04}-")]
        content = "\n".join(f"[{s['id']}] [{timestamp(s['start'])}] {s['text']}" for s in selected)
        (packet_dir / f"{index:04}.md").write_text(content + "\n", encoding="utf-8")
    save_json(
        out / "asr-status.json",
        {
            "complete": True,
            "status": "completed",
            "processed_duration": manifest["duration"],
            "segment_count": len(all_segments),
            "settings": settings,
        },
    )
    print("转写完成；识别结果仍需核对术语、引用与低置信度片段。")


def frames(args):
    manifest = load_manifest(args)
    media = Path(manifest.get("media", ""))
    if not media.is_file() or "video" not in probe(media)["streams"]:
        raise WorkflowError("当前来源没有视频流；需另行 fetch --mode video 或导入本地视频")
    if digest(media) != manifest["sha256"]:
        raise WorkflowError("媒体校验和变化，拒绝复用帧缓存")
    directory = Path(args.out).resolve() / "frames"
    directory.mkdir(exist_ok=True)
    times = (
        [float(t) for t in args.times.split(",")]
        if args.times
        else list(range(0, math.ceil(manifest["duration"]), args.interval))
    )
    times = sorted(set(times))
    if any(not math.isfinite(t) or t < 0 or t >= manifest["duration"] for t in times):
        raise WorkflowError("抽帧时间必须在媒体时长内")
    if len(times) > args.max_frames:
        raise WorkflowError(f"计划抽取 {len(times)} 帧，超过 --max-frames；请增加间隔或显式提高上限")
    entries = []
    for t in times:
        target = directory / f"frame-{round(t * 1000):010}.jpg"
        if not target.exists():
            run_command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-ss",
                    str(t),
                    "-i",
                    str(media),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2",
                    str(target),
                ]
            )
        if not target.is_file():
            raise WorkflowError(f"无法生成 {t} 秒处的画面")
        entries.append({"id": f"f{round(t * 1000):010}", "time": t, "path": str(target)})
    previous = read_json(directory / "index.json") if (directory / "index.json").exists() else []
    combined = {item["id"]: item for item in previous + entries}
    save_json(directory / "index.json", sorted(combined.values(), key=lambda x: x["time"]))
    print(f"已抽取 {len(entries)} 帧；这只是采样，不能声称覆盖所有画面文字。")


def ocr(args):
    directory = Path(args.out).resolve() / "frames"
    if not (directory / "index.json").exists():
        raise WorkflowError("请先抽取视频帧")
    results = []
    for frame in read_json(directory / "index.json"):
        text = run_command(["tesseract", frame["path"], "stdout", "-l", args.language, "--psm", "11"])
        results.append({**frame, "text": text.strip(), "verified": False})
    save_json(Path(args.out).resolve() / "ocr.json", results)
    print("OCR 完成；画面文字尚未人工核对。")


def validate_outline(data, evidence):
    if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not data["title"].strip():
        raise WorkflowError("大纲需要非空 title")
    if not isinstance(data.get("children"), list) or not data["children"]:
        raise WorkflowError("大纲需要非空 children 数组")

    def visit(node, depth):
        if depth > 12 or not isinstance(node, dict):
            raise WorkflowError("大纲层级过深或节点格式错误")
        if not isinstance(node.get("title"), str) or not node["title"].strip():
            raise WorkflowError("每个节点需要非空 title")
        ids = node.get("evidence", [])
        children = node.get("children", [])
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in evidence for i in ids):
            raise WorkflowError("节点包含未知 evidence ID")
        if not isinstance(children, list):
            raise WorkflowError("children 必须为数组")
        if not children and not ids:
            raise WorkflowError("每个叶节点必须引用至少一个转写或画面 evidence ID")
        if "body" in node and not isinstance(node["body"], str):
            raise WorkflowError("body 必须为字符串")
        for child in children:
            visit(child, depth + 1)

    for child in data["children"]:
        visit(child, 1)


def render(args):
    out = Path(args.out).resolve()
    transcript = read_json(out / "transcript.json")
    if not transcript.get("complete"):
        raise WorkflowError("转写尚未完成；不可渲染为完整分析")
    evidence = {s["id"]: s for s in transcript["segments"]}
    frame_index = out / "frames" / "index.json"
    if frame_index.exists():
        evidence.update({f["id"]: f for f in read_json(frame_index)})
    data = read_json(args.outline)
    validate_outline(data, evidence)
    title = data["title"]
    markdown = [f"# {title}", "", "> 基于转写与采样画面整理；语音识别、OCR 和解释均需核对。", ""]
    mm_markdown = [f"# {title}", ""]
    root = ET.Element("map", version="1.0.1")
    root_node = ET.SubElement(root, "node", TEXT=title)

    def citation(node):
        ids = node.get("evidence", [])
        if not ids:
            return ""
        times = [evidence[i].get("start", evidence[i].get("time", 0)) for i in ids]
        return f"[{timestamp(min(times))}] " + ", ".join(ids)

    def build(node, level, parent):
        label, body = node["title"], node.get("body", "")
        cite = citation(node)
        markdown.extend(["#" * min(level + 1, 6) + " " + label, "", body, "", cite, ""])
        mm_markdown.append("  " * (level - 1) + "- " + label + (" " + cite if cite else ""))
        child_element = ET.SubElement(parent, "node", TEXT=label + (" " + cite if cite else ""))
        contents = "".join(build(child, level + 1, child_element) for child in node.get("children", []))
        return (
            f"<li><details open><summary>{html.escape(label)}</summary>"
            f"<p>{html.escape(body)}</p><small>{html.escape(cite)}</small>"
            + (f"<ul>{contents}</ul>" if contents else "")
            + "</details></li>"
        )

    tree = "".join(build(node, 1, root_node) for node in data["children"])
    (out / "outline.md").write_text("\n".join(markdown), encoding="utf-8")
    (out / "mindmap.md").write_text("\n".join(mm_markdown) + "\n", encoding="utf-8")
    ET.ElementTree(root).write(out / "mindmap.mm", encoding="utf-8", xml_declaration=True)
    document = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font:16px/1.7 system-ui,sans-serif;background:#f5f5f0;color:#24352d;margin:0;padding:32px}}
main{{max-width:1200px;margin:auto}}h1{{font-size:28px}}ul{{list-style:none;padding-left:24px;border-left:2px solid #bacabc}}
li{{margin:12px 0}}details{{background:#fff;border:1px solid #dce4dc;border-radius:10px;padding:12px 16px}}
summary{{cursor:pointer;font-weight:650}}p{{white-space:pre-wrap;margin:8px 0}}small{{color:#55695b}}
@media print{{body{{background:white;padding:0}}details{{break-inside:avoid}}}}
</style><main><h1>{html.escape(title)}</h1><p>点击节点展开或收起；时间点与证据编号对应原始转写或采样画面。</p>
<ul>{tree}</ul></main></html>"""
    (out / "mindmap.html").write_text(document, encoding="utf-8")
    print("已生成 outline.md、mindmap.md、mindmap.mm、mindmap.html")


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor").set_defaults(func=doctor)
    for name, function in (
        ("fetch", fetch),
        ("import-media", import_media),
        ("transcribe", transcribe),
        ("frames", frames),
        ("ocr", ocr),
        ("render", render),
    ):
        child = commands.add_parser(name)
        child.add_argument("--out", required=True, help="独立运行目录；不要存入待发布仓库")
        child.set_defaults(func=function)
        if name in {"fetch", "import-media"}:
            child.add_argument("source")
        if name == "fetch":
            child.add_argument("--mode", choices=["audio", "video"], default="audio")
            child.add_argument("--height", type=positive, default=480)
            child.add_argument("--cookies", help="用户显式提供的 Netscape cookie 文件；不会自动读取浏览器")
        if name == "transcribe":
            child.add_argument("--model", default="small", help="faster-whisper 模型名或本地模型目录")
            child.add_argument("--language", default="zh")
            child.add_argument("--chunk-seconds", type=positive, default=600)
            child.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
            child.add_argument("--compute-type", default="int8")
        if name == "frames":
            child.add_argument("--interval", type=positive, default=60)
            child.add_argument("--times", help="逗号分隔的秒数；指定时忽略 interval")
            child.add_argument("--max-frames", type=positive, default=120)
        if name == "ocr":
            child.add_argument("--language", default="chi_sim+eng")
        if name == "render":
            child.add_argument("--outline", required=True, help="Codex 基于证据生成的大纲 JSON")
    return p


def main():
    args = parser().parse_args()
    try:
        if hasattr(args, "out"):
            args.out = str(output_directory(args.out))
        args.func(args)
    except Exception as exc:  # noqa: BLE001 -- CLI boundary handles optional engine and network failures.
        if args.command == "transcribe":
            # Only save failure state after a valid run directory has been established.
            try:
                directory = output_directory(args.out)
            except WorkflowError:
                directory = None
            if directory and (directory / "manifest.json").exists():
                save_json(
                    directory / "asr-status.json",
                    {"complete": False, "status": "blocked", "error": safe_message(exc)},
                )
        print("错误：" + safe_message(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
