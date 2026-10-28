from pathlib import Path

from docxtpl import DocxTemplate


def render_docx(template: Path, context: dict, out: Path, title: str) -> Path:
    tpl = DocxTemplate(template)
    tpl.render(context, autoescape=True)
    props = tpl.docx.core_properties
    props.author = context["person"]["name"]
    props.title = title
    out.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(out)
    return out
