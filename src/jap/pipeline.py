"""One job, end to end: scrape → extract → pick track → tailor → validate → render → report."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx

from jap import config
from jap.answers import answers_markdown, facts, generate_answers
from jap.db import DB
from jap.extract import extract_posting
from jap.llm import LLMError, LLMProvider
from jap.models import Profile, TailorPlan, Track
from jap.profile import load_profile, load_tracks
from jap.render.context import PlanError, build_context, file_stem
from jap.render.docx import render_docx
from jap.render.pdf import PdfUnavailable, page_count, to_pdf
from jap.report import match_report
from jap.scrape import NeedsManualJD, ScrapeError, Scraped, make_client, scrape, scrape_context
from jap.select_track import choose_track
from jap.tailor import tailor
from jap.tracker import write_tracker
from jap.urls import normalize_url, slug

log = logging.getLogger("jap")
MAX_TRIMS = 4
# Seeing the same posting again redoes it without '# force' when the last attempt didn't finish:
# it failed, waited for a pasted description, hit the budget, or was interrupted mid-run.
RERUNNABLE = {"failed", "needs_manual_jd", "paused_budget", "queued", "scraping", "extracting", "tailoring",
              "answering", "rendering"}


@dataclass
class JobInput:
    urls: list[str] = field(default_factory=list)
    jd_text: str | None = None
    track_hint: str | None = None
    note: str | None = None
    force: bool = False


@dataclass
class Rendered:
    files: list[Path]
    pages: dict[str, int | None]
    notes: list[str]
    over_budget: bool


def render_application(profile: Profile, track: Track, plan: TailorPlan, out: Path, pdf: bool = True,
                       today: date | None = None) -> Rendered:
    """Render CV (and letter), trimming the least important bullets until the CV fits its page budget."""
    notes: list[str] = []
    for trim in range(MAX_TRIMS + 1):
        ctx = build_context(profile, track, plan, today)
        cv = render_docx(config.TEMPLATES_DIR / "cv.docx", ctx, out / f"{file_stem(profile, track, plan, 'cv')}.docx",
                         f"CV: {plan.role}, {plan.company}")
        cv_pages = _pdf_pages(cv, out, notes) if pdf else None
        if cv_pages is None or cv_pages <= config.CV_MAX_PAGES or trim == MAX_TRIMS or not _trim_one(profile, plan, notes):
            break
    files, pages = [cv], {"CV": cv_pages}
    if ctx["letter"]:
        letter = render_docx(config.TEMPLATES_DIR / "cover_letter.docx", ctx,
                             out / f"{file_stem(profile, track, plan, 'letter')}.docx", f"Cover letter: {plan.role}, {plan.company}")
        files.append(letter)
        pages["Cover letter"] = _pdf_pages(letter, out, notes) if pdf else None
    over = (pages["CV"] or 0) > config.CV_MAX_PAGES or (pages.get("Cover letter") or 0) > config.LETTER_MAX_PAGES
    if over:
        notes.append("over the page budget after trimming; shorten by hand")
    return Rendered(files, pages, notes, over)


def _pdf_pages(docx: Path, out: Path, notes: list[str]) -> int | None:
    try:
        return page_count(to_pdf(docx, out))
    except PdfUnavailable as e:
        if not any("PDF" in n for n in notes):
            notes.append(f"no PDF: {e}")
        return None


def _trim_one(profile: Profile, plan: TailorPlan, notes: list[str]) -> bool:
    """Drop the lowest-priority bullet from the oldest role that has more than one."""
    priority = {b.id: b.priority for r in profile.experience for b in r.bullets}
    for role in reversed(profile.experience):
        choices = plan.experience.get(role.id, [])
        if len(choices) > 1:
            victim = max(reversed(choices), key=lambda c: priority.get(c.bullet_id, 2))
            choices.remove(victim)
            notes.append(f"trimmed {victim.bullet_id} to fit {config.CV_MAX_PAGES} pages")
            return True
    return False


def primary_key(job: JobInput) -> str:
    if job.urls:
        return normalize_url(job.urls[0])
    return "manual:" + hashlib.sha1((job.jd_text or "").encode()).hexdigest()[:12]


def run_job(job: JobInput, *, settings: config.Settings, provider: LLMProvider, db: DB,
            http: httpx.Client | None = None, pdf: bool = True, today: date | None = None) -> dict:
    today = today or date.today()
    key = primary_key(job)
    if existing := db.find_by_url(key):
        if not job.force and existing["status"] not in RERUNNABLE:
            log.info("job %04d already exists for %s (status %s); add '# force' to redo it", existing["id"], key, existing["status"])
            return {**existing, "skipped": True}
        job_id = existing["id"]
        db.reset_for_rerun(job_id)
        if job.note:
            db.update(job_id, note=job.note)
    else:
        job_id = db.create_job(key, [normalize_url(u) for u in job.urls[1:]], job.note)

    run_log: list[str] = []

    def stage(status: str, msg: str = "") -> None:
        db.update(job_id, status=status)
        run_log.append(f"[{status}] {msg}".rstrip())
        log.info("job %04d: %s %s", job_id, status, msg)

    def finish(status: str, **fields) -> dict:
        db.update(job_id, status=status, **fields)
        run_log.append(f"[{status}] {fields.get('error') or ''}".rstrip())
        write_tracker(db, config.APPLICATIONS_DIR)
        return db.get(job_id)

    try:
        if settings.price(settings.model_tailor) and db.month_cost() >= settings.monthly_budget_usd:
            return finish("paused_budget", error=f"monthly budget ${settings.monthly_budget_usd:.2f} reached")

        profile = load_profile(config.PROFILE_PATH)
        tracks = load_tracks(config.TRACKS_PATH)
        own_http = http is None
        http = http or make_client()
        try:
            stage("scraping", job.urls[0] if job.urls else "pasted description")
            if job.jd_text:
                scraped = Scraped(job.urls[0] if job.urls else None, "manual", job.jd_text, job.jd_text, "md")
            else:
                scraped = scrape(job.urls[0], http)
            context_pages = [c for u in job.urls[1:] if (c := scrape_context(u, http))]
        finally:
            if own_http:
                http.close()
        company_context = "\n\n".join(c.text for c in context_pages) or None

        stage("extracting", f"via {scraped.source}")
        posting, usage, notes = extract_posting(provider, settings, scraped.text, scraped.url, company_context)
        usages = [("extract", usage, usage.cost_usd(settings.price(usage.model)))]
        db.record_usage(job_id, "extract", usage, usages[-1][2])

        folder = config.APPLICATIONS_DIR / f"{today.isoformat()}_{job_id:04d}_{slug(posting.company)}_{slug(posting.title)}"
        (folder / "source").mkdir(parents=True, exist_ok=True)
        (folder / "source" / f"raw.{scraped.raw_ext}").write_text(scraped.raw)
        (folder / "source" / "jd.md").write_text(scraped.text)
        if company_context:
            (folder / "source" / "company_context.md").write_text(company_context)
        (folder / "job.json").write_text(posting.model_dump_json(indent=2))
        db.update(job_id, company=posting.company, title=posting.title, location=posting.location,
                  remote=posting.remote, folder=folder.name)

        track_id, scores, reason = choose_track(posting, tracks, profile, job.track_hint)
        db.update(job_id, track=track_id)
        stage("tailoring", f"track {track_id} ({reason})")
        plan, tailor_usages, problems, attempts = tailor(provider, settings, profile, tracks, track_id, posting,
                                                         company_context, today)
        for u in tailor_usages:
            usages.append(("tailor", u, u.cost_usd(settings.price(u.model))))
            db.record_usage(job_id, "tailor", u, usages[-1][2])
        (folder / "plan.json").write_text(plan.model_dump_json(indent=2))

        stage("answering", f"{len(scraped.questions)} form question(s) + standard set")
        answer_set, answer_usages, answer_problems, missing = generate_answers(
            provider, settings, profile, posting, plan, scraped.questions, today)
        for u in answer_usages:
            usages.append(("answers", u, u.cost_usd(settings.price(u.model))))
            db.record_usage(job_id, "answers", u, usages[-1][2])
        if scraped.questions:
            (folder / "source" / "form_questions.json").write_text(
                json.dumps([q.model_dump() for q in scraped.questions], indent=2, ensure_ascii=False))
        (folder / "answers.json").write_text(answer_set.model_dump_json(indent=2))
        (folder / "answers.md").write_text(answers_markdown(
            posting, answer_set, scraped.questions, facts(profile, posting, today), answer_problems))
        notes.append(f"answers.md: {len(answer_set.answers)} answers"
                     + (f"; required questions need your input: {'; '.join(missing)}" if missing else ""))
        problems = problems + [f"answers: {p}" for p in answer_problems]

        stage("rendering")
        try:
            rendered = render_application(profile, tracks[track_id], plan, folder, pdf, today)
            notes += rendered.notes
            pages = rendered.pages
        except PlanError as e:
            problems = problems or e.problems
            pages, rendered = {}, None
            notes.append("not rendered: the plan references items the profile doesn't have")
        if problems:
            (folder / "VALIDATION_FAILED.md").write_text(
                "# Validation failed\n\nThe documents (if any) were rendered anyway. Review before sending.\n\n"
                + "\n".join(f"- {p}" for p in problems) + "\n")

        (folder / "match_report.md").write_text(match_report(
            job_id=job_id, urls=job.urls or ["pasted"], source=scraped.source, posting=posting, track_id=track_id,
            reason=reason, scores=scores, plan=plan, problems=problems, attempts=attempts, pages=pages,
            usages=usages, notes=notes))
        status = "needs_review" if problems or missing or rendered is None or rendered.over_budget else "ready"
        error = "; ".join(problems[:3]) or (f"answers need your input: {'; '.join(missing)}" if missing else None)
        return finish(status, error=error)
    except NeedsManualJD as e:
        return finish("needs_manual_jd", error=str(e))
    except (ScrapeError, LLMError) as e:
        return finish("failed", error=str(e))
    except Exception as e:
        log.exception("job %04d failed", job_id)
        return finish("failed", error=f"{type(e).__name__}: {e}")
    finally:
        job_row = db.get(job_id)
        if job_row and job_row["folder"]:
            path = config.APPLICATIONS_DIR / job_row["folder"]
            if path.exists():
                (path / "run.log").write_text("\n".join(run_log) + "\n")
