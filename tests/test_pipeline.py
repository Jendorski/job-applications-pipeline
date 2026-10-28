import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from jap import config
from jap.db import DB
from jap.llm.fake import FakeProvider
from jap.models import Answer, AnswerSet, CoverLetterDraft, DraftBullet, DraftRole, JobPosting, TailorDraft, TailorPlan
from jap.pipeline import JobInput, run_job

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "scrape"
TODAY = date(2026, 10, 27)
SETTINGS = config.Settings()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APPLICATIONS_DIR", tmp_path / "applications")
    monkeypatch.setattr(config, "PROFILE_PATH", ROOT / "examples" / "profile" / "master_profile.yaml")
    monkeypatch.setattr(config, "TRACKS_PATH", ROOT / "examples" / "profile" / "tracks.yaml")
    monkeypatch.setattr(config, "TEMPLATES_DIR", ROOT / "templates")
    return tmp_path


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "data" / "pipeline.db")


POSTING = JobPosting(
    company="Company F", title="Backend Software Engineer", location="Amsterdam, Netherlands", remote="onsite",
    seniority="senior", mission="Software that connects POS terminals and payment devices.",
    responsibilities=["Build device-facing backend services"], must_have=["Node.js", "TypeScript"],
    nice_to_have=["C++"], keywords=["Node.js", "TypeScript", "event-driven architecture", "WebSockets"],
    visa_or_location_constraints=None, salary=None, asks_for_cover_letter=True,
)


def good_draft() -> TailorDraft:
    plan = TailorPlan.model_validate_json((ROOT / "fixtures" / "sample_plan.json").read_text())
    return TailorDraft(
        headline=plan.headline, location=plan.location, summary=plan.summary, skill_groups=plan.skill_groups,
        experience=[DraftRole(role_id=r, bullets=[DraftBullet(bullet_id=c.bullet_id, rephrased=None) for c in cs])
                    for r, cs in plan.experience.items()],
        projects=plan.projects,
        cover_letter=CoverLetterDraft(greeting=plan.cover_letter.greeting, paragraphs=plan.cover_letter.paragraphs,
                                      sign_off=plan.cover_letter.sign_off),
        keyword_coverage=[], gaps=["No POS-terminal experience"],
    )


def good_answers(_user: str = "") -> AnswerSet:
    return AnswerSet(answers=[
        Answer(question="Why do you want to work at Company F?", source="standard", kind="motivation",
               answer="Your backend sits between software and payment devices, which is close to my work at Company A."),
        Answer(question="What motivates you?", source="standard", kind="motivation",
               answer="The gap between a system that seems to work and one that is actually right."),
    ])


def fake(responses: dict) -> FakeProvider:
    """FakeProvider with a default answer set, so tests only spell out what they care about."""
    return FakeProvider({AnswerSet: good_answers, **responses})


def bad_draft() -> TailorDraft:
    d = good_draft()
    d.summary += " Expert in Kafka."
    return d


def run(db, provider, **job):
    return run_job(JobInput(**job), settings=SETTINGS, provider=provider, db=db, pdf=False, today=TODAY)


def test_pasted_description_end_to_end(db, isolated):
    provider = fake({JobPosting: [POSTING], TailorDraft: [good_draft()]})
    row = run(db, provider, jd_text="Backend Software Engineer at Company F ... " * 20, note="via friend")

    assert row["status"] == "ready", row["error"]
    folder = config.APPLICATIONS_DIR / row["folder"]
    assert row["folder"] == "2026-10-27_0001_company-f_backend-software-engineer"
    names = {p.name for p in folder.iterdir()}
    assert {"John Doe Backend_CompanyF_CV.docx", "John Doe Cover_Letter_CompanyF.docx",
            "job.json", "plan.json", "match_report.md", "run.log", "source"} <= names
    report = (folder / "match_report.md").read_text()
    assert "passed" in report and "No POS-terminal experience" in report
    assert json.loads((folder / "plan.json").read_text())["prompt_version"] == "tailor-v1"
    assert "| 0001 | Company F |" in (config.APPLICATIONS_DIR / "tracker.md").read_text()
    # The cached prefix is identical across calls; the job goes in the user turn.
    tailor_call = next(c for c in provider.calls if c["schema"] is TailorDraft)
    assert "<profile>" in tailor_call["system"] and "Company F" not in tailor_call["system"]
    assert row["cost_usd"] > 0


def test_validation_failure_is_retried_with_the_errors(db):
    provider = fake({JobPosting: [POSTING], TailorDraft: [bad_draft(), good_draft()]})
    row = run(db, provider, jd_text="x" * 500)
    assert row["status"] == "ready"
    retry = [c for c in provider.calls if c["schema"] is TailorDraft][1]
    assert "<validation_errors>" in retry["user"] and "Kafka" in retry["user"]


def test_persistent_failure_needs_review(db):
    provider = fake({JobPosting: [POSTING], TailorDraft: [bad_draft(), bad_draft()]})
    row = run(db, provider, jd_text="x" * 500)
    assert row["status"] == "needs_review" and "Kafka" in row["error"]
    assert (config.APPLICATIONS_DIR / row["folder"] / "VALIDATION_FAILED.md").exists()


def test_duplicates_are_skipped_unless_forced(db):
    provider = fake({JobPosting: [POSTING, POSTING], TailorDraft: [good_draft(), good_draft()]})
    first = run(db, provider, jd_text="same text " * 60)
    again = run(db, provider, jd_text="same text " * 60)
    assert again["id"] == first["id"] and len(provider.calls) == 3
    forced = run(db, provider, jd_text="same text " * 60, force=True)
    assert forced["id"] == first["id"] and forced["status"] == "ready" and len(provider.calls) == 6


def test_scraped_url_and_blocked_site(db):
    gh = (FIX / "greenhouse_company_g_8704363002.json").read_text()
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=gh, headers={"content-type": "application/json"})))
    provider = fake({JobPosting: [POSTING], TailorDraft: [good_draft()]})
    row = run_job(JobInput(urls=["https://job-boards.greenhouse.io/company-g/jobs/8704363002?gh_src=abc"]),
                  settings=SETTINGS, provider=provider, db=db, http=http, pdf=False, today=TODAY)
    assert row["status"] == "ready" and row["primary_url"] == "https://job-boards.greenhouse.io/company-g/jobs/8704363002"
    assert "Agent Core" in next(c for c in provider.calls if c["schema"] is JobPosting)["user"]

    blocked = run(db, FakeProvider({}), urls=["https://www.linkedin.com/jobs/view/1"])
    assert blocked["status"] == "needs_manual_jd" and "paste" in blocked["error"]


def test_budget_pause(db):
    db.record_usage(None, "extract", __import__("jap.llm", fromlist=["Usage"]).Usage(model="m", input_tokens=1, output_tokens=1), 50.0)
    row = run(db, FakeProvider({}), jd_text="y" * 500)
    assert row["status"] == "paused_budget"
