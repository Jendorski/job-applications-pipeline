from datetime import date

import pytest

from jap.models import BulletChoice, TailorPlan
from jap.validate import validate

TODAY = date(2026, 10, 27)


@pytest.fixture
def plan(plan_json):
    return TailorPlan.model_validate_json(plan_json)


def problems(profile, tracks, plan):
    return validate(profile, tracks[plan.track], plan, today=TODAY)


def test_fixture_plan_is_clean(profile, tracks, plan):
    assert problems(profile, tracks, plan) == []


def test_unsupported_technology_in_summary(profile, tracks, plan):
    plan.summary += " Deep experience with Kafka and Kubernetes."
    found = problems(profile, tracks, plan)
    assert any("Kafka" in p for p in found) and any("Kubernetes" in p for p in found)


def test_quarantined_devops_skill_in_letter(profile, tracks, plan):
    plan.cover_letter.paragraphs[1] += " I also ran Terraform across three clouds."
    assert any("Terraform" in p for p in problems(profile, tracks, plan))


def test_supported_technology_is_fine(profile, tracks, plan):
    plan.summary = plan.summary.replace("Company A:", "Company A, in Python/FastAPI:")
    assert problems(profile, tracks, plan) == []


def test_case_sensitive_terms_ignore_plain_english(profile, tracks, plan):
    plan.cover_letter.paragraphs[0] += " I would move swiftly and keep the work in good spark."
    assert problems(profile, tracks, plan) == []


def test_rephrase_cannot_add_numbers_or_grow(profile, tracks, plan):
    plan.experience["company_a"][0] = BulletChoice(bullet_id="company_a.monitoring_platform", rephrased="Built the platform for 40 plants")
    plan.experience["company_a"][1] = BulletChoice(bullet_id="company_a.device_writeback", rephrased="x" * 2000)
    found = problems(profile, tracks, plan)
    assert any("numbers ['40']" in p for p in found)
    assert any("light rephrasing only" in p for p in found)


def test_invented_number_in_letter(profile, tracks, plan):
    plan.cover_letter.paragraphs[2] += " That cut costs by 37%."
    assert any("'37'" in p for p in problems(profile, tracks, plan))


def test_years_placeholder_and_profile_years_are_allowed(profile, tracks, plan):
    plan.cover_letter.paragraphs[2] += " Over {years} years, and at Company B since 2023, I have shipped steadily."
    assert problems(profile, tracks, plan) == []


def test_hidden_and_quarantined_employers_never_appear(profile, tracks, plan):
    plan.cover_letter.paragraphs[2] += " I also built a rental platform for Company C and worked at Company X."
    found = " ".join(problems(profile, tracks, plan))
    assert "Company C" in found and "Company X" in found


def test_every_cv_role_needs_a_bullet(profile, tracks, plan):
    del plan.experience["company_e"]
    assert any("'company_e' is missing" in p for p in problems(profile, tracks, plan))


def test_letter_shape(profile, tracks, plan):
    plan.cover_letter.paragraphs = ["I am passionate about this role."]
    found = " ".join(problems(profile, tracks, plan))
    assert "words" in found and "passionate" in found


def test_letter_must_name_the_company(profile, tracks, plan):
    plan.cover_letter.greeting = "Dear hiring team,"
    plan.cover_letter.paragraphs = [p.replace("Company F", "your company") for p in plan.cover_letter.paragraphs]
    assert any("never names Company F" in p for p in problems(profile, tracks, plan))
