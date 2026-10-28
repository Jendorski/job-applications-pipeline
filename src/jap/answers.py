"""Answers to application-form questions: the posting's own form (when the ATS exposes it) plus a standard set.

Logistics answers come only from the candidate facts below; anything missing is marked for the
candidate to fill in rather than guessed. Motivation answers get the same validator as the letter.
"""

import json
import re
from datetime import date, timedelta
from importlib.resources import files

from jap.config import Settings
from jap.llm import LLMProvider, Usage
from jap.models import AnswerSet, FormQuestion, JobPosting, Profile, TailorPlan
from jap.tailor import profile_data
from jap.validate import allowed_numbers, prose_problems

PROMPT = files("jap.prompts").joinpath("answers.md").read_text()
MAX_ANSWER_WORDS = 320
STANDARD_QUESTIONS = [
    "Why do you want to work at {company}?",
    "Why are you interested in this role?",
    "What motivates you?",
    "What is the project you are proudest of, and why?",
    "Why are you looking for a new role?",
    "Is there anything else you would like us to know?",
]


def earliest_start(today: date) -> date:
    """First Monday of next month: the candidate's "first week of next month"."""
    first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7)


def facts(profile: Profile, posting: JobPosting, today: date) -> list[str]:
    c = profile.candidate
    if c is None:
        return []
    start = earliest_start(today)
    salary = (f"{c.salary.get('rule', '')} The posted range is: {posting.salary}." if posting.salary
              else f"{c.salary.get('fallback', '')} (no range is posted).")
    links = [f"https://{l}" if not l.startswith("http") else l for l in profile.person.links]
    labelled = [f"Website/Portfolio: {c.website}"] if c.website else []
    labelled += [f"LinkedIn: {c.linkedin}"] if c.linkedin else [f"LinkedIn: {l}" for l in links if "linkedin.com" in l]
    labelled += [f"GitHub: {l}" for l in links if "github.com" in l]
    labelled += [f"Other link: {l}" for l in links if "linkedin.com" not in l and "github.com" not in l]
    return [
        f"Preferred name: {c.preferred_name}",
        f"Based in: {c.based_in}",
        f"Open to relocating to: {', '.join(c.relocation.get('open_to', []))}",
        f"Work authorisation: {c.work_authorisation}",
        f"Earliest start: the first week of {start:%B %Y} (from Monday {start.day} {start:%B %Y})",
        f"Salary expectation: {salary}",
        *labelled,
        f"Email: {profile.person.email}",
    ]


def answers_context(profile: Profile) -> str:
    """Cacheable prefix: identical across jobs."""
    c = profile.candidate
    candidate = {
        "motivation": c.motivation.get("text", "") if c else "",
        "why_looking": c.why_looking if c else "",
        "proudest_project": c.proudest_project if c else {},
    }
    return (f"{PROMPT}\n<profile>\n{json.dumps(profile_data(profile), sort_keys=True, ensure_ascii=False)}\n</profile>\n"
            f"<candidate>\n{json.dumps(candidate, sort_keys=True, ensure_ascii=False)}\n</candidate>")


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()[:80]


def validate_answers(profile: Profile, posting: JobPosting, answer_set: AnswerSet, questions: list[FormQuestion],
                     today: date, fact_lines: list[str]) -> list[str]:
    problems = []
    by_question = {_key(a.question): a for a in answer_set.answers if a.source == "form"}
    for q in questions:
        a = by_question.get(_key(q.label))
        if a is None:
            problems.append(f"form question not answered: {q.label}")
        elif q.options and a.kind != "needs_input" and a.answer.strip() not in q.options:
            problems.append(f"'{q.label}': answer must be exactly one of {q.options}")
    numbers = allowed_numbers(profile, posting, today, "\n".join(fact_lines))
    prose = [(f"answer '{a.question[:50]}'", a.answer) for a in answer_set.answers if a.kind != "needs_input"]
    problems += prose_problems(profile, prose, numbers)
    for a in answer_set.answers:
        if a.kind == "motivation" and (n := len(a.answer.split())) > MAX_ANSWER_WORDS:
            problems.append(f"answer '{a.question[:50]}': {n} words (max {MAX_ANSWER_WORDS})")
    return problems


def generate_answers(provider: LLMProvider, settings: Settings, profile: Profile, posting: JobPosting,
                     plan: TailorPlan, questions: list[FormQuestion], today: date,
                     ) -> tuple[AnswerSet, list[Usage], list[str], list[str]]:
    """Returns (answers, usages, validation problems, required questions that need the candidate's input)."""
    fact_lines = facts(profile, posting, today)
    form = [{"question": q.label, "required": q.required, **({"options": q.options} if q.options else {})} for q in questions]
    base = (f"<job_posting>\n{posting.model_dump_json(indent=1)}\n</job_posting>\n"
            f"<cv_summary>\n{plan.summary}\n</cv_summary>\n"
            f"<facts>\n" + "\n".join(fact_lines) + "\n</facts>\n"
            f"<form_questions>\n{json.dumps(form, ensure_ascii=False, indent=1)}\n</form_questions>\n"
            f"<standard_questions>\n" + "\n".join(q.format(company=posting.company) for q in STANDARD_QUESTIONS)
            + "\n</standard_questions>")
    system = answers_context(profile)

    usages: list[Usage] = []
    user = base
    for _attempt in (1, 2):
        answer_set, usage = provider.complete(system=system, user=user, schema=AnswerSet, model=settings.model_tailor,
                                              effort=settings.effort_tailor, max_tokens=settings.max_tokens_tailor)
        usages.append(usage)
        problems = validate_answers(profile, posting, answer_set, questions, today, fact_lines)
        if not problems:
            break
        user = (f"{base}\n<previous_answers>\n{answer_set.model_dump_json()}\n</previous_answers>\n<validation_errors>\n"
                + "\n".join(f"- {p}" for p in problems) + "\n</validation_errors>")

    required = {_key(q.label) for q in questions if q.required}
    missing = [a.question for a in answer_set.answers
               if a.kind == "needs_input" and a.source == "form" and _key(a.question) in required]
    return answer_set, usages, problems, missing


def answers_markdown(posting: JobPosting, answer_set: AnswerSet, questions: list[FormQuestion],
                     fact_lines: list[str], problems: list[str]) -> str:
    required = {_key(q.label) for q in questions if q.required}
    out = [f"# Application answers: {posting.title} at {posting.company}", "",
           "Review before pasting. Facts come from your profile; motivation answers are drafted for this job.", ""]
    if problems:
        out += ["> **Validation failed** for some answers; fix these before using them:", ""]
        out += [f"> - {p}" for p in problems] + [""]
    todo = [a for a in answer_set.answers if a.kind == "needs_input"]
    if todo:
        out += ["## Needs your input", ""] + [f"- **{a.question}**: {a.answer}" for a in todo] + [""]

    for title, source in (("From the application form", "form"), ("Standard questions", "standard")):
        group = [a for a in answer_set.answers if a.source == source and a.kind != "needs_input"]
        if not group:
            continue
        out += [f"## {title}", ""]
        for a in group:
            flag = " *(required)*" if source == "form" and _key(a.question) in required else ""
            out += [f"### {a.question}{flag}", "", a.answer, "", f"<sub>{a.kind} · {len(a.answer.split())} words</sub>", ""]

    out += ["## Facts used", ""] + [f"- {line}" for line in fact_lines] + [""]
    return "\n".join(out)
