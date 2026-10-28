"""Turns a TailorPlan into template context, resolving every reference against the profile.

The plan only carries ids and short rephrasings. Companies, titles, dates and contact
details always come from the profile, so a plan cannot change them.
"""

import re
from datetime import date

from docxtpl import Listing

from jap.models import BulletChoice, Profile, TailorPlan, Track
from jap.profile import find_never_claim

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class PlanError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("plan does not match the profile:\n  " + "\n  ".join(problems))
        self.problems = problems


def fmt_month(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def years_since(ym: str, today: date) -> int:
    year, month = map(int, ym.split("-"))
    return max(0, ((today.year - year) * 12 + today.month - month) // 12)


def check_plan(profile: Profile, track: Track, plan: TailorPlan) -> None:
    if problems := plan_problems(profile, track, plan):
        raise PlanError(problems)


def plan_problems(profile: Profile, track: Track, plan: TailorPlan) -> list[str]:
    """Reference checks the renderer can't work without: ids, skills, headline, never_claim."""
    problems: list[str] = []
    allow = profile.allow_list

    if plan.headline not in track.headlines:
        problems.append(f"headline '{plan.headline}' is not one of track '{track.id}' headlines")
    for group in plan.skill_groups:
        problems += [f"skill '{s}' is not in the allow-list" for s in group.skills if s not in allow]
    for role_id, choices in plan.experience.items():
        role = profile.role(role_id)
        if role is None or not role.show_on_cv:
            problems.append(f"role '{role_id}' is unknown or hidden")
            continue
        verified = {b.id for b in role.bullets if b.status == "verified"}
        problems += [f"'{c.bullet_id}' is not a verified bullet of {role_id}" for c in choices if c.bullet_id not in verified]
    for pid in plan.projects:
        p = profile.project(pid)
        if p is None or p.status != "verified":
            problems.append(f"'{pid}' is not a verified project")

    texts = [plan.summary, *(c.rephrased or "" for cs in plan.experience.values() for c in cs)]
    if plan.cover_letter:
        texts += plan.cover_letter.paragraphs
    for text in texts:
        problems += [f"never_claim term '{t}' in: {text[:60]}…" for t in find_never_claim(text, profile.never_claim)]
    return problems


def contact_line(profile: Profile, plan: TailorPlan) -> Listing:
    """Email and links on one line; the relocation line (if any) on its own line below.

    EB Garamond sets wider than Times, so a single line overflows once a location is added.
    """
    line = "  •  ".join([profile.person.email, *profile.person.links])
    if plan.location:
        line += "\n" + profile.person.location_line_template.format(location=plan.location)
    return Listing(line)


def build_context(profile: Profile, track: Track, plan: TailorPlan, today: date | None = None) -> dict:
    check_plan(profile, track, plan)
    today = today or date.today()
    years = str(years_since(profile.person.career_start, today))

    experience = []
    for role in profile.experience:  # profile order = reverse-chronological
        if role.id not in plan.experience:
            continue
        by_id = {b.id: b for b in role.bullets}
        experience.append({
            "company": role.company,
            "title": role.title,
            "location": role.location or "",
            "dates": role.date_display or f"{fmt_month(role.start)} – {fmt_month(role.end) if role.end else 'Present'}",
            "bullets": [c.rephrased or by_id[c.bullet_id].text for c in plan.experience[role.id]],
        })

    projects = []
    for pid in plan.projects:
        p = profile.project(pid)
        projects.append({"name": p.name, "stack": ", ".join(p.stack), "text": p.text, "repo": p.repo})

    education = []
    if plan.include_education:
        education = [
            {"institution": e.institution, "degree": e.degree, "location": e.location or "", "dates": e.end.split("-")[0]}
            for e in profile.education if e.status == "verified"
        ]

    letter = None
    if plan.cover_letter:
        letter_date = plan.cover_letter.date or today
        letter = {
            "date": f"{letter_date.day} {letter_date.strftime('%B %Y')}",
            "greeting": plan.cover_letter.greeting,
            "paragraphs": [p.replace("{years}", years) for p in plan.cover_letter.paragraphs],
            "sign_off": plan.cover_letter.sign_off,
        }

    return {
        "person": profile.person.model_dump(),
        "headline": plan.headline,
        "contact_line": contact_line(profile, plan),
        "summary": plan.summary.replace("{years}", years),
        "skill_groups": [{"label": g.label, "skills": ", ".join(g.skills)} for g in plan.skill_groups],
        "experience": experience,
        "projects": projects,
        "education": education,
        "letter": letter,
    }


def plan_from_track(track: Track, company: str = "Base", role: str = "") -> TailorPlan:
    """The untailored CV a track describes: its first headline, defaults, no cover letter."""
    return TailorPlan(
        company=company,
        role=role,
        track=track.id,
        headline=track.headlines[0],
        summary=track.summary,
        skill_groups=track.skill_groups,
        experience={rid: [BulletChoice(bullet_id=b) for b in bids] for rid, bids in track.experience.items()},
        projects=track.projects,
    )


def file_stem(profile: Profile, track: Track, plan: TailorPlan, kind: str) -> str:
    company = re.sub(r"[^A-Za-z0-9]+", "", plan.company)
    if kind == "cv":
        return f"{profile.person.name} {track.label}_{company}_CV"
    return f"{profile.person.name} Cover_Letter_{company}"
