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
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
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
