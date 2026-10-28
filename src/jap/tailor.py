import json
from datetime import date
from importlib.resources import files

from jap.config import Settings
from jap.llm import LLMProvider, Usage
from jap.models import BulletChoice, CoverLetter, JobPosting, Profile, TailorDraft, TailorPlan, Track
from jap.validate import validate

PROMPT_VERSION = "tailor-v1"
PROMPT = files("jap.prompts").joinpath("tailor.md").read_text()


def profile_context(profile: Profile, tracks: dict[str, Track]) -> str:
    """The cacheable part of the tailor prompt. Must be byte-identical across jobs: no dates, sorted keys."""
    track_data = {t.id: t.model_dump(exclude={"id", "label", "keywords"}) for t in tracks.values()}
    return (f"{PROMPT}\n<profile>\n{json.dumps(profile_data(profile), sort_keys=True, ensure_ascii=False)}\n</profile>\n"
            f"<tracks>\n{json.dumps(track_data, sort_keys=True, ensure_ascii=False)}\n</tracks>")


def profile_data(profile: Profile) -> dict:
    """Everything verified and CV-visible, as the LLM sees it."""
    roles = [
        {"id": r.id, "company": r.company, "title": r.title, "location": r.location, "start": r.start, "end": r.end,
         "bullets": [{"id": b.id, "text": b.text, "tags": b.tags, "skills": b.skills, "priority": b.priority}
                     for b in r.bullets if b.status == "verified"]}
        for r in profile.experience if r.show_on_cv
    ]
    return {
        "skills": profile.skills,
        "aliases": profile.aliases,
        "experience": roles,
        "projects": [{"id": p.id, "name": p.name, "text": p.text, "stack": p.stack, "hobby": p.hobby}
                     for p in profile.projects if p.status == "verified"],
        "education": [{"institution": e.institution, "degree": e.degree, "end": e.end}
                      for e in profile.education if e.status == "verified"],
        "never_claim": profile.never_claim,
    }


def draft_to_plan(draft: TailorDraft, posting: JobPosting, track_id: str, today: date) -> TailorPlan:
    experience: dict[str, list[BulletChoice]] = {}
    for role in draft.experience:
        experience.setdefault(role.role_id, []).extend(
            BulletChoice(bullet_id=b.bullet_id, rephrased=b.rephrased) for b in role.bullets)
    letter = draft.cover_letter
    return TailorPlan(
        company=posting.company, role=posting.title, track=track_id, headline=draft.headline,
        location=draft.location, summary=draft.summary, skill_groups=draft.skill_groups,
        experience=experience, projects=draft.projects,
        cover_letter=CoverLetter(greeting=letter.greeting, paragraphs=letter.paragraphs,
                                 sign_off=letter.sign_off, date=today),
        keyword_coverage=draft.keyword_coverage, gaps=draft.gaps, prompt_version=PROMPT_VERSION,
    )


def tailor(provider: LLMProvider, settings: Settings, profile: Profile, tracks: dict[str, Track], track_id: str,
           posting: JobPosting, company_context: str | None = None, today: date | None = None,
           ) -> tuple[TailorPlan, list[Usage], list[str], int]:
    """Returns (plan, usages, remaining validation problems, attempts). Retries once on problems."""
    today = today or date.today()
    system = profile_context(profile, tracks)
    base = (f"<target_track>{track_id}</target_track>\n"
            f"<job_posting>\n{posting.model_dump_json(indent=1)}\n</job_posting>")
    if company_context:
        base += f"\n<company_context>\n{company_context}\n</company_context>"

    usages: list[Usage] = []
    user = base
    for attempt in (1, 2):
        draft, usage = provider.complete(system=system, user=user, schema=TailorDraft, model=settings.model_tailor,
                                         effort=settings.effort_tailor, max_tokens=settings.max_tokens_tailor)
        usages.append(usage)
        plan = draft_to_plan(draft, posting, track_id, today)
        problems = validate(profile, tracks[track_id], plan, posting, today)
        if not problems:
            return plan, usages, [], attempt
        user = (f"{base}\n<previous_plan>\n{draft.model_dump_json()}\n</previous_plan>\n<validation_errors>\n"
                + "\n".join(f"- {p}" for p in problems) + "\n</validation_errors>")
    return plan, usages, problems, 2
