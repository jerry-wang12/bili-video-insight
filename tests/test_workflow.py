import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bili_insight", Path(__file__).parents[1] / "scripts/bili_insight.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_source_scope_and_tracking_removed():
    assert module.normalized_source("BV1wZcVevENV") == "https://www.bilibili.com/video/BV1wZcVevENV/"
    assert module.normalized_source(
        "https://www.bilibili.com/video/BV1wZcVevENV/?vd_source=private"
    ) == module.normalized_source("BV1wZcVevENV")
    with pytest.raises(module.WorkflowError):
        module.normalized_source("https://other.example/video/BV1wZcVevENV/")


def test_run_data_cannot_enter_skill_repository():
    with pytest.raises(module.WorkflowError):
        module.output_directory(Path(__file__).parents[1] / "private-run")


def test_default_library_is_project_data_independent_of_cwd(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("BILI_INSIGHT_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert module.library_directory() == project / "data"
    assert module.output_directory(project / "data/tasks/example/work") == project / "data/tasks/example/work"
    monkeypatch.setenv("BILI_INSIGHT_HOME", str(tmp_path / "custom"))
    assert module.library_directory() == tmp_path / "custom"
    assert module.library_directory(tmp_path / "explicit") == tmp_path / "explicit"


def test_srt_rounding_rolls_over():
    assert module.timestamp(59.9996, True) == "00:01:00,000"


def test_evidence_required_and_unknown_rejected():
    for evidence in ([], ["invented"]):
        with pytest.raises(module.WorkflowError):
            module.validate_outline(
                {"title": "Topic", "children": [{"title": "Claim", "evidence": evidence}]}, {}
            )


def test_render_escapes_content_and_preserves_all_nodes(tmp_path):
    segments = [{"id": "s1", "start": 62, "end": 65, "text": "source"}]
    module.export_transcript(tmp_path, segments, True)
    data = {
        "title": "Topic <&>",
        "children": [
            {"title": f"Claim {i}", "body": "<script>alert(1)</script>", "evidence": ["s1"]}
            for i in range(20)
        ],
    }
    source = tmp_path / "outline.json"
    module.save_json(source, data)
    module.render(SimpleNamespace(out=str(tmp_path), outline=str(source)))
    page = (tmp_path / "mindmap.html").read_text()
    assert "<script>alert(1)</script>" not in page
    assert r"\u003cscript>alert(1)\u003c/script>" in page
    assert "Claim 19" in page
    assert "00:01:02" in page
    assert len(ET.parse(tmp_path / "mindmap.mm").findall(".//node")) == 21


def test_render_rejects_partial_transcription(tmp_path):
    module.save_json(tmp_path / "transcript.json", {"complete": False, "segments": []})
    with pytest.raises(module.WorkflowError):
        module.render(SimpleNamespace(out=str(tmp_path), outline="unused.json"))
    assert not (tmp_path / "mindmap.html").exists()


def test_local_media_conflict_does_not_replace_manifest(tmp_path, monkeypatch):
    media = tmp_path / "source.wav"
    media.write_bytes(b"different media")
    out = tmp_path / "run"
    original = {"sha256": "old"}
    module.save_json(out / "manifest.json", original)
    monkeypatch.setattr(module, "probe", lambda _: {"duration": 5, "streams": ["audio"]})
    with pytest.raises(module.WorkflowError):
        module.import_media(SimpleNamespace(source=str(media), out=str(out)))
    assert module.read_json(out / "manifest.json") == original


def test_download_failure_is_blocked_without_signed_url(tmp_path, monkeypatch):
    import yt_dlp

    class Downloader:
        def __init__(self, options):
            assert options["retries"] == 0
            assert "cookiesfrombrowser" not in options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, *args, **kwargs):
            raise RuntimeError("403 Forbidden https://media.example/video?secret=token")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    args = SimpleNamespace(source="BV1wZcVevENV", out=str(tmp_path), mode="audio", height=480, cookies=None)
    with pytest.raises(module.WorkflowError):
        module.fetch(args)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "blocked"
    assert "secret" not in manifest["error"]


def test_transcription_resume_and_absolute_timestamps(tmp_path, monkeypatch):
    media = tmp_path / "source.wav"
    media.write_bytes(b"test media")
    module.save_json(
        tmp_path / "manifest.json",
        {"media": str(media), "sha256": module.digest(media), "status": "acquired", "duration": 12},
    )
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            calls.append("load")

        def transcribe(self, *args, **kwargs):
            calls.append("transcribe")
            return iter(
                [SimpleNamespace(start=0, end=20, text="recognized", avg_logprob=-0.1, no_speech_prob=0)]
            ), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(module, "run_command", lambda _: "")
    args = SimpleNamespace(
        out=str(tmp_path), model="test", language="en", chunk_seconds=10, device="cpu", compute_type="int8"
    )
    module.transcribe(args)
    result = module.read_json(tmp_path / "transcript.json")
    assert result["complete"]
    assert [(s["start"], s["end"]) for s in result["segments"]] == [(0, 10), (10, 12)]
    assert len({s["id"] for s in result["segments"]}) == 2
    assert len(list((tmp_path / "analysis-packets").glob("*.md"))) == 2
    calls.clear()
    module.transcribe(args)
    assert calls == []
    args.model = "other-model"
    with pytest.raises(module.WorkflowError):
        module.transcribe(args)


def test_frame_cap_prevents_unbounded_sampling(tmp_path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"test video")
    module.save_json(
        tmp_path / "manifest.json", {"media": str(media), "sha256": module.digest(media), "duration": 500}
    )
    monkeypatch.setattr(module, "probe", lambda _: {"streams": ["audio", "video"]})
    with pytest.raises(module.WorkflowError):
        module.frames(SimpleNamespace(out=str(tmp_path), times=None, interval=1, max_frames=120))


def test_tiny_tail_is_merged_instead_of_transcribed_separately(tmp_path, monkeypatch):
    media = tmp_path / "source.wav"
    media.write_bytes(b"short media")
    module.save_json(
        tmp_path / "manifest.json",
        {"media": str(media), "sha256": module.digest(media), "status": "acquired", "duration": 10.15},
    )
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(1)
            return iter(
                [SimpleNamespace(start=10, end=10.15, text="tail", avg_logprob=-0.1, no_speech_prob=0)]
            ), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(module, "run_command", lambda _: "")
    module.transcribe(
        SimpleNamespace(
            out=str(tmp_path),
            model="test",
            language="en",
            chunk_seconds=10,
            device="cpu",
            compute_type="int8",
        )
    )
    assert len(calls) == 1
    packet = (tmp_path / "analysis-packets/0000.md").read_text()
    assert "tail" in packet


def library_args(source, root, **overrides):
    values = {
        "source": str(source),
        "library": str(root),
        "mode": "audio",
        "model": "small",
        "language": "zh",
        "interval": 60,
        "height": 480,
        "cookies": None,
        "new": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_library_resume_variants_and_failure_index(tmp_path, monkeypatch):
    calls = []

    def acquire(args):
        calls.append("fetch")
        module.save_json(Path(args.out) / "manifest.json", {"title": "<视频>", "source": args.source})

    def fail(args):
        raise module.WorkflowError("blocked https://secret.example/token")

    monkeypatch.setattr(module, "fetch", acquire)
    monkeypatch.setattr(module, "transcribe", fail)
    args = library_args("BV1wZcVevENV", tmp_path)
    with pytest.raises(module.WorkflowError):
        module.run_task(args)
    task_file = next(tmp_path.glob("tasks/*/*/task.json"))
    assert module.read_json(task_file)["status"] == "blocked"
    assert "secret.example" not in task_file.read_text()
    assert "&lt;视频&gt;" in (tmp_path / "index.html").read_text()
    monkeypatch.setattr(module, "transcribe", lambda args: calls.append("asr"))
    module.run_task(args)
    module.run_task(args)
    assert calls == ["fetch", "fetch", "asr"]
    assert module.read_json(task_file)["status"] == "awaiting_analysis"
    module.run_task(library_args("BV1wZcVevENV", tmp_path, model="tiny"))
    assert len(list(tmp_path.glob("tasks/*/*/task.json"))) == 2
    module.run_task(library_args("BV1wZcVevENV", tmp_path, new=True))
    assert len(list(tmp_path.glob("tasks/*/*/task.json"))) == 3


def test_library_finish_and_bundle_only_exports(tmp_path):
    import zipfile

    directory = tmp_path / "tasks" / "BV1wZcVevENV" / "sample"
    work = directory / "work"
    module.save_json(
        directory / "task.json",
        {
            "status": "awaiting_analysis",
            "title": "示例",
            "source_id": "BV1wZcVevENV",
            "created_at": "2026-10-05",
        },
    )
    module.save_json(
        work / "transcript.json",
        {
            "complete": True,
            "segments": [{"id": "s0000-0000", "start": 0, "end": 1, "text": "private transcript"}],
        },
    )
    module.save_json(
        work / "outline.json",
        {"title": "示例", "children": [{"title": "观点", "body": "原创概括", "evidence": ["s0000-0000"]}]},
    )
    args = SimpleNamespace(task=str(directory))
    with pytest.raises(module.WorkflowError):
        module.bundle_task(args)
    module.finish_task(args)
    (directory / "outputs" / "cookies.txt").write_text("sensitive")
    module.bundle_task(args)
    with zipfile.ZipFile(directory / "outputs" / "deliverables.zip") as archive:
        assert set(archive.namelist()) == {
            "outline.md",
            "mindmap.md",
            "mindmap.mm",
            "mindmap.html",
            "mindmap.svg",
            "reading.html",
        }
    assert module.read_json(directory / "task.json")["status"] == "completed"
    assert "outputs/mindmap.html" in (directory / "index.html").read_text()

    module.finish_task(args)
    assert not (directory / "outputs" / "deliverables.zip").exists()


def test_adopt_copies_external_assets_and_preserves_source_and_new_analysis(tmp_path):
    legacy = tmp_path / "legacy"
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"existing audio")
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"existing frame")
    module.save_json(
        legacy / "manifest.json",
        {
            "source": "https://www.bilibili.com/video/BV1wZcVevENV/",
            "title": "Existing",
            "media": str(media),
            "sha256": module.digest(media),
            "duration": 2,
        },
    )
    module.export_transcript(legacy, [{"id": "s1", "start": 0, "end": 2, "text": "private speech"}], True)
    module.save_json(legacy / "frames/index.json", [{"id": "f1", "time": 1, "path": str(frame)}])
    module.save_json(
        legacy / "outline.json",
        {
            "title": "Existing",
            "children": [{"title": "Claim", "body": "Own explanation", "evidence": ["f1"]}],
        },
    )
    args = SimpleNamespace(directory=str(legacy), library=str(tmp_path / "library"))
    task = module.adopt_task(args)
    copied_media = Path(module.read_json(task / "work/manifest.json")["media"])
    copied_frame = Path(module.read_json(task / "work/frames/index.json")[0]["path"])
    assert copied_media.is_relative_to(task / "work")
    assert copied_media.read_bytes() == media.read_bytes()
    assert copied_frame.is_relative_to(task / "work")
    assert copied_frame.read_bytes() == frame.read_bytes()
    assert module.read_json(legacy / "manifest.json")["media"] == str(media)
    assert module.read_json(legacy / "frames/index.json")[0]["path"] == str(frame)
    (task / "work/outline.json").write_text('{"kept": true}')
    assert module.adopt_task(args) == task
    assert module.read_json(task / "work/outline.json") == {"kept": True}
    assert "work/transcript.txt" in (task / "index.html").read_text()


def test_adopt_rejects_changed_media_without_publishing_task(tmp_path):
    media = tmp_path / "media.m4a"
    media.write_bytes(b"changed")
    legacy = tmp_path / "legacy"
    module.save_json(
        legacy / "manifest.json", {"source": "BV1wZcVevENV", "media": str(media), "sha256": "old"}
    )
    module.export_transcript(legacy, [{"id": "s1", "start": 0, "end": 2, "text": "private"}], True)
    with pytest.raises(module.WorkflowError):
        module.adopt_task(SimpleNamespace(directory=str(legacy), library=str(tmp_path / "library")))
    assert not list((tmp_path / "library").glob("tasks/*/*/task.json"))
    assert not list((tmp_path / "library").glob("tasks/*/*/.import-*"))


def test_library_renders_frame_preview_with_relative_images_and_missing_state(tmp_path):
    task = tmp_path / "tasks/BV1wZcVevENV/example"
    module.save_json(
        task / "task.json",
        {
            "status": "completed",
            "source_id": "BV1wZcVevENV",
            "title": "Long original title",
            "created_at": "2026-10-05",
        },
    )
    module.save_json(task / "work/outline.json", {"display_title": "Short <title>"})
    frame = task / "work/frames/picture<&>.jpg"
    frame.parent.mkdir()
    frame.write_bytes(b"image fixture")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"external image")
    module.save_json(
        task / "work/frames/index.json",
        [
            {"id": "f1", "time": 30, "path": str(frame)},
            {"id": "f2", "time": 60, "path": str(task / "work/frames/missing.jpg")},
            {"id": "f3", "time": 90, "path": str(outside)},
        ],
    )
    outputs = task / "outputs"
    outputs.mkdir()
    (outputs / "reading.html").write_text("Own explanation")
    (outputs / "outline.md").write_text("Own explanation")
    module.library_index(tmp_path)
    gallery = (task / "frames.html").read_text()
    home = (task / "index.html").read_text()
    assert '<img src="work/frames/picture&lt;&amp;&gt;.jpg"' in gallery
    assert 'alt="采样画面 · 00:00:30"' in gallery
    assert "outside.jpg" not in gallery
    assert "missing.jpg" not in gallery
    assert "另有 2 张" in gallery
    assert gallery.count("<img ") == 1
    assert '<a href="frames.html">采样画面</a>' in home
    assert '<a href="outputs/reading.html" class="primary">阅读页</a>' in home
    assert "Short &lt;title&gt;" in home
    assert '<details class="downloads">' in home
    (task / "work/frames/index.json").unlink()
    module.library_index(tmp_path)
    assert not (task / "frames.html").exists()
    assert 'href="frames.html"' not in (task / "index.html").read_text()


def test_detailed_reader_preserves_prose_without_exporting_raw_transcript(tmp_path):
    module.export_transcript(
        tmp_path, [{"id": "s1", "start": 0, "end": 2, "text": "PRIVATE RAW SPEECH"}], True
    )
    data = {
        "title": "Reader",
        "notice": "<img src=x onerror=alert(1)>",
        "review": "Own note [Primary](https://example.org/primary).",
        "children": [
            {"title": "Topic", "body": "First explanation.\n\nSecond explanation.", "evidence": ["s1"]}
        ],
        "timeline": [
            {"title": "Paraphrase", "body": "My own summary <&>", "start": 0, "end": 2, "evidence": ["s1"]}
        ],
    }
    source = tmp_path / "outline.json"
    module.save_json(source, data)
    module.render(SimpleNamespace(out=str(tmp_path), outline=str(source)))
    page = (tmp_path / "reading.html").read_text()
    assert "<p>First explanation.</p><p>Second explanation.</p>" in page
    assert "PRIVATE RAW SPEECH" not in page
    assert "<img src=x" not in page
    assert "My own summary &lt;&amp;&gt;" in page
    assert "My own summary <&>" in (tmp_path / "segments.md").read_text()
    assert '<a href="https://example.org/primary">Primary</a>' in page
    assert "Own note" in (tmp_path / "review.md").read_text()
    assert (
        "Second explanation." in ET.parse(tmp_path / "mindmap.mm").find(".//richcontent/html/body/p[2]").text
    )
    data.pop("timeline")
    data.pop("review")
    module.save_json(source, data)
    module.render(SimpleNamespace(out=str(tmp_path), outline=str(source)))
    assert not (tmp_path / "segments.md").exists()
    assert not (tmp_path / "review.md").exists()


@pytest.mark.parametrize(
    "start,end,ids",
    [(2, 1, ["s1"]), (0, float("inf"), ["s1"]), (0, 2, []), (0, 2, ["unknown"]), (2, 3, ["s1"])],
)
def test_timeline_requires_valid_times_and_matching_evidence(start, end, ids):
    data = {
        "timeline": [{"title": "Claim", "body": "Paraphrase", "start": start, "end": end, "evidence": ids}]
    }
    with pytest.raises(module.WorkflowError):
        module.validate_timeline(data, {"s1": {"start": 0, "end": 1}}, 5)


def test_reader_presentation_escapes_metadata_and_retains_folded_content(tmp_path):
    module.export_transcript(
        tmp_path, [{"id": "private-evidence-id", "start": 1, "end": 2, "text": "PRIVATE SPEECH"}], True
    )
    data = {
        "title": "Reader",
        "display_title": "<script>alert(1)</script>",
        "lede": "<img src=x>",
        "children": [
            {
                "title": "Chapter",
                "nav_title": "Short",
                "focus": "Main point",
                "sequence": ["<img src=x>", "Conclusion"],
                "children": [
                    {
                        "title": "Background",
                        "body": "Preserved <concept>.\n\n整理补充：A qualification.",
                        "emphasis": ["<concept>"],
                        "takeaway": "<img src=x>",
                        "secondary": True,
                        "evidence": ["private-evidence-id"],
                    }
                ],
            }
        ],
    }
    source = tmp_path / "outline.json"
    module.save_json(source, data)
    module.render(SimpleNamespace(out=str(tmp_path), outline=str(source)))
    page = (tmp_path / "reading.html").read_text()
    assert "private-evidence-id" not in page
    assert "PRIVATE SPEECH" not in page
    assert "<img src=x>" not in page
    assert "<script>alert(1)</script>" not in page
    assert "Preserved <strong>&lt;concept&gt;</strong>." in page
    assert "A qualification." in page
    assert '<details class="background">' in page
    assert "private-evidence-id" in (tmp_path / "outline.md").read_text()


@pytest.mark.parametrize(
    "fields",
    [
        {"display_title": []},
        {"focus": 4},
        {"sequence": "a"},
        {"sequence": [""]},
        {"emphasis": [False]},
        {"secondary": "true"},
    ],
)
def test_reader_rejects_invalid_presentation_fields(fields):
    node = {"title": "Topic", "evidence": ["s1"], **fields}
    with pytest.raises(module.WorkflowError):
        module.validate_outline({"title": "Reader", "children": [node]}, {"s1": {"start": 0}})
