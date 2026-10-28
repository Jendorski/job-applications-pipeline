from datetime import date

import pytest
from docx import Document

from jap.models import BulletChoice, TailorPlan
from jap.render.context import PlanError, build_context, years_since
from jap.render.docx import render_docx


def text_of(path):
    return "\n".join(p.text for p in Document(path).paragraphs)


def test_years_since():
    assert years_since("2019-06", date(2026, 10, 27)) == 7
    assert years_since("2019-11", date(2026, 10, 27)) == 6


def test_sample_cv_renders_from_profile(profile, tracks, plan_json, templates, tmp_path):
    plan = TailorPlan.model_validate_json(plan_json)
    ctx = build_context(profile, tracks[plan.track], plan, today=date(2026, 10, 27))
    text = text_of(render_docx(templates / "cv.docx", ctx, tmp_path / "cv.docx", "t"))

    assert "{{" not in text and "{%" not in text
    assert "Backend engineer with 7+ years" in text
    assert "Feb 2023 – Present (part-time since Feb 2026)" in text
    assert "Open to relocating to Amsterdam" in text
    assert "Example University" in text
    assert "Company C" not in text


def test_sample_letter_renders(profile, tracks, plan_json, templates, tmp_path):
    plan = TailorPlan.model_validate_json(plan_json)
    ctx = build_context(profile, tracks[plan.track], plan)
    text = text_of(render_docx(templates / "cover_letter.docx", ctx, tmp_path / "l.docx", "t"))

    assert "27 October 2026" in text
    assert "Dear Company F hiring team," in text
    assert text.rstrip().endswith("John Doe")


def test_plan_cannot_reference_unverified_or_hidden_items(profile, tracks, plan_json):
    plan = TailorPlan.model_validate_json(plan_json)
    plan.experience["company_c"] = [BulletChoice(bullet_id="company_c.backend")]
    plan.experience["company_b"].append(BulletChoice(bullet_id="company_b.made_up"))
    with pytest.raises(PlanError) as e:
        build_context(profile, tracks[plan.track], plan)
    assert len(e.value.problems) == 2


def test_plan_headline_must_come_from_track(profile, tracks, plan_json):
    plan = TailorPlan.model_validate_json(plan_json)
    plan.headline = "Principal Kubernetes Architect"
    with pytest.raises(PlanError):
        build_context(profile, tracks[plan.track], plan)
