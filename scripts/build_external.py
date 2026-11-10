"""Build the pre-labelled external-source challenge; never run after observing results.

Source snapshots and attribution are retained in data/external/sources/.
The Bonterms pair edits the publisher's PDF in place; other PDFs use two different
exports. These are our mutations of public texts, not independent expert labels.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import pymupdf as fitz
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4, letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/external"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(text, path, style):
    font, size, pagesize = ("Helvetica", 10, letter) if style == "paper" else ("Times-Roman", 11, A4)
    doc = SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=48, rightMargin=48,
                            topMargin=42, bottomMargin=42, invariant=1)
    normal = ParagraphStyle("body", fontName=font, fontSize=size, leading=size * 1.45, spaceAfter=9)
    story = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"<[^>]+>", "", para)
        para = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", para)
        para = re.sub(r"^#+\s*", "", para).replace("**", "").replace("_", "")
        if para.strip():
            story.extend([Paragraph(html.escape(" ".join(para.split())), normal), Spacer(1, 2)])
    doc.build(story)


def edit_native(path, edits, dest):
    doc = fitz.open(path)
    for old, new in edits:
        hits = [(page, rect) for page in doc for rect in page.search_for(old)]
        assert len(hits) == 1, (old, len(hits))
        page, rect = hits[0]
        span = next(s for b in page.get_text("dict")["blocks"] if "lines" in b
                    for line in b["lines"] for s in line["spans"]
                    if old in s["text"] and fitz.Rect(s["bbox"]).intersects(rect))
        baseline, size = span["origin"][1], span["size"]
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=0)
        page.insert_text((rect.x0, baseline), new, fontname="helv", fontsize=size)
    doc.save(dest, garbage=4, deflate=True)
    doc.close()


def main():
    assert not (BASE / "LOCK.json").exists(), "Corpus already frozen; do not overwrite the first-run gold."
    sources = BASE / "sources"
    cp = (sources / "commonpaper.md").read_text()
    six = (sources / "sixarm.md").read_text()
    six = "# Consulting agreement — sections 6 and 7 (excerpt)\n\n" + six[six.index("## 6. Limits"):six.index("## 8. Mutual Alignment")]
    plans = [
        ("commonpaper", cp, "paper", [("prior written approval", "prior notification"),
                                         ("cease using Confidential Information", "continue using Confidential Information")],
         ("reasonable standard of care", "reasonable level of care")),
        ("bonterms", None, "native", [("cease using", "keep using"), ("no license", "a license")],
         ("reasonable standard of care", "reasonable level of care")),
        ("sixarm", six, "serif", [("initial term of this Agreement is one calendar week", "initial term of this Agreement is four calendar weeks"),
                                    ("one hundred percent (100%)", "two hundred percent (200%)")],
         ("All aspects of this section are mutual", "Every aspect of this section is mutual")),
    ]
    manifest = []
    for source, original, style, edits, benign in plans:
        for kind in ("changed", "benign"):
            cid = f"{source}-{kind}"
            d = BASE / cid
            d.mkdir(parents=True, exist_ok=True)
            selected = edits if kind == "changed" else [benign]
            if style == "native":
                (d / "v1.pdf").write_bytes((sources / "bonterms-original.pdf").read_bytes())
                edit_native(d / "v1.pdf", selected, d / "v2.pdf")
            else:
                export(original, d / "v1.pdf", style)
                modified = original
                for old, new in selected:
                    assert old in modified, old
                    modified = modified.replace(old, new, 1)
                export(modified, d / "v2.pdf", style)
            cover = "We only changed formatting. The substantive obligations are unchanged." if kind == "changed" else "We reworded one sentence without changing its meaning."
            (d / "cover.txt").write_text(cover + "\n")
            truth = {"id": cid, "source": source, "kind": kind, "substantive": selected if kind == "changed" else [],
                     "faithful": [benign] if kind == "benign" else [], "expected_status": "changes" if kind == "changed" else "ready",
                     "false_cover": kind == "changed"}
            (d / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
            manifest.append({"id": cid, "style": style})
    (BASE / "index.json").write_text(json.dumps(manifest, indent=2) + "\n")
    files = sorted(p for p in BASE.rglob("*") if p.is_file())
    code = ["referee/engine.py", "referee/rules.py", "referee/textdoc.py", "referee/prompts.py", "eval/run_external.py", "eval/run_routing.py"]
    lock = {"protocol": 1, "cases": [m["id"] for m in manifest],
            "acceptance": {"completed": 6, "substantive_held": 6, "faithful_cleared": 3, "false_cover_caught": 3,
                           "routing_correct": 6, "routing_ablation": "Report observed differences; no required uplift."},
            "scope": "Three public authors; our pre-labelled edits. Not an independently labelled or blinded benchmark. Native Bonterms PDF, two generated exports, SixArm excerpt only. No training or prompt tuning after freeze.",
            "files": {str(p.relative_to(ROOT)): digest(p) for p in files},
            "code_at_freeze": {p: digest(ROOT / p) for p in code}}
    (BASE / "LOCK.json").write_text(json.dumps(lock, indent=2) + "\n")


if __name__ == "__main__":
    main()
