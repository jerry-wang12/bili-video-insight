#!/usr/bin/env python3
"""Deterministic media helpers. Semantic outlining is performed by the consuming agent."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path


class WorkflowError(Exception):
    pass


def output_directory(value):
    directory = Path(value).expanduser().resolve()
    project = Path(__file__).resolve().parents[1]
    if directory.is_relative_to(project) and not directory.is_relative_to(project / "data"):
        raise WorkflowError("仓库内运行数据仅允许存入被 Git 忽略的 data/；也可指定仓库外目录")
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
                download_root=str(
                    output_directory(getattr(args, "model_cache", None) or out / "model-cache")
                ),
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
    for key in ("display_title", "lede"):
        if key in data and not isinstance(data[key], str):
            raise WorkflowError(f"{key} 必须为字符串")

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
        for key in ("nav_title", "display_title", "time_label", "focus", "takeaway"):
            if key in node and not isinstance(node[key], str):
                raise WorkflowError(f"{key} 必须为字符串")
        for key in ("sequence", "emphasis"):
            if key in node and (
                not isinstance(node[key], list)
                or any(not isinstance(value, str) or not value.strip() for value in node[key])
            ):
                raise WorkflowError(f"{key} 必须为非空字符串组成的数组")
        if "secondary" in node and not isinstance(node["secondary"], bool):
            raise WorkflowError("secondary 必须为布尔值")
        for child in children:
            visit(child, depth + 1)

    for child in data["children"]:
        visit(child, 1)


def validate_timeline(data, evidence, duration):
    if any(not isinstance(data.get(key, ""), str) for key in ("notice", "summary", "review")):
        raise WorkflowError("notice、summary 和 review 必须为字符串")
    rows = data.get("timeline", [])
    if not isinstance(rows, list):
        raise WorkflowError("timeline 必须为数组")
    previous_end = 0
    for row in rows:
        if not isinstance(row, dict):
            raise WorkflowError("分段释义必须为对象")
        start, end = row.get("start"), row.get("end")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (start, end)
        ):
            raise WorkflowError("分段时间必须为有限数值")
        if not 0 <= start < end <= duration + 0.01 or start < previous_end:
            raise WorkflowError("分段时间越界、重叠或顺序错误")
        if row.get("children"):
            raise WorkflowError("分段释义不使用 children")
        validate_outline({"title": "timeline", "children": [row]}, evidence)
        if not isinstance(row.get("body"), str) or not row["body"].strip():
            raise WorkflowError("每段释义需要非空 body")
        for key in row["evidence"]:
            item = evidence[key]
            point = item.get("start", item.get("time", 0))
            evidence_end = item.get("end", point)
            if point >= end or evidence_end < start:
                raise WorkflowError("分段引用的证据不在对应时间范围内")
        previous_end = end


def render_reading(out, data, evidence):
    """Render authored prose with a quiet reader; keep source IDs in text exports."""
    from string import Template

    def paragraphs(text, terms=()):
        def plain(value):
            if not terms:
                return html.escape(value)
            pattern = "|".join(re.escape(term) for term in sorted(set(terms), key=len, reverse=True))
            chunks, cursor = [], 0
            for match in re.finditer(pattern, value):
                chunks.append(html.escape(value[cursor : match.start()]))
                chunks.append("<strong>" + html.escape(match[0]) + "</strong>")
                cursor = match.end()
            return "".join(chunks) + html.escape(value[cursor:])

        def inline(value):
            chunks, cursor = [], 0
            for match in re.finditer(r"\[([^\]]+)\]\((https://[^\s)]+)\)", value):
                chunks.append(plain(value[cursor : match.start()]))
                chunks.append(f'<a href="{html.escape(match[2])}">{plain(match[1])}</a>')
                cursor = match.end()
            return "".join(chunks) + plain(value[cursor:])

        return "".join(f"<p>{inline(p)}</p>" for p in text.split("\n\n") if p.strip())

    def time_label(node):
        items = [evidence[key] for key in node.get("evidence", [])]
        points = [item.get("start", item.get("time", 0)) for item in items]
        return f'<span class="time">{timestamp(min(points))}</span>' if points else ""

    def topic(node, depth=3):
        heading = min(depth, 6)
        prose, supplements = [], []
        for p in node.get("body", "").split("\n\n"):
            (supplements if p.lstrip().startswith(("整理补充", "整理提示")) else prose).append(p)
        supplement = (
            (
                '<details class="supplement"><summary>整理补充</summary>'
                + paragraphs("\n\n".join(supplements), node.get("emphasis", []))
                + "</details>"
            )
            if supplements
            else ""
        )
        takeaway = (
            ('<p class="takeaway">' + html.escape(node["takeaway"]) + "</p>") if node.get("takeaway") else ""
        )
        return (
            '<article class="search-item topic"><div class="topic-heading">'
            f"<h{heading}>{html.escape(node.get('display_title') or node['title'])}</h{heading}>"
            + time_label(node)
            + "</div>"
            + takeaway
            + '<div class="prose">'
            + paragraphs("\n\n".join(prose), node.get("emphasis", []))
            + "</div>"
            + supplement
            + "".join(topic(child, depth + 1) for child in node.get("children", []))
            + "</article>"
        )

    chapters, navigation = [], []
    for index, chapter in enumerate(data["children"], 1):
        navigation.append(
            f'<a href="#chapter-{index}" aria-current="{str(index == 1).lower()}">'
            f'<span class="nav-number">{index:02}</span><span>'
            + html.escape(chapter.get("nav_title") or chapter["title"])
            + "</span></a>"
        )
        focus = (
            (
                '<div class="focus"><span class="focus-label">这一章的重点</span><p>'
                + html.escape(chapter["focus"])
                + "</p></div>"
            )
            if chapter.get("focus")
            else ""
        )
        sequence = (
            (
                '<ol class="sequence" aria-label="本章分析线索">'
                + "".join("<li>" + html.escape(item) + "</li>" for item in chapter["sequence"])
                + "</ol>"
            )
            if chapter.get("sequence")
            else ""
        )
        intro = (
            (
                '<details class="chapter-intro"><summary>展开本章导读</summary>'
                + paragraphs(chapter["body"])
                + "</details>"
            )
            if chapter.get("children") and chapter.get("body")
            else ""
        )
        nodes = chapter.get("children") or [chapter]
        primary = "".join(topic(node) for node in nodes if not node.get("secondary"))
        secondary = "".join(topic(node) for node in nodes if node.get("secondary"))
        background = (
            ('<details class="background"><summary>阅读背景与限定</summary>' + secondary + "</details>")
            if secondary
            else ""
        )
        interval = (
            ('<p class="time chapter-time">' + html.escape(chapter["time_label"]) + "</p>")
            if chapter.get("time_label")
            else ""
        )
        chapters.append(
            f'<section id="chapter-{index}" class="chapter"><div class="chapter-overview">'
            f'<div class="chapter-heading"><span class="chapter-number">{index:02}</span><h2>'
            + html.escape(chapter.get("display_title") or chapter["title"])
            + "</h2></div>"
            + interval
            + focus
            + sequence
            + intro
            + "</div>"
            + primary
            + background
            + "</section>"
        )

    chronological, segment_md = (
        [],
        [
            f"# {data['title']} · 分段释义",
            "",
            "> 以下内容按时间用自己的话概括，不是原文逐字转写；证据编号用于回溯。",
            "",
        ],
    )
    for index, row in enumerate(data.get("timeline", []), 1):
        interval = f"{timestamp(row['start'])}–{timestamp(row['end'])}"
        chronological.append(
            f'<article class="search-item topic" id="segment-{index}"><div class="topic-heading">'
            f'<h3>{index:02} · {html.escape(row["title"])}</h3><span class="time">{interval}</span>'
            '</div><div class="prose">' + paragraphs(row["body"]) + "</div></article>"
        )
        segment_md.extend(
            [
                f"## {index:02} · {interval} · {row['title']}",
                "",
                row["body"],
                "",
                "证据：" + ", ".join(row["evidence"]),
                "",
            ]
        )
    if chronological:
        (out / "segments.md").write_text("\n".join(segment_md), encoding="utf-8")
    else:
        (out / "segments.md").unlink(missing_ok=True)
    review = data.get("review", "")
    if review:
        (out / "review.md").write_text(f"# {data['title']} · 复核记录\n\n" + review + "\n", encoding="utf-8")
    else:
        (out / "review.md").unlink(missing_ok=True)
    notice = data.get("notice") or "依据识别材料撰写的分析性说明与分段释义，语音与解释仍需核对。"
    source_match = re.search(r"\[([^\]]+)\]\((https://[^\s)]+)\)", data.get("summary", ""))
    source = (
        (
            '<p class="source">视频来源：<a href="'
            + html.escape(source_match[2])
            + '">'
            + html.escape(source_match[1])
            + " ↗</a></p>"
        )
        if source_match
        else ""
    )
    method = paragraphs(notice) + paragraphs(data.get("summary", ""))
    if review:
        method += "<h3>复核记录</h3>" + paragraphs(review) + '<a href="review.md" download>下载复核记录</a>'
    assets = Path(__file__).resolve().parents[1] / "assets"
    document = Template((assets / "reader.html").read_text(encoding="utf-8")).substitute(
        title=html.escape(data["title"]),
        display_title=html.escape(data.get("display_title") or data["title"]),
        lede='<p class="lede">' + html.escape(data["lede"]) + "</p>" if data.get("lede") else "",
        source=source,
        css=(assets / "reader.css").read_text(encoding="utf-8"),
        js=(assets / "reader.js").read_text(encoding="utf-8"),
        navigation="".join(navigation),
        chapters="".join(chapters),
        timeline="".join(chronological),
        method=method,
        timeline_button='<button type="button" data-view="timeline" aria-pressed="false">分段阅读</button>'
        if chronological
        else "",
        segment_link='<a href="segments.md" download>分段释义</a>' if chronological else "",
    )
    (out / "reading.html").write_text(document, encoding="utf-8")


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
    duration = max((item.get("end", 0) for item in transcript["segments"]), default=0)
    if (out / "manifest.json").exists():
        duration = read_json(out / "manifest.json").get("duration", duration)
    validate_timeline(data, evidence, duration)
    title = data["title"]
    markdown = [f"# {title}", "", "> 基于转写与采样画面整理；语音识别、OCR 和解释均需核对。", ""]
    if data.get("notice"):
        markdown.extend([data["notice"], ""])
    if data.get("summary"):
        markdown.extend([data["summary"], ""])
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
        if body:
            rich = ET.SubElement(child_element, "richcontent", TYPE="NOTE")
            note_body = ET.SubElement(ET.SubElement(rich, "html"), "body")
            for paragraph in body.split("\n\n"):
                ET.SubElement(note_body, "p").text = paragraph
        contents = "".join(build(child, level + 1, child_element) for child in node.get("children", []))
        return (
            f"<li><details {'open' if level == 1 else ''}><summary>{html.escape(label)}</summary>"
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
</style><main><h1>{html.escape(title)}</h1><p>脑图用于查看主题结构；展开节点可读解释。时间点和证据编号用于回溯。</p>
<p><a href="reading.html">打开详细解读与分段释义 →</a></p><ul>{tree}</ul></main></html>"""
    (out / "mindmap.html").write_text(document, encoding="utf-8")
    render_reading(out, data, evidence)
    print("已生成详细文字大纲、脑图及 reading.html；提供 timeline 时还生成 segments.md")


# High-level library workflow; low-level commands retain their existing --out contract.
def library_directory(value=None):
    return output_directory(
        value or os.environ.get("BILI_INSIGHT_HOME") or Path(__file__).resolve().parents[1] / "data"
    )


def library_index(root):
    root = library_directory(root)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    css = (Path(__file__).resolve().parents[1] / "assets/library.css").read_text(encoding="utf-8")

    def page(title, body):
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{css}</style></head>"
            f"<body><main>{body}</main></body></html>"
        )

    def link(label, url, primary=False):
        return (
            f'<a href="{html.escape(url)}"'
            + (' class="primary"' if primary else "")
            + f">{html.escape(label)}</a>"
        )

    labels = {
        "processing": "处理中",
        "awaiting_analysis": "材料就绪，待分析",
        "completed": "分析已导出",
        "blocked": "遇到阻碍",
    }

    def frame_preview(directory, title):
        index = directory / "work/frames/index.json"
        preview = directory / "frames.html"
        if not index.is_file():
            preview.unlink(missing_ok=True)
            return "", False
        figures, missing = [], 0
        for frame in read_json(index):
            path = Path(frame["path"])
            if not path.is_absolute():
                path = directory / "work" / path
            path = path.resolve()
            if (
                not path.is_relative_to(directory)
                or not path.is_file()
                or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
            ):
                missing += 1
                continue
            url = html.escape(path.relative_to(directory).as_posix())
            time = html.escape(timestamp(frame["time"]))
            figures.append(
                f'<figure class="frame"><a href="{url}" target="_blank" rel="noopener">'
                f'<img src="{url}" alt="采样画面 · {time}" loading="lazy" decoding="async">'
                f"</a><figcaption>{time}</figcaption></figure>"
            )
        caption = f"{len(figures)} 张采样画面，点击缩略图打开原图。采样只覆盖选取的时间点。"
        unavailable = (
            f'<p class="muted">另有 {missing} 张画面缺失或不在当前任务目录，未展示。</p>' if missing else ""
        )
        content = (
            '<div class="frames">' + "".join(figures) + "</div>"
            if figures
            else '<p class="muted">目前没有可预览的画面。</p>'
        )
        preview.write_text(
            page(
                "采样画面",
                link("← 返回视频", "index.html")
                + "<h1>采样画面</h1>"
                + f'<p class="muted">{html.escape(title)}</p><p class="muted">{caption}</p>'
                + unavailable
                + content,
            ),
            encoding="utf-8",
        )
        inline = (
            '<section class="group"><div class="section-heading"><h2>采样画面</h2>'
            + link(f"查看全部 {len(figures)} 张 →", "frames.html")
            + "</div>"
            + '<div class="frames">'
            + "".join(figures[:3])
            + "</div>"
            + unavailable
            + "</section>"
        )
        return inline, True

    for path in sorted((root / "tasks").glob("*/*/task.json"), reverse=True):
        task = read_json(path)
        directory = path.parent
        title = task.get("title") or task["source_id"]
        outline = directory / "work/outline.json"
        if task["status"] == "completed" and outline.is_file():
            title = read_json(outline).get("display_title") or title
        status = labels.get(task["status"], task["status"])
        frame_content, has_frames = frame_preview(directory, title)
        links = []
        for label, name in (
            ("阅读页", "reading.html"),
            ("主题脑图", "mindmap.html"),
            ("详细解读 · Markdown", "outline.md"),
            ("分段释义 · Markdown", "segments.md"),
            ("可编辑脑图 · FreeMind", "mindmap.mm"),
            ("复核记录 · Markdown", "review.md"),
            ("下载交付包", "deliverables.zip"),
        ):
            if (directory / "outputs" / name).is_file():
                links.append((label, "outputs/" + name))
        navigation = "".join(
            link(label, url, label == "阅读页") for label, url in links if url.endswith(".html")
        )
        if has_frames:
            navigation += link("采样画面", "frames.html")
        downloads = "".join(
            "<li>" + link(label, url) + "</li>" for label, url in links if not url.endswith(".html")
        )
        downloads = (
            '<details class="downloads"><summary>下载文字、可编辑脑图与交付包</summary><ul>'
            + downloads
            + "</ul></details>"
            if downloads
            else ""
        )
        materials = []
        for label, name in (
            ("原始语音识别稿 · TXT", "transcript.txt"),
            ("带时间轴识别稿 · SRT", "transcript.srt"),
            ("结构化识别记录 · JSON", "transcript.json"),
            ("采样索引 · JSON", "frames/index.json"),
        ):
            if (directory / "work" / name).is_file():
                materials.append("<li>" + link(label, "work/" + name) + "</li>")
        manifest = directory / "work/manifest.json"
        if manifest.is_file():
            media = Path(read_json(manifest).get("media", ""))
            if media.is_file() and media.is_relative_to(directory):
                materials.append(
                    "<li>" + link("本地音视频素材", media.relative_to(directory).as_posix()) + "</li>"
                )
        extras = []
        known = {url.removeprefix("outputs/") for _, url in links} | {"mindmap.md"}
        for file in sorted((directory / "outputs").glob("*")):
            if (
                file.is_file()
                and file.name not in known
                and file.suffix.lower() in {".png", ".jpg", ".jpeg", ".txt", ".md"}
            ):
                extras.append("<li>" + link(file.name, "outputs/" + file.name) + "</li>")
        supplemental = (
            '<details class="group"><summary>补充产物</summary><ul>' + "".join(extras) + "</ul></details>"
            if extras
            else ""
        )
        error = f'<p class="error">{html.escape(task["error"])}</p>' if task.get("error") else ""
        (directory / "index.html").write_text(
            page(
                title,
                link("← 所有视频", "../../../index.html")
                + f"<h1>{html.escape(title)}</h1>"
                + f'<p class="meta"><span class="status">{html.escape(status)}</span>{html.escape(task["created_at"])}</p>'
                + error
                + '<div class="actions">'
                + navigation
                + "</div>"
                + downloads
                + frame_content
                + '<section class="group"><h2>原始材料</h2><p class="muted">语音识别稿可能有错字，与整理后的释义、解读分别保存。</p><ul>'
                + "".join(materials)
                + "</ul></section>"
                + supplemental
                + '<p class="muted">原始材料与分析源文件在 work/，阅读和分享产物在 outputs/。分析已导出不代表内容已全部校勘。</p>',
            ),
            encoding="utf-8",
        )
        url = directory.relative_to(root).as_posix() + "/index.html"
        reading = directory.relative_to(root).as_posix() + "/outputs/reading.html"
        primary = link("开始阅读", reading, True) if (directory / "outputs/reading.html").is_file() else ""
        rows.append(
            f'<li class="task"><h2>{link(title, url)}</h2><p class="meta"><span class="status">{html.escape(status)}</span>'
            f'{html.escape(task["created_at"])}</p><div class="actions">{primary}{link("全部产物与原始转写", url)}</div></li>'
        )
    (root / "index.html").write_text(
        page(
            "视频分析资料库",
            '<h1>视频分析资料库</h1><p class="muted">从这里查阅所有视频的解读、脑图与原始识别材料。</p>'
            + (
                '<ul class="tasks">' + "".join(rows) + "</ul>"
                if rows
                else '<p class="empty">暂无任务。在项目目录运行 <code>./bili run 视频链接</code> 开始。</p>'
            ),
        ),
        encoding="utf-8",
    )
    print(f"资料库：{root / 'index.html'}")


def run_task(args):
    root = library_directory(args.library)
    local = Path(args.source).expanduser()
    is_local = local.is_file()
    source = str(local.resolve()) if is_local else normalized_source(args.source)
    match = re.search(r"BV[0-9A-Za-z]{10}", source) if not is_local else None
    source_id = (
        match.group()
        if match
        else ("local-" if is_local else "link-")
        + hashlib.sha256((digest(local) if is_local else source).encode()).hexdigest()[:12]
    )
    settings = {
        "source": source,
        "mode": args.mode,
        "model": args.model,
        "language": args.language,
        "interval": args.interval,
        "height": args.height,
    }
    version = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]
    if args.new:
        version += "-" + uuid.uuid4().hex[:8]
    directory = root / "tasks" / source_id / version
    directory.mkdir(parents=True, exist_ok=True)
    # flock is released by the OS even if the process is interrupted.
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowError("同一任务正在运行；等待其完成再恢复") from exc
        task_path = directory / "task.json"
        task = (
            read_json(task_path)
            if task_path.exists()
            else {
                "schema_version": 1,
                "source_id": source_id,
                "settings": settings,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        if task["settings"] != settings:
            raise WorkflowError("任务参数冲突；使用 --new 新建任务")
        if task.get("status") in {"awaiting_analysis", "completed"}:
            print(f"已复用任务：{directory}")
            library_index(root)
            return
        task.update(status="processing", error=None)
        save_json(task_path, task)
        work = directory / "work"
        namespace = argparse.Namespace(
            out=str(work),
            source=source,
            mode=args.mode,
            height=args.height,
            cookies=args.cookies,
            model=args.model,
            language=args.language,
            chunk_seconds=600,
            device="cpu",
            compute_type="int8",
            model_cache=str(root / "cache/models"),
            interval=args.interval,
            times=None,
            max_frames=120,
        )
        try:
            if is_local:
                import_media(namespace)
            else:
                fetch(namespace)
            task["title"] = load_manifest(namespace).get("title", source_id)
            transcribe(namespace)
            if args.mode == "video":
                frames(namespace)
            task["status"] = "awaiting_analysis"
        except Exception as exc:
            task.update(status="blocked", error=safe_message(exc))
            raise
        finally:
            save_json(task_path, task)
            library_index(root)
        print(f"材料已就绪：{work}\n请让 Codex 读取所有 analysis-packets 并生成 work/outline.json。")
        print(f"完成分析后：./bili finish '{directory}'")


def adopt_task(args):
    """Copy a completed legacy run into the library without reacquiring material."""
    legacy = Path(args.directory).expanduser().resolve()
    manifest = read_json(legacy / "manifest.json")
    transcript = read_json(legacy / "transcript.json")
    if not transcript.get("complete"):
        raise WorkflowError("adopt 需要已完成的转写；请先完成原目录的 transcribe")
    source = manifest["source"]
    match = re.search(r"BV[0-9A-Za-z]{10}", source)
    source_id = match.group() if match else "import-" + hashlib.sha256(source.encode()).hexdigest()[:12]
    root = library_directory(args.library)
    version = "imported-" + hashlib.sha256(str(legacy).encode()).hexdigest()[:12]
    directory = root / "tasks" / source_id / version
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowError("同一任务正在接入；请等待其完成") from exc
        if (directory / "task.json").is_file():
            print(f"已接入，保留现有分析：{directory}")
            library_index(root)
            return directory
        stage = directory / (".import-" + uuid.uuid4().hex[:8])
        stage.mkdir()
        try:
            for name in (
                "manifest.json",
                "transcript.json",
                "transcript.txt",
                "transcript.srt",
                "asr-status.json",
                "ocr.json",
            ):
                if (legacy / name).is_file():
                    shutil.copy2(legacy / name, stage / name)
            for file in legacy.glob("outline*.json"):
                shutil.copy2(file, stage / file.name)
            for name in ("asr-chunks", "analysis-packets"):
                if (legacy / name).is_dir():
                    shutil.copytree(legacy / name, stage / name)
            media = Path(manifest["media"])
            if not media.is_absolute():
                media = legacy / media
            if media.suffix.lower() not in {
                ".mp4",
                ".m4a",
                ".mp3",
                ".wav",
                ".webm",
                ".flv",
                ".mov",
                ".mkv",
                ".ogg",
                ".flac",
                ".opus",
                ".avi",
                ".m4v",
                ".aac",
            }:
                raise WorkflowError("manifest 中的媒体扩展名不受支持")
            if not media.is_file() or digest(media) != manifest["sha256"]:
                raise WorkflowError("原媒体不存在或校验和不一致；停止接入，不重新下载")
            media_name = "media" + media.suffix.lower()
            shutil.copy2(media, stage / media_name)
            manifest["media"] = str(directory / "work" / media_name)
            save_json(stage / "manifest.json", manifest)
            frame_index = legacy / "frames/index.json"
            frame_paths = {}
            if frame_index.is_file():
                frames_data = read_json(frame_index)
                for frame in frames_data:
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", frame["id"]):
                        raise WorkflowError("画面 ID 无效")
                    path = Path(frame["path"])
                    if not path.is_absolute():
                        path = legacy / path
                    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not path.is_file():
                        raise WorkflowError("原采样画面缺失或格式不支持；停止接入")
                    name = frame["id"] + path.suffix.lower()
                    (stage / "frames").mkdir(exist_ok=True)
                    shutil.copy2(path, stage / "frames" / name)
                    frame["path"] = str(directory / "work/frames" / name)
                    frame_paths[frame["id"]] = frame["path"]
                save_json(stage / "frames/index.json", frames_data)
            if (stage / "ocr.json").is_file():
                ocr_data = read_json(stage / "ocr.json")
                for row in ocr_data:
                    if row.get("id") in frame_paths:
                        row["path"] = frame_paths[row["id"]]
                save_json(stage / "ocr.json", ocr_data)
            stage.replace(directory / "work")
            save_json(
                directory / "task.json",
                {
                    "schema_version": 1,
                    "source_id": source_id,
                    "settings": {"source": source, "imported": True},
                    "title": manifest.get("title", source_id),
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "status": "awaiting_analysis",
                    "adopted_from": str(legacy),
                    "error": None,
                },
            )
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    library_index(root)
    print(f"已接入：{directory}\n已有 work/outline.json 时，执行 ./bili finish '{directory}'。")
    return directory


def managed_task(value):
    directory = output_directory(value)
    if directory.parents[1].name != "tasks" or not (directory / "task.json").is_file():
        raise WorkflowError("请指定资料库 tasks/来源/版本 下包含 task.json 的任务目录")
    return directory


def finish_task(args):
    directory = managed_task(args.task)
    task_path = directory / "task.json"
    task = read_json(task_path)
    work = directory / "work"
    render(argparse.Namespace(out=str(work), outline=str(work / "outline.json")))
    outputs = directory / "outputs"
    outputs.mkdir(exist_ok=True)
    for name in ("outline.md", "mindmap.md", "mindmap.mm", "mindmap.html", "reading.html"):
        shutil.copy2(work / name, outputs / name)
    for name in ("segments.md", "review.md"):
        if (work / name).is_file():
            shutil.copy2(work / name, outputs / name)
        else:
            (outputs / name).unlink(missing_ok=True)
    (outputs / "deliverables.zip").unlink(missing_ok=True)
    task.update(status="completed", error=None)
    save_json(task_path, task)
    library_index(directory.parents[2])
    print(f"交付物：{outputs}")


def bundle_task(args):
    directory = managed_task(args.task)
    task = read_json(directory / "task.json")
    if task.get("status") != "completed":
        raise WorkflowError("先完成 finish 再打包")
    outputs = directory / "outputs"
    # Explicit allowlist: never include transcripts, media, credentials or arbitrary files.
    names = ["outline.md", "mindmap.md", "mindmap.mm", "mindmap.html"]
    names.extend(name for name in ("reading.html", "segments.md", "review.md") if (outputs / name).is_file())
    for name in names:
        if not (outputs / name).is_file():
            raise WorkflowError(f"缺少交付物：{name}")
    with zipfile.ZipFile(outputs / "deliverables.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(outputs / name, name)
    library_index(directory.parents[2])
    print(f"交付包：{outputs / 'deliverables.zip'}")


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor").set_defaults(func=doctor)
    run = commands.add_parser("run", help="下载/导入、转写并整理到统一资料库")
    run.add_argument("source", help="BV号、B站链接或本地媒体文件")
    run.add_argument("--library", help="覆盖 BILI_INSIGHT_HOME 和默认资料库")
    run.add_argument("--mode", choices=["audio", "video"], default="audio")
    run.add_argument("--height", type=positive, default=480)
    run.add_argument("--model", default="small")
    run.add_argument("--language", default="zh")
    run.add_argument("--interval", type=positive, default=60)
    run.add_argument("--cookies")
    run.add_argument("--new", action="store_true", help="创建独立任务，不复用此前结果")
    run.set_defaults(func=run_task)
    listing = commands.add_parser("list", help="重建并显示离线资料库索引")
    listing.add_argument("--library")
    listing.set_defaults(func=lambda args: library_index(library_directory(args.library)))
    adoption = commands.add_parser("adopt", help="接入已有完整转写的低层目录，不重新下载或转写")
    adoption.add_argument("directory", help="包含 manifest.json 与完整 transcript.json 的现有目录")
    adoption.add_argument("--library")
    adoption.set_defaults(func=adopt_task)
    for name, function in (("finish", finish_task), ("bundle", bundle_task)):
        child = commands.add_parser(name)
        child.add_argument("task", help="run 输出的任务目录（包含 task.json）")
        child.set_defaults(func=function)
    for name, function in (
        ("fetch", fetch),
        ("import-media", import_media),
        ("transcribe", transcribe),
        ("frames", frames),
        ("ocr", ocr),
        ("render", render),
    ):
        child = commands.add_parser(name)
        child.add_argument("--out", required=True, help="项目 data/ 内或仓库外的独立运行目录")
        child.set_defaults(func=function)
        if name in {"fetch", "import-media"}:
            child.add_argument("source")
        if name == "fetch":
            child.add_argument("--mode", choices=["audio", "video"], default="audio")
            child.add_argument("--height", type=positive, default=480)
            child.add_argument("--cookies", help="用户显式提供的 Netscape cookie 文件；不会自动读取浏览器")
        if name == "transcribe":
            child.add_argument("--model-cache", help="共享模型缓存目录；默认位于运行目录")
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
