import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("mindmap", Path(__file__).parents[1] / "scripts/mindmap.py")
mindmap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mindmap)


def tree(depth, branches, label="一个长一些的主题标签，验证中文换行与布局"):
    return {
        "title": label,
        "body": "节点正文",
        "children": [tree(depth - 1, branches) for _ in range(branches)] if depth else [],
        "evidence": ["s1"],
    }


@pytest.mark.parametrize("data", [tree(3, 3), tree(1, 20), tree(1, 1, "主题" * 70)])
def test_layout_keeps_nodes_in_canvas_and_disjoint(data):
    plan = mindmap.layout(data)
    assert len(plan["edges"]) == len(plan["nodes"]) - 1
    ids = {n["id"] for n in plan["nodes"]}
    assert len(ids) == len(plan["nodes"])
    assert all(a in ids and b in ids for a, b in plan["edges"])
    for i, a in enumerate(plan["nodes"]):
        assert 0 <= a["x"] < a["x"] + a["width"] <= plan["width"]
        assert 0 <= a["y"] < a["y"] + a["height"] <= plan["height"]
        for b in plan["nodes"][i + 1 :]:
            overlap = (
                a["x"] < b["x"] + b["width"]
                and b["x"] < a["x"] + a["width"]
                and a["y"] < b["y"] + b["height"]
                and b["y"] < a["y"] + a["height"]
            )
            assert not overlap
    if len(data["children"]) > 1:
        assert {n["side"] for n in plan["nodes"] if n["depth"] == 1} == {-1, 1}


def test_svg_and_embedded_data_escape_hostile_text_and_preserve_notes(tmp_path):
    attack = "</script><img src=x onerror=alert(1)>"
    data = {
        "title": "中文脑图",
        "children": [
            {"title": attack, "map_label": "简短标签", "body": attack + "\n\n完整论证", "evidence": ["s1"]}
        ],
    }
    mindmap.render(tmp_path, data, {"s1": {"start": 62}})
    svg = ET.parse(tmp_path / "mindmap.svg")
    ns = {"s": mindmap.NS}
    assert len(svg.findall(".//s:g[@data-id]", ns)) == 2
    assert svg.find('.//s:g[@data-id="n-1"]/s:title', ns).text == attack
    assert "简短标签" in "".join(svg.getroot().itertext())
    page = (tmp_path / "mindmap.html").read_text()
    assert attack not in page
    payload = re.search(r'<script id="map-data" type="application/json">(.*?)</script>', page, re.DOTALL)[1]
    value = json.loads(payload)
    assert value["nodes"][1]["body"] == attack + "\n\n完整论证"
    assert value["nodes"][1]["time"] == "00:01:02"
    assert "https://" not in page.replace(mindmap.NS, "")
