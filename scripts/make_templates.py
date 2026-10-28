"""Build templates/cv.docx and templates/cover_letter.docx from a hand-made application.

Each template paragraph is a deep copy of a prototype paragraph from the source document,
so fonts, sizes, spacing, borders, bullets and the right-aligned date tab are carried over
exactly. Only the text changes, to docxtpl (Jinja) tags.

    uv run python scripts/make_templates.py \
        --cv "../backend/John Doe Backend_CV.docx" \
        --letter "../cover_letter/John Doe Cover_Letter.docx"
"""

import argparse
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Prototype paragraph indexes in the source documents.
CV_PROTOS = {
    "name": 0, "headline": 1, "contact": 2, "heading": 3, "text": 4, "skill": 6,
    "company": 14, "role": 15, "bullet": 16, "project_title": 42, "project_body": 43, "project_repo": 44,
}
LETTER_PROTOS = {"name": 0, "contact": 1, "date": 2, "greeting": 3, "body": 4, "sign_off": 9, "sign_name": 10}

CV_SPEC = [
    ("name", ["{{ person.name }}"]),
    ("headline", ["{{ headline }}"]),
    ("contact", ["{{ contact_line }}"]),
    ("heading", ["Summary"]),
    ("text", ["{{ summary }}"]),
    ("heading", ["Core Skills"]),
    ("text", ["{%p for g in skill_groups %}"]),
    ("skill", ["{{ g.label }}: ", "{{ g.skills }}"]),
    ("text", ["{%p endfor %}"]),
    ("heading", ["Professional Experience"]),
    ("text", ["{%p for job in experience %}"]),
    ("company", ["{{ job.company }}", "{{ job.dates }}"]),
    ("role", ["{{ job.title }}", "{{ job.location }}"]),
    ("text", ["{%p for b in job.bullets %}"]),
    ("bullet", ["{{ b }}"]),
    ("text", ["{%p endfor %}"]),
    ("text", ["{%p endfor %}"]),
    ("text", ["{%p if projects %}"]),
    ("heading", ["Selected Projects"]),
    ("text", ["{%p for p in projects %}"]),
    ("project_title", ["{{ p.name }}", "  |  {{ p.stack }}"]),
    ("project_body", ["{{ p.text }}"]),
    ("text", ["{%p if p.repo %}"]),
    ("project_repo", ["Repo: {{ p.repo }}"]),
    ("text", ["{%p endif %}"]),
    ("text", ["{%p endfor %}"]),
    ("text", ["{%p endif %}"]),
    ("text", ["{%p if education %}"]),
    ("heading", ["Education"]),
    ("text", ["{%p for e in education %}"]),
    ("company", ["{{ e.institution }}", "{{ e.dates }}"]),
    ("role", ["{{ e.degree }}", "{{ e.location }}"]),
    ("text", ["{%p endfor %}"]),
    ("text", ["{%p endif %}"]),
]

LETTER_SPEC = [
    ("name", ["{{ person.name }}"]),
    ("contact", ["{{ contact_line }}"]),
    ("date", ["{{ letter.date }}"]),
    ("greeting", ["{{ letter.greeting }}"]),
    ("body", ["{%p for para in letter.paragraphs %}"]),
    ("body", ["{{ para }}"]),
    ("body", ["{%p endfor %}"]),
    ("sign_off", ["{{ letter.sign_off }}"]),
    ("sign_name", ["{{ person.name }}"]),
]


def with_texts(paragraph, texts: list[str]):
    p = deepcopy(paragraph)
    nodes = list(p.iter(qn("w:t")))
    if len(nodes) != len(texts):
        raise ValueError(f"prototype has {len(nodes)} text nodes, spec gives {len(texts)}: {texts}")
    for node, text in zip(nodes, texts):
        node.text = text
        node.set(XML_SPACE, "preserve")
    return p


def right_tab_stop(paragraph, pos: int):
    """Swap Word's positional tab (w:ptab, ignored by LibreOffice) for a real right tab stop."""
    for ptab in paragraph.iter(qn("w:ptab")):
        tab = ptab.makeelement(qn("w:tab"), {})
        ptab.getparent().replace(ptab, tab)
    ppr = paragraph.find(qn("w:pPr"))
    tabs = ppr.makeelement(qn("w:tabs"), {})
    tabs.append(tabs.makeelement(qn("w:tab"), {qn("w:val"): "right", qn("w:pos"): str(pos)}))
    spacing = ppr.find(qn("w:spacing"))  # w:tabs must precede w:spacing in pPr
    if spacing is not None:
        spacing.addprevious(tabs)
    else:
        ppr.append(tabs)


def build(src: Path, out: Path, protos: dict[str, int], spec: list[tuple[str, list[str]]]) -> None:
    doc = Document(src)
    body = doc.element.body
    paragraphs = [el for el in body if el.tag == qn("w:p")]
    prototypes = {key: deepcopy(paragraphs[i]) for key, i in protos.items()}
    section = doc.sections[0]
    text_width = (section.page_width - section.left_margin - section.right_margin) // 635  # EMU → twips
    for p in prototypes.values():
        if p.find(f".//{qn('w:ptab')}") is not None:
            right_tab_stop(p, text_width)
    for p in paragraphs:
        body.remove(p)
    sect = body.find(qn("w:sectPr"))
    for key, texts in spec:
        sect.addprevious(with_texts(prototypes[key], texts))
    props = doc.core_properties
    props.title, props.subject, props.keywords, props.comments = "", "", "", ""
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv", type=Path, required=True)
    ap.add_argument("--letter", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("templates"))
    args = ap.parse_args()
    build(args.cv, args.out / "cv.docx", CV_PROTOS, CV_SPEC)
    build(args.letter, args.out / "cover_letter.docx", LETTER_PROTOS, LETTER_SPEC)


if __name__ == "__main__":
    main()
