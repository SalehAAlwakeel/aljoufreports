"""One-off helper: dump text + hyperlink targets from a .docx (tables preserved)."""

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
W = "{%s}" % NS["w"]
R = "{%s}" % NS["r"]


def rels(z):
    out = {}
    try:
        root = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    except KeyError:
        return out
    for rel in root:
        out[rel.get("Id")] = rel.get("Target")
    return out


def para_text(p, relmap):
    parts = []
    for node in p.iter():
        tag = node.tag
        if tag == W + "t":
            parts.append(node.text or "")
        elif tag == W + "tab":
            parts.append("\t")
        elif tag == W + "hyperlink":
            tgt = relmap.get(node.get(R + "id"))
            if tgt:
                parts.append(f" <{tgt}>")
    return "".join(parts).strip()


def walk(el, relmap, depth=0):
    lines = []
    for child in el:
        if child.tag == W + "p":
            t = para_text(child, relmap)
            if t:
                lines.append("  " * depth + t)
        elif child.tag == W + "tbl":
            lines.append("  " * depth + "--- TABLE ---")
            for row in child.findall("w:tr", NS):
                cells = []
                for cell in row.findall("w:tc", NS):
                    cells.append(" ".join(walk(cell, relmap, 0)).strip())
                lines.append("  " * depth + " | ".join(cells))
            lines.append("  " * depth + "--- END TABLE ---")
        else:
            lines.extend(walk(child, relmap, depth))
    return lines


def main(path, out):
    z = zipfile.ZipFile(path)
    relmap = rels(z)
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", NS)
    text = "\n".join(walk(body, relmap))
    urls = sorted(set(re.findall(r"https?://[^\s<>\"]+", text)))
    payload = {"lines": text.split("\n"), "urls": urls}
    with open(out, "w", encoding="ascii") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=1)
    print(f"wrote {out}: {len(text)} chars, {len(urls)} urls")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
