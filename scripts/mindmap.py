"""Offline SVG mind maps: authored labels, deterministic tree layout, no source transcript."""

import html
import json
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from string import Template

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def label_lines(label, limit=22):
    lines, line, units = [], "", 0
    for char in label:
        weight = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if char == "\n" or units + weight > limit:
            lines.append(line)
            line, units = "", 0
        if char != "\n":
            line += char
            units += weight
    if line or not lines:
        lines.append(line)
    return lines


def layout(data):
    """Reserve each subtree's height before placing it; alternate branches on both sides."""
    nodes, edges = [], []
    columns, gap, margin = 310, 22, 70

    def prepare(source, key, depth, chapter):
        label = source.get("map_label") or source.get("nav_title") or source["title"]
        lines = label_lines(label, 20 if depth < 2 else 26)
        height = max(52, len(lines) * 25 + 24)
        node = {
            "id": key,
            "depth": depth,
            "chapter": chapter,
            "label": label,
            "lines": lines,
            "width": 244,
            "height": height,
            "title": source["title"],
            "body": source.get("body", ""),
            "takeaway": source.get("takeaway", ""),
            "evidence": source.get("evidence", []),
            "children": [],
        }
        for i, child in enumerate(source.get("children", []), 1):
            node["children"].append(prepare(child, f"{key}-{i}", depth + 1, chapter))
        node["span"] = max(
            height, sum(c["span"] for c in node["children"]) + gap * max(0, len(node["children"]) - 1)
        )
        return node

    branches = [prepare(c, f"n-{i}", 1, i) for i, c in enumerate(data["children"], 1)]
    sides = [branches[::2], branches[1::2]]
    extent = max(sum(n["span"] for n in side) + 90 * max(0, len(side) - 1) for side in sides)
    depth = max_depth(data)
    width = 2 * (depth * columns + margin) + 244
    root_label = data.get("map_label") or data.get("display_title") or data["title"]
    root_lines = label_lines(root_label, 18)
    root_height = max(84, len(root_lines) * 25 + 24)
    height = max(extent, root_height) + margin * 2
    cx, cy = width / 2, height / 2
    root = {
        "id": "root",
        "depth": 0,
        "chapter": 0,
        "label": root_label,
        "title": data["title"],
        "body": data.get("summary", ""),
        "takeaway": data.get("lede", ""),
        "evidence": [],
        "x": cx - 122,
        "y": cy - 42,
        "width": 244,
        "height": 84,
    }
    root["lines"] = root_lines
    root["height"] = root_height
    root["y"] = cy - root["height"] / 2
    nodes.append(root)

    def place(node, top, side, parent):
        node.update(
            x=cx + side * node["depth"] * columns - node["width"] / 2,
            y=top + node["span"] / 2 - node["height"] / 2,
            side=side,
        )
        nodes.append(node)
        edges.append((parent["id"], node["id"]))
        child_top = (
            top
            + (
                node["span"]
                - sum(c["span"] for c in node["children"])
                - gap * max(0, len(node["children"]) - 1)
            )
            / 2
        )
        for child in node["children"]:
            place(child, child_top, side, node)
            child_top += child["span"] + gap

    for side, group in zip((-1, 1), sides):
        span = sum(n["span"] for n in group) + 90 * max(0, len(group) - 1)
        top = (height - span) / 2
        for node in group:
            place(node, top, side, root)
            top += node["span"] + 90
    return {"width": width, "height": height, "nodes": nodes, "edges": edges}


def max_depth(data):
    return 1 + max((max_depth(c) for c in data.get("children", [])), default=-1)


def svg_document(plan):
    def element(parent, tag, **attrs):
        return ET.SubElement(
            parent, f"{{{NS}}}{tag}", {k.replace("_", "-"): str(v) for k, v in attrs.items()}
        )

    svg = ET.Element(
        f"{{{NS}}}svg",
        {
            "viewBox": f"0 0 {plan['width']} {plan['height']}",
            "width": str(plan["width"]),
            "height": str(plan["height"]),
            "aria-label": "主题思维导图",
            "role": "group",
        },
    )
    element(svg, "rect", width="100%", height="100%", fill="#f6f4ef", id="paper")
    lookup = {n["id"]: n for n in plan["nodes"]}
    paths = element(svg, "g", fill="none", stroke="#b1babd", stroke_width=2)
    for parent_id, child_id in plan["edges"]:
        p, c = lookup[parent_id], lookup[child_id]
        side = c["side"]
        x1 = p["x"] + (p["width"] if side == 1 else 0)
        x2 = c["x"] + (0 if side == 1 else c["width"])
        y1, y2 = p["y"] + p["height"] / 2, c["y"] + c["height"] / 2
        middle = (x1 + x2) / 2
        element(
            paths,
            "path",
            d=f"M{x1},{y1} C{middle},{y1} {middle},{y2} {x2},{y2}",
            data_depth=c["depth"],
            data_branch=c["chapter"],
        )
    for node in plan["nodes"]:
        group = element(
            svg,
            "g",
            data_id=node["id"],
            data_depth=node["depth"],
            data_branch=node["chapter"],
            tabindex=0,
            role="button",
            aria_label=node["label"],
            **{"class": "map-node"},
        )
        element(group, "title").text = node["title"]
        fill = "#52666f" if node["depth"] == 0 else "#e4eaec" if node["depth"] == 1 else "#ffffff"
        element(
            group,
            "rect",
            x=node["x"],
            y=node["y"],
            width=node["width"],
            height=node["height"],
            rx=12,
            fill=fill,
            stroke="#ccd2d3",
            stroke_width=1,
        )
        font = 22 if node["depth"] == 0 else 19 if node["depth"] == 1 else 17
        text = element(
            group,
            "text",
            x=node["x"] + node["width"] / 2,
            y=node["y"] + (node["height"] - 25 * len(node["lines"])) / 2 + 19,
            text_anchor="middle",
            font_family="PingFang SC,Microsoft YaHei,sans-serif",
            font_size=font,
            font_weight=600 if node["depth"] < 2 else 400,
            fill="#ffffff" if node["depth"] == 0 else "#383b3a",
        )
        for i, line in enumerate(node["lines"]):
            element(text, "tspan", x=node["x"] + node["width"] / 2, dy=0 if i == 0 else 25).text = line
    return ET.tostring(svg, encoding="unicode")


def render(out, data, evidence):
    plan = layout(data)
    for node in plan["nodes"]:
        points = [evidence[key].get("start", evidence[key].get("time", 0)) for key in node["evidence"]]
        seconds = int(min(points)) if points else None
        node["time"] = (
            f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}" if seconds is not None else ""
        )
    svg = svg_document(plan)
    (out / "mindmap.svg").write_text(svg, encoding="utf-8")
    assets = Path(__file__).resolve().parents[1] / "assets"
    payload = json.dumps(plan, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = Template((assets / "mindmap.html").read_text(encoding="utf-8")).substitute(
        title=html.escape(data.get("display_title") or data["title"]),
        svg=svg,
        payload=payload,
        css=(assets / "mindmap.css").read_text(encoding="utf-8"),
        js=(assets / "mindmap.js").read_text(encoding="utf-8"),
    )
    (out / "mindmap.html").write_text(page, encoding="utf-8")
